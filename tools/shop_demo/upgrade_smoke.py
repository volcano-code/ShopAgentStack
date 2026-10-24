"""Fixed-version BM25 upgrade and checkpoint rollback using NEW synthetic projects.

No source deployment, URL, key or database can be supplied by the caller. The old
checkout creates its own source. Only allowlisted evidence leaves private state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

from . import backup, backup_crypto, runtime, state as ds
from .backup_smoke import port
from .seed_smoke import readback
from .smoke import agent, business, expect, failure_locations, request
from .upgrade_contract import inspect_pair, run_sqlite_probe
from .upgrade_restore import restore_candidate

ROOT = ds.ROOT
CHECKS = {
    "distinct-clean-versions", "unchanged-mysql-and-backup-contract", "sqlite-roundtrip",
    "baseline-live-writes", "authenticated-checkpoint", "new-projects-and-volumes",
    "candidate-runtime-loaded", "candidate-reads-baseline", "sqlite-additive-schema",
    "candidate-live-writes", "candidate-restart-retains-writes", "rollback-runtime-loaded",
    "checkpoint-data-restored", "candidate-writes-absent-after-checkpoint-rollback",
    "rollback-restart", "source-restart-unchanged", "source-files-unchanged",
}
SCOPE = "M2.8-fixed-pair-bm25-upgrade-and-checkpoint-rollback"

# Imported by a fresh Python process with cwd at the pinned old checkout. This
# prevents new lifecycle code from silently managing an old checkout's owner.
BASELINE_DRIVER = r'''
import json, sys
from pathlib import Path
from tools.shop_demo import backup, runtime, seed, state as ds
from tools.shop_demo.smoke import failure_locations
op, state = sys.argv[1], Path(sys.argv[2])
try:
    if op == 'init':
        result = ds.init(ds.ROOT, state, {'retrieval':'bm25','port':int(sys.argv[3]),
            'fixture':False,'model_network':False,'seed_import':True})
        result = {'project':result['project']}
    elif op == 'seed':
        for publish in (False, True):
            plan = seed.preview(state, publish=publish)
            seed.apply(state, plan['preview_id'], plan['confirmation'])
        result = {'seeded':True}
    elif op == 'backup':
        result = backup.backup(state, Path(sys.argv[3]), Path(sys.argv[4]))
    elif op == 'destroy':
        owner, _ = ds.load(ds.ROOT, state)
        if owner['project'] != sys.argv[3]:
            raise ValueError('project confirmation differs')
        result = runtime.destroy(state, owner['project'])
    elif op in ('up', 'stop'):
        result = getattr(runtime, op)(state)
    else:
        raise ValueError('unsupported baseline operation')
    print('UPGRADE_RESULT=' + json.dumps(result))
except Exception as exc:
    print('UPGRADE_RESULT=' + json.dumps({'failed':True,'failure_type':type(exc).__name__,
                                        'failure_locations':failure_locations(exc)}))
    raise SystemExit(1)
'''

SQLITE_INSPECT = r'''
import json, sqlite3
with sqlite3.connect('file:/data/agent.sqlite?mode=ro', uri=True) as db:
    objects = {kind+':'+name: sql for kind,name,sql in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY type,name")}
    integrity = db.execute('PRAGMA integrity_check').fetchall()
print(json.dumps({'objects':objects,'integrity_ok':integrity == [('ok',)]}))
'''

AGENT_CODE = r'''
import hashlib,json
from pathlib import Path
p=Path('/app/shop_agent_stack')
print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest()
                  for f in sorted(p.rglob('*.py'))}))
'''


class BaselineFailure(runtime.DemoError):
    def __init__(self, details):
        super().__init__("baseline operation failed; private log retained")
        self.details = details


def baseline_call(baseline: Path, base: Path, action: str, state: Path, *args) -> dict:
    print("shop-upgrade: baseline-" + action, flush=True)
    result = subprocess.run([sys.executable, "-c", BASELINE_DRIVER, action, str(state),
                             *(str(x) for x in args)], cwd=baseline,
                            env=runtime.environment(), text=True, capture_output=True, timeout=1500)
    ds.write_private(base / "logs" / (action + "-" + uuid4().hex + ".log"), result.stdout + result.stderr)
    values = [json.loads(line[len("UPGRADE_RESULT="):]) for line in result.stdout.splitlines()
              if line.startswith("UPGRADE_RESULT=")]
    if result.returncode or len(values) != 1 or values[0].get("failed"):
        details = values[-1] if values and values[-1].get("failed") else {"failure_type": "SubprocessError"}
        raise BaselineFailure(details)
    return values[0]


def digest_map(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def live_identity(state: Path, project: str, source_root: Path) -> dict:
    """Verify files, real bind mounts and Python code inside both running images."""
    manifest = json.loads((state / "manifest.json").read_text())["files"]
    actual = {name[len("runtime/"):]: ds.sha(state / name)
              for name in manifest if name.startswith("runtime/")}
    expected = {str(path.relative_to(source_root)): ds.sha(path) for path in ds.inputs(source_root)}
    expect(actual == expected, "runtime snapshot differs from its built checkout")
    spec = json.loads((state / "compose.json").read_text())
    current = runtime.resources(state, project)
    containers = {item["Config"]["Labels"]["com.docker.compose.service"]: item for item in current["container"]}
    expect(set(containers) == set(spec["services"]), "runtime container inventory differs")
    for name, service in spec["services"].items():
        item = containers[name]
        expect(item["State"].get("Running") is True, "runtime service is not running")
        if service["image"].startswith("sha256:"):
            expect(item["Image"] == service["image"], "runtime image differs from its pinned identity")
        mounts = {v["Destination"]: v for v in item["Mounts"]}
        for volume in service.get("volumes", []):
            if isinstance(volume, dict):
                mount = mounts.get(volume["target"], {})
                expect(mount.get("Type") == "bind" and mount.get("Source") == volume["source"]
                       and mount.get("RW") is False, "application bind mount identity differs")
            else:
                logical, destination = volume.split(":", 1)
                mount = mounts.get(destination, {})
                expect(mount.get("Type") == "volume" and mount.get("Name") == project + "_" + logical,
                       "runtime attached a different project's data volume")
    python_root = state / "runtime/services/agent/shop_agent_stack"
    expected_code = {str(p.relative_to(python_root)): ds.sha(p) for p in sorted(python_root.rglob("*.py"))}
    for name in ("agent", "commerce-mcp"):
        found = json.loads(runtime.command(state, "verify-loaded-" + name,
                    [*runtime.base(state, project), "exec", "-T", name, "python", "-c", AGENT_CODE]))
        expect(found == expected_code, "loaded Python code differs from the snapshot")
    return {"snapshot_sha256": ds.sha(state / "manifest.json"),
            "portal_jar_sha256": actual["services/commerce/mall-portal/target/mall-portal-1.0-SNAPSHOT.jar"],
            "admin_jar_sha256": actual["services/commerce/mall-admin/target/mall-admin-1.0-SNAPSHOT.jar"],
            "web_dist_sha256": digest_map({k: v for k, v in actual.items() if k.startswith("apps/web/dist/")}),
            "agent_source_sha256": digest_map(expected_code),
            "images": {name: item["Image"] for name, item in sorted(containers.items())},
            "bind_mounts_verified": True, "loaded_python_verified": True}


def sqlite_state(state: Path, project: str) -> dict:
    result = json.loads(runtime.command(state, "inspect-sqlite-schema",
            [*runtime.base(state, project), "exec", "-T", "agent", "python", "-c", SQLITE_INSPECT]))
    expect(result.get("integrity_ok") is True, "live SQLite integrity check failed")
    return result


def register(url: str) -> dict:
    user = {"username": "upgrade_" + uuid4().hex[:12], "password": uuid4().hex + "Aa9!"}
    telephone = "000" + str(secrets.randbelow(10**8)).zfill(8)
    otp = business(url, "/api/portal/sso/getAuthCode?telephone=" + telephone)
    business(url, "/api/portal/sso/register", data={**user, "telephone": telephone, "authCode": otp},
             form=True, allow_empty=True)
    return user


def login(url: str, user: dict) -> str:
    result = business(url, "/api/portal/sso/login", data=user, form=True)
    return result["tokenHead"] + result["token"]


def adjust_stock(url: str, state: Path, expected: int) -> None:
    account = next(x for x in json.loads((state / "accounts.json").read_text()) if x["role"] == "ADMIN")
    auth = business(url, "/api/admin/admin/login", data={k: account[k] for k in ("username", "password")})
    business(url, "/api/admin/shop_agent_stack/catalog/10001/changes", allow_empty=True,
             bearer=auth["tokenHead"] + auth["token"], data={"requestId": str(uuid4()), "action": "STOCK",
             "skuId": 10001, "expected": expected, "value": -1, "reason": "synthetic version rehearsal"})


def check_data(url: str, state: Path, project: str, saved: dict, database: dict,
               nickname: str, extra_session: str | None = None) -> str:
    for account in saved["accounts"]:
        business(url, "/api/admin/admin/login", data={k: account[k] for k in ("username", "password")})
    token = login(url, saved["user"])
    other = login(url, saved["other"])
    expect(readback(state, project) == database, "independent MySQL readback differs")
    expect(business(url, "/api/portal/cart/list", bearer=token) == saved["cart"], "cart was not retained")
    expect(agent(url, "/sessions/" + saved["session"], token)["id"] == saved["session"], "old session missing")
    status, _ = request(url, "/api/agent/sessions/" + saved["session"], bearer=other)
    expect(status == 404, "other owner could read the old session")
    expect(agent(url, "/settings", token)["nickname"] == nickname, "preference checkpoint differs")
    config = agent(url, "/settings/providers/custom", token)
    expect(config.get("has_key") is True and "api_key" not in config and "key" not in config,
           "encrypted configuration could not be read safely")
    if extra_session:
        expect(agent(url, "/sessions/" + extra_session, token)["id"] == extra_session, "new session missing")
    return token


def record_state(base: Path, name: str, project: str | None) -> None:
    path = base / "ownership.json"
    value = json.loads(path.read_text())
    value["states"][name] = project
    ds._atomic(path, value)


def cleanup(baseline: Path, base: Path) -> list[dict]:
    """Only reserved states from this rehearsal; never discover arbitrary demos."""
    path = base / "ownership.json"
    if not path.is_file():
        return []
    value = json.loads(path.read_text())
    if (value.get("schema") != "shop-demo-upgrade-owned/v1" or value.get("baseline") != str(baseline)
            or not isinstance(value.get("states"), dict) or set(value["states"]) - {"source", "upgrade", "rollback"}):
        raise ValueError("invalid upgrade ownership record")
    errors = []
    for name in ("rollback", "upgrade", "source"):
        if name not in value["states"]:
            continue
        state = baseline / ".local/upgrade-source" if name == "source" else base / name
        try:
            ds.no_symlinks(state, baseline if name == "source" else ROOT)
            if not (state / "manifest.json").is_file():
                continue  # No containers are created before the manifest exists.
            owner = json.loads((state / "owner.json").read_text())
            expected_root = baseline if name == "source" else ROOT
            expect(owner.get("root") == str(expected_root.resolve()) and ds.PROJECT.fullmatch(owner.get("project", "")),
                   "cleanup owner is outside the rehearsal")
            recorded = value["states"][name]
            expect(recorded is None or recorded == owner["project"], "cleanup project differs from its receipt")
            if runtime._life(state)["phase"] == "destroyed":
                continue
            if name == "source":
                baseline_call(baseline, base, "destroy", state, owner["project"])
            else:
                runtime.destroy(state, owner["project"])
        except Exception as exc:
            errors.append({"state": name, "failure_type": type(exc).__name__})
    return errors


def verify(report: dict) -> None:
    expect(report.get("scope") == SCOPE, "unexpected upgrade evidence scope")
    expect(set(report.get("checks", [])) == CHECKS and len(report["checks"]) == len(CHECKS),
           "missing or duplicate upgrade checks")
    expect(report["source_pair"]["baseline"]["sha"] != report["source_pair"]["candidate"]["sha"],
           "same version cannot prove an upgrade")
    expect(all(report["source_pair"].get(key) is True for key in
               ("mysql_schema_unchanged", "backup_contract_unchanged", "application_changed")),
           "version compatibility admission was not verified")
    expect(report.get("isolated_project_count") == 3 and report.get("isolated_volume_count") == 15,
           "upgrade projects and data volumes were not isolated")
    expect(report["sqlite_probe"]["passed"] is True, "SQLite compatibility probe did not pass")
    before, after, rollback = (report["database"][key] for key in ("baseline", "candidate", "rollback"))
    expect(before == rollback and before["stock"] == 179 and after == {**before, "stock": 178},
           "checkpoint stock evidence differs")
    expect(report["live_llm_called"] is False and report["public_deployment"] is False
           and report["full_stack_lossless_downgrade_verified"] is False
           and report["mysql_ddl_migration_verified"] is False
           and report["hybrid_recovery_verified"] is False
           and report["post_checkpoint_writes_retained_by_rollback"] is False,
           "upgrade scope was overstated")


def run(baseline: Path) -> int:
    base = ROOT / ".local/upgrade-smoke"
    source = baseline / ".local/upgrade-source"
    vault = baseline / ".local/upgrade-vault"
    if source.exists() or vault.exists():
        raise FileExistsError("baseline rehearsal state already exists; never reuse old evidence")
    base.mkdir(parents=True, mode=0o700, exist_ok=False)
    (base / "logs").mkdir(mode=0o700)
    (base / "artifacts").mkdir(mode=0o700)
    ds.write_private(base / "ownership.json", json.dumps({"schema": "shop-demo-upgrade-owned/v1",
                                                       "baseline": str(baseline), "states": {}}))
    report = {"scope": SCOPE, "status": "failed", "checks": [], "cleanup_verified": False,
              "upgrade_verified": False, "checkpoint_rollback_verified": False,
              "live_llm_called": False, "public_deployment": False,
              "hybrid_recovery_verified": False, "mysql_ddl_migration_verified": False,
              "full_stack_lossless_downgrade_verified": False,
              "post_checkpoint_writes_retained_by_rollback": False}
    stage = "pair-preflight"
    code = 1
    def mark(name):
        nonlocal stage
        stage = name
        print("shop-upgrade: " + name, flush=True)
    try:
        report["source_pair"] = inspect_pair(baseline, ROOT)
        report["checks"] += ["distinct-clean-versions", "unchanged-mysql-and-backup-contract"]
        mark("sqlite-compatibility")
        report["sqlite_probe"] = run_sqlite_probe(baseline, ROOT, base / "sqlite-probe")
        report["checks"].append("sqlite-roundtrip")
        mark("baseline-startup")
        vault.mkdir(parents=True, mode=0o700, exist_ok=False)
        key, archive = vault / "checkpoint.key", vault / "checkpoint.enc"
        backup_crypto.keygen(key)
        record_state(base, "source", None)
        owner = baseline_call(baseline, base, "init", source, port())
        project = owner["project"]
        record_state(base, "source", project)
        url = baseline_call(baseline, base, "up", source)["url"]
        baseline_call(baseline, base, "seed", source)
        adjust_stock(url, source, 180)
        user, other = register(url), register(url)
        token = login(url, user)
        business(url, "/api/portal/cart/add", bearer=token,
                 data={"productId": 10001, "productSkuId": 10001, "quantity": 1,
                       "price": 0.01, "productCategoryId": 1011})
        session = agent(url, "/sessions", token, data={})["id"]
        agent(url, "/settings", token, data={"nickname": "M2.8 baseline checkpoint", "compact": True})
        agent(url, "/settings/providers/custom", token, data={"model": "not-live",
              "base_url": "https://model.example.invalid", "api_key": "synthetic-" + uuid4().hex})
        saved = {"user": user, "other": other, "session": session,
                 "accounts": json.loads((source / "accounts.json").read_text()),
                 "cart": business(url, "/api/portal/cart/list", bearer=token)}
        before_db = readback(source, project)
        expect(before_db["products"] == before_db["skus"] == 100 and before_db["published"] == 96
               and before_db["drafts"] == 0 and before_db["stock"] == 179, "baseline import incomplete")
        check_data(url, source, project, saved, before_db, "M2.8 baseline checkpoint")
        old_identity = live_identity(source, project, baseline)
        old_schema = sqlite_state(source, project)
        expect("table:provider_test_attempts" not in old_schema["objects"], "baseline already has candidate schema")
        report["runtime"] = {"baseline": old_identity}
        report["database"] = {"baseline": before_db}
        report["checks"].append("baseline-live-writes")

        mark("encrypted-checkpoint")
        baseline_call(baseline, base, "stop", source)
        source_files = {name: ds.sha(source / name) for name in
                        ("owner.json", ".env", "accounts.json", "agent-key", "manifest.json", "lifecycle.json")}
        created = baseline_call(baseline, base, "backup", source, archive, key)
        inspected = backup.inspect(archive, key)
        expect(inspected["archive_sha256"] == created["archive_sha256"], "checkpoint digest differs")
        report["archive_sha256"] = created["archive_sha256"]
        report["checks"].append("authenticated-checkpoint")
        original_port = json.loads((source / "owner.json").read_text())["options"]["port"]
        ports = {original_port}
        def next_port():
            selected = port()
            while selected in ports:
                selected = port()
            ports.add(selected)
            return selected

        mark("construct-candidate")
        upgraded = base / "upgrade"
        record_state(base, "upgrade", None)
        target = restore_candidate(archive, key, upgraded, next_port(), created["archive_sha256"])
        record_state(base, "upgrade", target["project"])
        expect(target["volume_count"] == 5 and target["target_still_stopped"]
               and not target["application_verified"], "candidate clone was not a stopped five-volume target")
        mark("candidate-startup-and-readback")
        url2 = runtime.up(upgraded)["url"]
        new_identity = live_identity(upgraded, target["project"], ROOT)
        expect(new_identity["agent_source_sha256"] != old_identity["agent_source_sha256"]
               and new_identity["images"]["agent"] != old_identity["images"]["agent"],
               "candidate did not load different application code")
        for name, image in old_identity["images"].items():
            if name not in {"agent", "commerce-mcp"}:
                expect(new_identity["images"][name] == image, "infrastructure image changed")
        report["runtime"]["candidate"] = new_identity
        report["checks"].append("candidate-runtime-loaded")
        token2 = check_data(url2, upgraded, target["project"], saved, before_db, "M2.8 baseline checkpoint")
        report["checks"].append("candidate-reads-baseline")
        new_schema = sqlite_state(upgraded, target["project"])
        expect(all(new_schema["objects"].get(k) == v for k, v in old_schema["objects"].items()),
               "candidate changed existing SQLite DDL")
        added = set(new_schema["objects"]) - set(old_schema["objects"])
        expect(added == {"table:provider_test_attempts", "index:idx_probe_member_window",
                        "index:idx_probe_active", "index:idx_events_kind", "index:idx_runs_active"},
               "unexpected live SQLite schema change")
        report["live_sqlite"] = {"added_objects": sorted(added), "existing_objects_unchanged": True,
                                  "baseline_integrity_ok": True, "candidate_integrity_ok": True}
        report["checks"].append("sqlite-additive-schema")

        mark("candidate-writes-and-restart")
        adjust_stock(url2, upgraded, 179)
        extra = agent(url2, "/sessions", token2, data={})["id"]
        agent(url2, "/settings", token2, data={"nickname": "M2.8 candidate write", "compact": True})
        after_db = {**before_db, "stock": 178}
        check_data(url2, upgraded, target["project"], saved, after_db, "M2.8 candidate write", extra)
        report["database"]["candidate"] = after_db
        report["checks"].append("candidate-live-writes")
        runtime.stop(upgraded)
        url2 = runtime.up(upgraded)["url"]
        check_data(url2, upgraded, target["project"], saved, after_db, "M2.8 candidate write", extra)
        report["checks"].append("candidate-restart-retains-writes")
        runtime.stop(upgraded)

        mark("checkpoint-rollback")
        rollback = base / "rollback"
        record_state(base, "rollback", None)
        restored = backup.restore(archive, key, rollback, next_port(), created["archive_sha256"])
        record_state(base, "rollback", restored["project"])
        expect(restored["target_still_stopped"] and not restored["application_verified"], "rollback started prematurely")
        url3 = runtime.up(rollback)["url"]
        rolled_identity = live_identity(rollback, restored["project"], baseline)
        for field in ("portal_jar_sha256", "admin_jar_sha256", "web_dist_sha256", "agent_source_sha256", "images"):
            expect(rolled_identity[field] == old_identity[field], "rollback did not load the old application")
        report["runtime"]["rollback"] = rolled_identity
        report["checks"].append("rollback-runtime-loaded")
        token3 = check_data(url3, rollback, restored["project"], saved, before_db, "M2.8 baseline checkpoint")
        report["database"]["rollback"] = readback(rollback, restored["project"])
        report["checks"].append("checkpoint-data-restored")
        status, _ = request(url3, "/api/agent/sessions/" + extra, bearer=token3)
        expect(status == 404, "candidate-only session unexpectedly survived checkpoint rollback")
        rolled_schema = sqlite_state(rollback, restored["project"])
        expect(rolled_schema == old_schema, "rollback did not restore the old SQLite checkpoint")
        report["live_sqlite"]["rollback_matches_baseline"] = True
        report["checks"].append("candidate-writes-absent-after-checkpoint-rollback")
        runtime.stop(rollback)
        url3 = runtime.up(rollback)["url"]
        check_data(url3, rollback, restored["project"], saved, before_db, "M2.8 baseline checkpoint")
        report["checks"].append("rollback-restart")
        runtime.stop(rollback)

        mark("source-preservation")
        expect(source_files == {name: ds.sha(source / name) for name in source_files}, "source snapshot changed")
        report["checks"].append("source-files-unchanged")
        all_projects = [project, target["project"], restored["project"]]
        volume_sets = [{v["Name"] for v in runtime.resources(st, pr)["volume"]}
                      for st, pr in zip((source, upgraded, rollback), all_projects)]
        expect(len(set(all_projects)) == 3 and all(len(v) == 5 for v in volume_sets)
               and len(set.union(*volume_sets)) == 15, "projects or volume identities were reused")
        report["isolated_project_count"], report["isolated_volume_count"] = 3, 15
        report["checks"].append("new-projects-and-volumes")
        url = baseline_call(baseline, base, "up", source)["url"]
        check_data(url, source, project, saved, before_db, "M2.8 baseline checkpoint")
        report["checks"].append("source-restart-unchanged")
        verify(report)
        report["upgrade_verified"] = report["checkpoint_rollback_verified"] = True
        report["status"] = "passed"
        code = 0
    except Exception as exc:
        report["failure_stage"] = stage
        report["failure_type"] = type(exc).__name__
        report["failure_locations"] = failure_locations(exc)
        if isinstance(exc, BaselineFailure):
            report["baseline_failure"] = exc.details
    finally:
        try:
            errors = cleanup(baseline, base)
        except Exception as exc:
            errors = [{"failure_type": type(exc).__name__}]
        report["cleanup_verified"] = not errors
        if errors:
            report["cleanup_errors"] = errors
            report["status"] = "failed"
            report["upgrade_verified"] = report["checkpoint_rollback_verified"] = False
            code = 1
        ds.write_private(base / "artifacts/evidence.json", json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-checkout", required=True, type=Path)
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        baseline = ds.validate_state(ROOT, args.baseline_checkout.absolute())
        if args.cleanup_only:
            errors = cleanup(baseline, ROOT / ".local/upgrade-smoke")
            print(json.dumps({"cleanup_verified": not errors, "errors": errors}))
            return int(bool(errors))
        return run(baseline)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, runtime.DemoError) as exc:
        print("shop-upgrade refused: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
