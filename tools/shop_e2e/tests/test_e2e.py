"""Unit/configuration tests only. Real business acceptance requires the Docker workflow."""
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest
import yaml

from tools.shop_e2e import evidence as ev
from tools.shop_e2e import stack as s
from tools.shop_e2e import __main__ as runner

ROOT = Path(__file__).resolve().parents[3]
P = "shop-e2e-" + "a" * 32
OP = "e3d2c52b-3e79-40cd-936f-d079b24ff156"


def fake_checkout(tmp_path):
    for side in ("portal", "admin"):
        p = tmp_path / f"services/commerce/mall-{side}/target/mall-{side}-1.0-SNAPSHOT.jar"
        p.parent.mkdir(parents=True); p.write_text("test-double-not-java")
    p = tmp_path / "apps/web/dist/index.html"; p.parent.mkdir(parents=True); p.write_text("test-double")
    p = tmp_path / "deploy/mysql/init"; p.mkdir(parents=True)
    (p / "01-schema.sql").write_text("-- fixture schema")
    (p / "02-seed.sql").write_text("-- fixture seed")
    p = tmp_path / "deploy/mysql/migrations"; p.mkdir()
    for n in range(3, 11):
        (p / f"{n:03d}.sql").write_text("-- fixture migration")
    return tmp_path


def test_prepare_never_overwrites_demo_and_generates_distinct_secrets(tmp_path):
    root = fake_checkout(tmp_path)
    (root / ".env").write_text("DO_NOT_TOUCH")
    first = root / ".local/e2e-one"; second = root / ".local/e2e-two"
    a = s.prepare(root, first); b = s.prepare(root, second)
    assert a["name"] != b["name"]
    assert (first / ".env").read_text() != (second / ".env").read_text()
    assert (root / ".env").read_text() == "DO_NOT_TOUCH"
    assert not (root / ".local/p1-accounts.json").exists()
    assert first.stat().st_mode & 0o077 == 0
    assert (first / ".env").stat().st_mode & 0o077 == 0
    assert (first / "accounts.json").stat().st_mode & 0o077 == 0
    assert len(list((first / "init").glob("*.sql"))) == 10
    with pytest.raises(FileExistsError): s.prepare(root, first)


def test_no_public_business_ports_or_live_provider_settings():
    spec = s.compose(ROOT, ROOT / ".local/test", P)
    assert set(spec["services"]) == {"mysql", "redis", "rabbitmq", "mongo", "portal", "admin", "commerce-mcp", "agent", "web"}
    for name, service in spec["services"].items():
        assert "container_name" not in service
        assert service["labels"] == {s.LABEL: P}
        if name != "web":
            assert "ports" not in service and service["networks"] == ["business"]
        for volume in service.get("volumes", []):
            if isinstance(volume, dict): assert volume["read_only"] and volume["bind"]["create_host_path"] is False
    assert spec["networks"]["business"]["internal"] is True
    assert spec["services"]["web"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert spec["services"]["web"]["ports"][0]["published"] == "0"
    assert not any(v.get("external") or v.get("name") for v in spec["volumes"].values())
    agent = spec["services"]["agent"]["environment"]
    assert agent["SHOP_AGENT_STACK_ENABLE_TEST_PROVIDER"] == "true"
    assert not any("API_KEY" in k or "BASE_URL" in k for k in agent)
    assert spec["services"]["portal"]["environment"]["SHOP_AGENT_STACK_REFUND_ENABLED"] == "false"
    assert spec["services"]["admin"]["environment"]["SHOP_AGENT_STACK_REFUND_ENABLED"] == "true"


@pytest.mark.parametrize("project", ["shop_agent_stack-p0", "shop-e2e-test", "", P + "\n", "other-" + "a"*32])
def test_foreign_project_rejected(project):
    with pytest.raises(ValueError): s.compose(ROOT, ROOT / ".local/x", project)


def test_state_outside_checkout_and_symlinks_rejected(tmp_path):
    (tmp_path / ".local").mkdir()
    with pytest.raises(ValueError): s.validate_state(tmp_path, tmp_path / "artifacts")
    (tmp_path / ".local/link").symlink_to(tmp_path / ".local", target_is_directory=True)
    with pytest.raises(ValueError): s.validate_state(tmp_path, tmp_path / ".local/link/child")


def test_environment_never_inherits_demo_secrets_or_remote_docker(monkeypatch):
    monkeypatch.setenv("SHOP_AGENT_STACK_OPENAI_API_KEY", "synthetic-not-a-key")
    monkeypatch.setenv("SHOP_AGENT_STACK_DB_PASSWORD", "demo-secret")
    monkeypatch.setenv("COMPOSE_FILE", "production.yaml")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2375")
    env = runner.environment()
    assert not any(k.startswith(("SHOP_AGENT_STACK_", "COMPOSE_", "DOCKER_")) for k in env)
    assert runner.DOCKER == ["docker", "--context", "default"]


def xml_report(path, names, marker=None):
    root = ET.Element("testsuite")
    for name in names:
        case = ET.SubElement(root, "testcase", name=name)
        if marker: ET.SubElement(case, marker)
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unexpected", "failure", "error", "skipped", "empty"])
def test_required_reports_fail_closed(tmp_path, kind):
    path = tmp_path / "report.xml"
    names = sorted(ev.BROWSER_TESTS)
    if kind == "missing": names.pop()
    if kind == "duplicate": names.append(names[0])
    if kind == "unexpected": names.append("unrelated-passing-test")
    if kind == "empty": names = []
    xml_report(path, names, kind if kind in ("failure", "error", "skipped") else None)
    with pytest.raises(ValueError): ev.test_report(path, ev.BROWSER_TESTS)


def test_reports_are_summarized_without_payloads(tmp_path):
    path = tmp_path / "report.xml"; xml_report(path, sorted(ev.BROWSER_TESTS))
    tree = ET.parse(path); ET.SubElement(tree.getroot(), "system-out").text = "synthetic-secret"
    tree.write(path)
    result = ev.test_report(path, ev.BROWSER_TESTS)
    assert result["passed"] == 3 and "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("bad", [{"order_id": True, "operation_id": OP}, {"order_id": 1, "operation_id": "'; DROP TABLE x;--"},
                                 {"order_id": 1, "operation_id": OP, "token": "secret"}, {"order_id": -1, "operation_id": OP}])
def test_receipts_accept_identifiers_only(tmp_path, bad):
    p = tmp_path / "receipt.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ValueError): ev.receipt(p, False)


def readback(refunded):
    return {"order_id": 7, "order_status": 4 if refunded else 1, "pay_amount": 49.9,
        "sale_count": int(refunded), "refunded_count": int(refunded), "case_id": 3 if refunded else None,
        "ledger_count": int(refunded), "ledger_case_id": 3 if refunded else None, "ledger_amount": 49.9 if refunded else 0,
        "job_count": int(refunded), "done_jobs": int(refunded), "refund_events": int(refunded),
        "operation_count": 1, "operation_status": "SUCCEEDED" if refunded else "CANCELLED",
        "operation_case": 3 if refunded else None, "consumed": int(refunded)}


@pytest.mark.parametrize("refunded", [True, False])
def test_sql_readback_requires_business_invariants(refunded):
    receipt = {"order_id": 7, "operation_id": OP}
    if refunded: receipt["case_id"] = 3
    actual = readback(refunded)
    assert ev.verify_readback(receipt, actual, refunded)["verified"]
    for field in actual:
        wrong = dict(actual); wrong[field] = "incorrect"
        with pytest.raises(ValueError): ev.verify_readback(receipt, wrong, refunded)
    assert "SELECT JSON_OBJECT" in ev.readback_sql(receipt)


@pytest.mark.parametrize("field,value", [("ledger_count", 2), ("done_jobs", 0), ("ledger_amount", 0.01), ("consumed", 0), ("refund_events", 2), ("sale_count", True)])
def test_green_ui_is_not_ledger_success(field, value):
    actual = readback(True); actual[field] = value
    with pytest.raises(ValueError): ev.verify_readback({"order_id": 7, "case_id": 3, "operation_id": OP}, actual, True)


def test_cleanup_refuses_foreign_labels(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "load_state", lambda _: (P, ["compose-test-double"]))
    monkeypatch.setattr(runner, "resources", lambda *a: {"container": ["fake"], "volume": [], "network": []})
    calls = []
    def command(*args, **kwargs):
        calls.append(args[2]); return json.dumps([{"Config": {"Labels": {s.LABEL: "demo"}}}])
    monkeypatch.setattr(runner, "command", command)
    with pytest.raises(ValueError): runner.cleanup(tmp_path)
    assert not any("down" in c for c in calls)


def test_workflow_keeps_actual_business_job_and_safe_artifacts():
    w = yaml.load((ROOT / ".github/workflows/business-e2e.yml").read_text(), Loader=yaml.BaseLoader)
    assert w["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in w["on"]
    job = w["jobs"]["business"]
    commands = []
    for step in job["steps"]:
        assert "continue-on-error" not in step
        if "run" in step:
            commands.append(step["run"])
            assert subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True).returncode == 0
        if step.get("uses", "").startswith("actions/upload-artifact@"):
            assert step["with"]["path"] == ".local/business-e2e/artifacts/"
    text = "\n".join(commands)
    assert "tools.shop_e2e run" in text and "tools.shop_e2e cleanup" in text
    assert "skipTests=true" not in text and "|| true" not in text and "secrets." not in text
    config = (ROOT / "apps/web/playwright.business.config.ts").read_text()
    assert "retries: 0" in config and 'trace: "off"' in config


def test_failure_excerpt_removes_secrets_and_bounds_output(tmp_path):
    secret = "synthetic-deployment-secret-value"
    (tmp_path / ".env").write_text("KEY=" + secret + "\n")
    text = "x " * 4000 + secret + " Bearer eyJhbGciOiJub25lIn0.payload.signature " + "C" * 43
    result = runner.redact(tmp_path, text)
    assert secret not in result and "eyJhbGci" not in result and "C" * 43 not in result
    assert len(result) <= 6000


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_first_failure_is_not_hidden_by_cleanup(monkeypatch, tmp_path, cleanup_fails):
    def prepare(*args):
        (tmp_path / "artifacts").mkdir()
        (tmp_path / ".env").write_text("KEY=synthetic-secret\n")
    monkeypatch.setattr(runner, "prepare", prepare)
    monkeypatch.setattr(runner, "load_state", lambda _: (P, ["compose-double"]))
    monkeypatch.setattr(runner, "resources", lambda *a: {"container": [], "volume": [], "network": []})
    called = []
    def command(state, name, args, **kwargs):
        if name == "source": raise runner.StageError("source: exit 1")
        return ""
    def cleanup(_):
        called.append(True)
        if cleanup_fails: raise runner.StageError("cleanup failed")
    monkeypatch.setattr(runner, "command", command)
    monkeypatch.setattr(runner, "cleanup", cleanup)
    assert runner.run(tmp_path) == 1
    report = json.loads((tmp_path / "artifacts/evidence.json").read_text())
    assert called == [True] and report["status"] == "failed"
    assert report["cleanup_verified"] is not cleanup_fails
    assert report["failure_stage"] == "source: exit 1"


def test_cleanup_refuses_modified_compose(monkeypatch, tmp_path):
    root = fake_checkout(tmp_path)
    state = root / ".local/test"
    spec = s.prepare(root, state)
    monkeypatch.setattr(runner, "ROOT", root)
    assert runner.load_state(state)[0] == spec["name"]
    spec["services"]["mysql"]["image"] = "not-the-owned-configuration"
    (state / "compose.json").write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="changed"): runner.load_state(state)


def test_dirty_source_cannot_be_reported_as_committed_acceptance(monkeypatch, tmp_path):
    def prepare(*args):
        (tmp_path / "artifacts").mkdir()
        (tmp_path / ".env").write_text("KEY=synthetic-secret\n")
    monkeypatch.setattr(runner, "prepare", prepare)
    monkeypatch.setattr(runner, "load_state", lambda _: (P, ["compose-double"]))
    monkeypatch.setattr(runner, "resources", lambda *a: {"container": [], "volume": [], "network": []})
    calls = []
    def command(state, name, args, **kwargs):
        calls.append(name)
        if name == "source": return "a" * 40 + "\n" + "b" * 40 + "\n"
        if name == "worktree-check": return " M services/agent/implementation.py\n"
        return ""
    monkeypatch.setattr(runner, "command", command)
    monkeypatch.setattr(runner, "cleanup", lambda _: None)
    assert runner.run(tmp_path) == 1
    report = json.loads((tmp_path / "artifacts/evidence.json").read_text())
    assert report["status"] == "failed" and report["cleanup_verified"]
    assert report["failure_stage"].startswith("worktree-check:")
    assert "image-build" not in calls and "startup" not in calls and "browser" not in calls
