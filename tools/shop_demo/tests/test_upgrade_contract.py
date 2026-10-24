"""Source gates and real SQLite behavior; these are not Docker upgrade evidence."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from tools.shop_demo import upgrade_contract as contract


ROOT = Path(__file__).resolve().parents[3]


def git(root, *args, env=None):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, env=env).strip()


@pytest.fixture
def pair(tmp_path, monkeypatch):
    """Two tiny real repositories; their Store is the actual current source.

    The fixture is deliberately not a claim about the historical baseline.
    The real CI rehearsal fetches and executes the fixed historical commit.
    """
    original_author = git(ROOT, "show", "-s", "--format=%an%n%ae", "HEAD").splitlines()
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME=original_author[0], GIT_AUTHOR_EMAIL=original_author[1],
               GIT_COMMITTER_NAME=original_author[0], GIT_COMMITTER_EMAIL=original_author[1])

    def commit(root):
        git(root, "add", "--all", env=env)
        git(root, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Synthetic unit fixture", env=env)
        return git(root, "rev-parse", "HEAD")

    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); git(old, "init", "--quiet")
    files = {
        ".gitignore": ".local/\n",
        "deploy/mysql/init/01.sql": "CREATE TABLE synthetic_fixture(id INTEGER);\n",
        "deploy/mysql/migrations/02.sql": "CREATE INDEX synthetic_id ON synthetic_fixture(id);\n",
        "apps/web/src/App.tsx": "export const fixtureVersion = 'old';\n",
        contract.STORE_PATH: (ROOT / contract.STORE_PATH).read_text(),
        **{name: "# synthetic unit contract file\n" for name in contract.BACKUP_CONTRACT},
    }
    for name, text in files.items():
        path = old / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
    baseline = commit(old)
    monkeypatch.setattr(contract, "BASELINE_SHA", baseline)
    subprocess.run(["git", "clone", "--quiet", str(old), str(new)], check=True, capture_output=True)
    (new / "apps/web/src/App.tsx").write_text("export const fixtureVersion = 'new';\n")
    commit(new)
    return old, new, commit


def test_pair_binds_exact_clean_versions_and_real_application_difference(pair):
    old, new, _ = pair
    before = [git(root, "status", "--porcelain") for root in (old, new)]
    result = contract.inspect_pair(old, new)
    assert result["baseline"]["sha"] == git(old, "rev-parse", "HEAD")
    assert result["candidate"]["tree_sha"] == git(new, "rev-parse", "HEAD^{tree}")
    assert result["application_changes"] == ["apps/web/src/App.tsx"]
    assert result["mysql_schema_file_count"] == 2
    assert len(result["mysql_schema_digest"]) == len(result["backup_contract_digest"]) == 64
    assert result["upgrade_execution_verified"] is False
    assert result["mysql_ddl_migration_verified"] is False
    assert [git(root, "status", "--porcelain") for root in (old, new)] == before


@pytest.mark.parametrize("which", [0, 1])
def test_dirty_checkout_is_rejected(pair, which):
    old, new, _ = pair
    (old if which == 0 else new).joinpath("untracked.txt").write_text("unreviewed")
    with pytest.raises(ValueError, match="clean"):
        contract.inspect_pair(old, new)


def test_assume_unchanged_cannot_hide_modified_source_bytes(pair):
    old, new, _ = pair
    name = "deploy/mysql/migrations/02.sql"
    git(new, "update-index", "--assume-unchanged", "--", name)
    (new / name).write_text("DROP TABLE synthetic_fixture;\n")
    assert git(new, "status", "--porcelain") == ""
    with pytest.raises(ValueError, match="reviewed Git HEAD"):
        contract.inspect_pair(old, new)


def test_unapproved_baseline_is_rejected(pair, monkeypatch):
    old, new, _ = pair
    monkeypatch.setattr(contract, "BASELINE_SHA", "f" * 40)
    with pytest.raises(ValueError, match="baseline SHA"):
        contract.inspect_pair(old, new)


def test_same_version_and_nested_repository_paths_are_rejected(pair):
    old, new, _ = pair
    with pytest.raises(ValueError, match="different source versions"):
        contract.inspect_pair(old, old)
    with pytest.raises(ValueError, match="checkout root"):
        contract.inspect_pair(old / "apps", new)


def test_documentation_only_candidate_is_not_an_upgrade(pair):
    old, new, commit = pair
    (new / "apps/web/src/App.tsx").write_bytes((old / "apps/web/src/App.tsx").read_bytes())
    (new / "README.md").write_text("synthetic documentation change")
    commit(new)
    with pytest.raises(ValueError, match="documentation-only"):
        contract.inspect_pair(old, new)


@pytest.mark.parametrize("change", ["destructive", "added", "deleted"])
def test_sql_changes_are_refused_before_any_runtime_mutation(pair, change):
    old, new, commit = pair
    sql = new / "deploy/mysql/migrations/02.sql"
    if change == "destructive":
        sql.write_text("DROP TABLE synthetic_fixture;\n")
    elif change == "added":
        sql.with_name("03.sql").write_text("ALTER TABLE synthetic_fixture ADD amount INTEGER;\n")
    else:
        sql.unlink()
    commit(new)
    with pytest.raises(ValueError, match="MySQL"):
        contract.inspect_pair(old, new)
    assert not (new / ".local").exists()


@pytest.mark.parametrize("change", ["changed", "missing", "symlink"])
def test_backup_layout_contract_drift_and_symlink_are_rejected(pair, change):
    old, new, commit = pair
    path = new / contract.BACKUP_CONTRACT[0]
    if change == "changed":
        path.write_text("# changed contract\n")
    else:
        path.unlink()
        if change == "symlink":
            path.symlink_to(new / contract.BACKUP_CONTRACT[1])
    commit(new)
    with pytest.raises(ValueError, match="contract|regular tracked"):
        contract.inspect_pair(old, new)


@pytest.mark.parametrize("after", [
    {},
    {"events": {"type": "table", "sql": "changed schema"}},
    {"events": {"type": "table", "sql": "old schema"}, "unexpected": {"type": "trigger", "sql": "unexpected trigger"}},
])
def test_nonadditive_or_unapproved_sqlite_schema_is_rejected(after):
    before = {"events": {"type": "table", "sql": "old schema"}}
    with pytest.raises(ValueError, match="SQLite"):
        contract._additive_schema(before, after)


def test_sqlite_probe_uses_actual_store_and_removes_raw_database(pair):
    old, new, _ = pair
    private = new / ".local/probe"
    result = contract.run_sqlite_probe(old, new, private)
    assert result["passed"] is True
    assert result["check_count"] == 13 == len(set(result["checks"]))
    assert {"baseline-owner-isolation", "candidate-owner-isolation", "rollback-owner-isolation",
            "rollback-can-write", "candidate-reads-rollback-writes", "sqlite-integrity-check"} <= set(result["checks"])
    assert result["existing_schema_unchanged"] is True
    # This unit fixture intentionally uses current Store for both endpoints.
    assert result["added_tables"] == result["added_indexes"] == []
    assert result["store_sha256"]["candidate"] == hashlib.sha256((ROOT / contract.STORE_PATH).read_bytes()).hexdigest()
    assert result["real_service_stack_verified"] is False
    assert result["mysql_runtime_verified"] is False
    assert result["paid_model_called"] is False
    assert result["temporary_databases_removed"] is True
    assert [path.name for path in private.iterdir()] == ["sqlite-compatibility.json"]
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    report = private / "sqlite-compatibility.json"
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert json.loads(report.read_text()) == result
    original = report.read_bytes()
    with pytest.raises(ValueError, match="previous SQLite probe"):
        contract.run_sqlite_probe(old, new, private)
    assert report.read_bytes() == original


def test_failed_sqlite_probe_retains_bounded_failure_and_removes_database(pair, monkeypatch):
    old, new, _ = pair
    private = new / ".local/failed-probe"
    def deny(*_):
        raise ValueError("synthetic unexpected private failure body")
    monkeypatch.setattr(contract, "_deny_other_owner", deny)
    with pytest.raises(ValueError, match="baseline-write"):
        contract.run_sqlite_probe(old, new, private)
    report = json.loads((private / "sqlite-compatibility.json").read_text())
    assert report["passed"] is False
    assert report["failure_stage"] == "baseline-write"
    assert "private failure body" not in json.dumps(report)
    assert [path.name for path in private.iterdir()] == ["sqlite-compatibility.json"]


@pytest.mark.parametrize("kind", ["outside", "traversal", "public", "symlink"])
def test_probe_directory_safety(pair, kind):
    old, new, _ = pair
    private = new / ".local/probe"
    if kind == "outside":
        private = new.parent / "outside"
    elif kind == "traversal":
        private = new / ".local/../outside"
    elif kind == "public":
        private.mkdir(parents=True, mode=0o755)
    else:
        (new / ".local").mkdir()
        private.symlink_to(old, target_is_directory=True)
    with pytest.raises(ValueError, match="private|0700|symlink"):
        contract.run_sqlite_probe(old, new, private)


def test_probe_does_not_import_model_or_network_modules(pair):
    old, new, commit = pair
    path = new / contract.STORE_PATH
    path.write_text(path.read_text() + "\nimport urllib.request\n")
    commit(new)
    private = new / ".local/unsupported-import"
    with pytest.raises(ValueError, match="load-reviewed-stores"):
        contract.run_sqlite_probe(old, new, private)
    report = json.loads((private / "sqlite-compatibility.json").read_text())
    assert report["passed"] is False
    assert report["failure_stage"] == "load-reviewed-stores"
    assert report["checks"] == []
