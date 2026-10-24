"""Fixed-baseline, offline contracts for the synthetic BM25 upgrade rehearsal.

This module does not migrate a deployment or authenticate arbitrary source code.
The SQLite probe executes the reviewed Store classes from two clean checkouts,
using only temporary synthetic data and no provider or model entrypoints.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import types

BASELINE_SHA = "e2a5310ec8a33f3078f796e8b7f2230c82fa2059"
BACKUP_CONTRACT = (
    "tools/shop_demo/state.py", "tools/shop_e2e/stack.py",
    "tools/shop_e2e/hybrid_stack.py", "tools/shop_demo/backup.py",
    "tools/shop_demo/backup_volume.py",
)
MYSQL_PATHS = ("deploy/mysql/init", "deploy/mysql/migrations")
APPLICATION_PATHS = (
    "services/agent/shop_agent_stack", "services/commerce", "apps/web/src",
)
STORE_PATH = "services/agent/shop_agent_stack/store.py"
ADDITIVE_OBJECTS = {
    "provider_test_attempts": "table", "idx_probe_member_window": "index",
    "idx_probe_active": "index", "idx_events_kind": "index",
    "idx_runs_active": "index",
}
SHA = re.compile(r"[0-9a-f]{40}\Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(["git", "-C", str(root), *args], env=env,
                            capture_output=True, timeout=30, check=False)
    _require(result.returncode == 0, "upgrade checkout Git inspection failed")
    return result.stdout


def _identity(root: Path) -> tuple[Path, dict]:
    root = root.absolute()
    _require(root.is_dir() and not root.is_symlink(), "upgrade checkout must be a real directory")
    root = root.resolve()
    top = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    _require(top == root, "select the actual upgrade checkout root")
    _require(not _git(root, "status", "--porcelain", "--untracked-files=all").strip(),
             "upgrade checkouts must be clean")
    sha, tree = _git(root, "rev-parse", "HEAD", "HEAD^{tree}").decode().split()
    _require(bool(SHA.fullmatch(sha) and SHA.fullmatch(tree)), "upgrade identities must be full commit/tree SHA")
    return root, {"sha": sha, "tree_sha": tree}


def _inventory(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for record in _git(root, "ls-tree", "-rz", "HEAD", "--", *paths).split(b"\0"):
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        name = raw_name.decode()
        _require(mode in ("100644", "100755") and kind == "blob",
                 "upgrade inputs must be regular tracked files")
        path = root / name
        _require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root),
                 "upgrade input path is missing or unsafe")
        for parent in path.parents:
            if parent == root:
                break
            _require(not parent.is_symlink(), "upgrade input directory is a symlink")
        content = path.read_bytes()
        actual_blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        _require(actual_blob == blob, "upgrade input bytes differ from the reviewed Git HEAD")
        result[name] = blob
    return result


def _hashes(root: Path, paths) -> dict[str, str]:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(paths)}


def _digest(records: dict) -> str:
    # Match the established backup.contract() path-to-digest representation.
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def _application_file(name: str) -> bool:
    if name.startswith("services/agent/shop_agent_stack/"):
        return name.endswith(".py")
    if name.startswith("services/commerce/"):
        return "/src/main/" in name and Path(name).suffix in {".java", ".xml", ".yml", ".yaml", ".properties"}
    return name.startswith("apps/web/src/") and Path(name).suffix in {".ts", ".tsx", ".js", ".jsx", ".css"}


def inspect_pair(baseline_root: Path, candidate_root: Path) -> dict:
    """Refuse unknown SQL/layout changes before touching any rehearsal data."""
    old, baseline = _identity(Path(baseline_root))
    new, candidate = _identity(Path(candidate_root))
    _require(baseline["sha"] == BASELINE_SHA, "unsupported upgrade baseline SHA")
    _require(old != new and baseline["sha"] != candidate["sha"], "upgrade requires two different source versions")
    _git(new, "merge-base", "--is-ancestor", BASELINE_SHA, candidate["sha"])

    sql = []
    contracts = []
    applications = []
    for root in (old, new):
        inventory = _inventory(root, MYSQL_PATHS)
        _require(inventory and all(name.endswith(".sql") for name in inventory),
                 "unsupported MySQL initialization inventory")
        _require(all(any(name.startswith(prefix + "/") for name in inventory) for prefix in MYSQL_PATHS),
                 "MySQL initialization or migration files missing")
        sql.append(_hashes(root, inventory))
        contract = _inventory(root, BACKUP_CONTRACT)
        _require(set(contract) == set(BACKUP_CONTRACT), "cold backup contract files missing")
        contracts.append(_hashes(root, contract))
        applications.append({name: blob for name, blob in _inventory(root, APPLICATION_PATHS).items()
                             if _application_file(name)})
    _require(sql[0] == sql[1], "MySQL SQL changes require a separate reviewed migration")
    _require(contracts[0] == contracts[1], "cold backup layout contract changed")
    changed = sorted(name for name in applications[0].keys() | applications[1].keys()
                     if applications[0].get(name) != applications[1].get(name))
    _require(bool(changed), "documentation-only changes are not an application upgrade")
    return {
        "schema": "shop-demo-upgrade-pair/v1", "baseline": baseline, "candidate": candidate,
        "mysql_schema_digest": _digest(sql[0]), "mysql_schema_file_count": len(sql[0]),
        "backup_contract_digest": _digest(contracts[0]),
        "application_changes": changed, "application_change_count": len(changed),
        "mysql_schema_unchanged": True, "backup_contract_unchanged": True,
        "application_changed": True, "same_version": False,
        "mysql_ddl_migration_verified": False, "upgrade_execution_verified": False,
        "production_deployment_approved": False, "paid_model_called": False,
    }


def _load_store(root: Path):
    source = (root / STORE_PATH).read_bytes()
    tree = ast.parse(source, filename=STORE_PATH)
    permitted = {"json", "os", "sqlite3", "time", "contextlib", "uuid"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            _require(all(item.name in permitted for item in node.names), "unsupported Store top-level import")
        elif isinstance(node, ast.ImportFrom):
            _require(node.level == 0 and node.module in permitted, "unsupported Store top-level import")
    module = types.ModuleType("synthetic_upgrade_store")
    exec(compile(tree, STORE_PATH, "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def _schema(path: Path) -> dict:
    with sqlite3.connect(path) as db:
        return {name: {"type": kind, "sql": sql} for name, kind, sql in db.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")}


def _additive_schema(before: dict, after: dict) -> dict:
    _require(all(after.get(name) == value for name, value in before.items()),
             "SQLite existing schema changed during upgrade")
    added = {name: value["type"] for name, value in after.items() if name not in before}
    _require(all(ADDITIVE_OBJECTS.get(name) == kind for name, kind in added.items()),
             "unsupported additive SQLite object")
    return added


def _deny_other_owner(store, module, rid: str) -> None:
    try:
        store.run(rid, 102)
    except module.StoreError as exc:
        _require(exc.code == 404, "SQLite owner denial returned an unexpected status")
    else:
        raise ValueError("SQLite probe accepted another owner")


def _private_dir(root: Path, selected: Path) -> Path:
    selected = selected.absolute()
    resolved = selected.resolve()
    _require(selected.is_relative_to(root / ".local") and
             resolved.is_relative_to(root / ".local") and resolved != root / ".local",
             "SQLite probe requires a dedicated ignored private directory")
    for part in (selected, *selected.parents):
        _require(not part.is_symlink(), "SQLite probe private path contains a symlink")
        if part == root:
            break
    relative = selected.relative_to(root)
    _git(root, "check-ignore", "--quiet", "--", str(relative))
    selected.mkdir(parents=True, mode=0o700, exist_ok=True)
    _require(selected.is_dir() and stat.S_IMODE(selected.stat().st_mode) & 0o077 == 0,
             "SQLite probe directory must have mode 0700")
    _require(not (selected / "sqlite-compatibility.json").exists(), "preserve the previous SQLite probe result")
    return selected


def run_sqlite_probe(baseline_root: Path, candidate_root: Path, private_dir: Path) -> dict:
    """Exercise old -> new -> old -> new Store code; no live-service claim."""
    pair = inspect_pair(baseline_root, candidate_root)
    baseline_root, candidate_root = Path(baseline_root).resolve(), Path(candidate_root).resolve()
    private_dir = _private_dir(candidate_root, Path(private_dir))
    report = {
        "schema": "shop-demo-sqlite-compatibility/v1", "passed": False,
        "baseline": pair["baseline"], "candidate": pair["candidate"], "checks": [],
        "real_service_stack_verified": False, "mysql_runtime_verified": False,
        "paid_model_called": False, "production_deployment_approved": False,
    }
    stage = "load-reviewed-stores"
    try:
        old_module, old_hash = _load_store(baseline_root)
        new_module, new_hash = _load_store(candidate_root)
        report["store_sha256"] = {"baseline": old_hash, "candidate": new_hash}
        with tempfile.TemporaryDirectory(prefix=".sqlite-probe-", dir=private_dir) as temporary:
            path = Path(temporary) / "agent.sqlite"
            stage = "baseline-write"
            old = old_module.Store(str(path)); path.chmod(0o600)
            session = old.new_session(101)
            rid, created = old.create_run(session["id"], 101, "baseline-request", "Synthetic old question", "custom")
            _require(created, "baseline probe run was not new")
            old.emit(rid, "assistant", {"text": "Synthetic old answer"})
            old.state(rid, "COMPLETED")
            before = old.session(session["id"], 101)
            _deny_other_owner(old, old_module, rid)
            report["checks"].append("baseline-owner-isolation")
            schema_before = _schema(path)

            stage = "candidate-read-and-schema"
            new = new_module.Store(str(path))
            _require(new.session(session["id"], 101) == before, "candidate changed baseline data")
            _deny_other_owner(new, new_module, rid)
            report["checks"] += ["candidate-reads-baseline-data", "candidate-owner-isolation"]
            schema_after = _schema(path)
            added = _additive_schema(schema_before, schema_after)
            report["added_tables"] = sorted(name for name, kind in added.items() if kind == "table")
            report["added_indexes"] = sorted(name for name, kind in added.items() if kind == "index")
            report["existing_schema_unchanged"] = True
            report["checks"].append("sqlite-additive-schema-only")
            _require(new.create_run(session["id"], 101, "baseline-request", "Synthetic old question", "custom") == (rid, False),
                     "candidate lost baseline request idempotency")
            report["checks"].append("candidate-preserves-request-idempotency")

            stage = "candidate-write"
            ticket = new.begin_provider_test(101)  # SQLite lease only; no provider/network invocation.
            newer = new.new_session(101)
            new_rid, created = new.create_run(newer["id"], 101, "candidate-request", "Synthetic new question", "custom")
            _require(created, "candidate probe run was not new")
            new.emit(new_rid, "usage", {"usage_known": False, "reported_tokens": None, "stage": "synthetic-no-model-call"})
            new.emit(new_rid, "assistant", {"text": "Synthetic new answer"})
            new.state(new_rid, "COMPLETED")
            candidate_data = new.session(newer["id"], 101)

            stage = "baseline-rollback-read-and-write"
            rollback = old_module.Store(str(path))
            _require(rollback.session(session["id"], 101) == before and
                     rollback.session(newer["id"], 101) == candidate_data,
                     "rollback lost baseline or candidate data")
            _deny_other_owner(rollback, old_module, new_rid)
            _require(_schema(path) == schema_after, "rollback dropped additive SQLite objects")
            _require(rollback.create_run(newer["id"], 101, "candidate-request", "Synthetic new question", "custom") == (new_rid, False),
                     "rollback lost candidate request idempotency")
            report["checks"] += ["rollback-reads-both-generations", "rollback-owner-isolation",
                                 "rollback-retains-additive-schema", "rollback-preserves-request-idempotency"]
            latest = rollback.new_session(101)
            latest_rid, created = rollback.create_run(latest["id"], 101, "rollback-request", "Synthetic rollback question", "custom")
            _require(created, "rollback probe run was not new")
            rollback.emit(latest_rid, "assistant", {"text": "Synthetic rollback answer"})
            rollback.state(latest_rid, "COMPLETED")
            report["checks"].append("rollback-can-write")

            stage = "candidate-reopen"
            reopened = new_module.Store(str(path))
            _require(reopened.run(latest_rid, 101)["status"] == "COMPLETED" and
                     reopened.session(newer["id"], 101) == candidate_data,
                     "candidate reopen lost rollback writes")
            with sqlite3.connect(path) as db:
                _require(db.execute("SELECT active FROM provider_test_attempts WHERE id=?", (ticket,)).fetchone() == (0,),
                         "candidate restart did not release the preserved probe lease")
                _require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                         "SQLite integrity check failed")
            report["checks"] += ["candidate-reads-rollback-writes", "probe-attempt-retained-lease-released",
                                 "sqlite-integrity-check"]
        report["temporary_databases_removed"] = True
        report["check_count"] = len(report["checks"])
        report["passed"] = True
        return report
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        report["failure_stage"] = stage
        raise ValueError("SQLite compatibility probe failed at " + stage) from None
    finally:
        destination = private_dir / "sqlite-compatibility.json"
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
