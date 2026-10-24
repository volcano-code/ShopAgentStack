"""Bounded evidence and cleanup checks; no simulated full-stack success claim."""
from __future__ import annotations

import copy
import json

import pytest

from tools.shop_demo import runtime, state as ds, upgrade_smoke as smoke


def valid_report():
    before = {"products": 100, "skus": 100, "published": 96, "drafts": 0,
              "customerClauses": 241, "staffClauses": 48, "epoch": 97, "stock": 179}
    return {"scope": smoke.SCOPE, "checks": sorted(smoke.CHECKS),
            "source_pair": {"baseline": {"sha": "a" * 40}, "candidate": {"sha": "b" * 40},
                            "mysql_schema_unchanged": True, "backup_contract_unchanged": True,
                            "application_changed": True},
            "sqlite_probe": {"passed": True}, "isolated_project_count": 3, "isolated_volume_count": 15,
            "database": {"baseline": before, "candidate": {**before, "stock": 178},
                         "rollback": copy.deepcopy(before)},
            "live_llm_called": False, "public_deployment": False,
            "full_stack_lossless_downgrade_verified": False,
            "mysql_ddl_migration_verified": False, "hybrid_recovery_verified": False,
            "post_checkpoint_writes_retained_by_rollback": False}


def test_evidence_requires_every_check_once():
    smoke.verify(valid_report())
    for name in smoke.CHECKS:
        report = valid_report()
        report["checks"].remove(name)
        with pytest.raises(runtime.DemoError, match="missing or duplicate"):
            smoke.verify(report)
    report = valid_report()
    report["checks"].append(report["checks"][0])
    with pytest.raises(runtime.DemoError, match="missing or duplicate"):
        smoke.verify(report)


@pytest.mark.parametrize("field", ["live_llm_called", "public_deployment",
    "full_stack_lossless_downgrade_verified", "mysql_ddl_migration_verified",
    "hybrid_recovery_verified", "post_checkpoint_writes_retained_by_rollback"])
def test_evidence_cannot_claim_an_unperformed_or_lossless_migration(field):
    report = valid_report()
    report[field] = True
    with pytest.raises(runtime.DemoError, match="scope was overstated"):
        smoke.verify(report)


@pytest.mark.parametrize("fault", ["same-version", "mysql", "backup-contract", "no-code-change",
    "shared-project", "shared-volume", "sqlite-failed", "candidate-write-missing", "rollback-new-write"])
def test_evidence_rejects_incompatible_or_incomplete_results(fault):
    report = valid_report()
    if fault == "same-version":
        report["source_pair"]["candidate"] = report["source_pair"]["baseline"]
    elif fault in ("mysql", "backup-contract", "no-code-change"):
        field = {"mysql": "mysql_schema_unchanged", "backup-contract": "backup_contract_unchanged",
                 "no-code-change": "application_changed"}[fault]
        report["source_pair"][field] = False
    elif fault == "shared-project":
        report["isolated_project_count"] = 2
    elif fault == "shared-volume":
        report["isolated_volume_count"] = 10
    elif fault == "sqlite-failed":
        report["sqlite_probe"]["passed"] = False
    elif fault == "candidate-write-missing":
        report["database"]["candidate"]["stock"] = 179
    else:
        report["database"]["rollback"]["stock"] = 178
    with pytest.raises(runtime.DemoError):
        smoke.verify(report)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    baseline = root / ".local/baseline"
    baseline.mkdir(parents=True)
    base = root / ".local/upgrade-smoke"
    base.mkdir(mode=0o700)
    monkeypatch.setattr(smoke, "ROOT", root)
    calls = []
    monkeypatch.setattr(runtime, "destroy", lambda state, project: calls.append(("new", state, project)))
    monkeypatch.setattr(smoke, "baseline_call",
                        lambda old, logs, action, state, project: calls.append((action, state, project)))

    def state(name, suffix, phase="stopped", owner_root=None):
        location = baseline / ".local/upgrade-source" if name == "source" else base / name
        location.mkdir(parents=True, mode=0o700)
        project = "shop-demo-" + suffix * 32
        ds.write_private(location / "manifest.json", "{}")
        expected = baseline if name == "source" else root
        ds.write_private(location / "owner.json", json.dumps({"root": str(owner_root or expected),
                                                             "project": project}))
        ds._atomic(location / "lifecycle.json", {"phase": phase, "volumes": None})
        return location, project

    def receipt(owned_states, **changes):
        ds._atomic(base / "ownership.json", {"schema": "shop-demo-upgrade-owned/v1",
                    "baseline": str(baseline), "states": owned_states, **changes})

    return root, baseline, base, calls, state, receipt


def test_cleanup_uses_only_reserved_paths_and_their_project_receipts(owned):
    root, baseline, base, calls, create, receipt = owned
    source, source_project = create("source", "1")
    target, target_project = create("upgrade", "2")
    unreserved, _ = create("rollback", "3")
    foreign = root / ".local/another-demo"
    foreign.mkdir()
    (foreign / "untouched.txt").write_text("not owned by this rehearsal")
    receipt({"source": source_project, "upgrade": target_project})
    assert smoke.cleanup(baseline, base) == []
    assert calls == [("new", target, target_project), ("destroy", source, source_project)]
    assert (unreserved / "manifest.json").exists()
    assert (foreign / "untouched.txt").read_text() == "not owned by this rehearsal"


def test_cleanup_can_finish_a_reserved_partial_init_without_adopting_another_path(owned):
    _, baseline, base, calls, create, receipt = owned
    target, project = create("upgrade", "2")
    receipt({"upgrade": None})
    assert smoke.cleanup(baseline, base) == []
    assert calls == [("new", target, project)]


@pytest.mark.parametrize("fault", ["receipt-project", "owner-root", "symlink", "destroyed"])
def test_cleanup_refuses_mismatched_ownership_and_skips_already_destroyed(owned, fault):
    root, baseline, base, calls, create, receipt = owned
    target, project = create("upgrade", "2", phase="destroyed" if fault == "destroyed" else "stopped",
                             owner_root=baseline if fault == "owner-root" else root)
    receipt({"upgrade": "shop-demo-" + "9" * 32 if fault == "receipt-project" else project})
    if fault == "symlink":
        actual = base / "unrelated"
        target.rename(actual)
        target.symlink_to(actual, target_is_directory=True)
    errors = smoke.cleanup(baseline, base)
    assert calls == []
    if fault == "destroyed":
        assert errors == []
    else:
        assert len(errors) == 1 and errors[0]["state"] == "upgrade"


@pytest.mark.parametrize("changes", [{"states": {"../another-demo": None}}, {"baseline": "/unrelated"},
                                      {"schema": "unrecognized-owner-format"}])
def test_cleanup_rejects_unrecognized_ownership_record_before_mutation(owned, changes):
    _, baseline, base, calls, _, receipt = owned
    receipt({}, **changes)
    with pytest.raises(ValueError, match="invalid upgrade ownership"):
        smoke.cleanup(baseline, base)
    assert calls == []


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_preflight_failure_is_preserved_and_never_becomes_an_upgrade_success(tmp_path, monkeypatch, capsys, cleanup_fails):
    root = tmp_path / "checkout"
    baseline = root / ".local/baseline"
    baseline.mkdir(parents=True)
    monkeypatch.setattr(smoke, "ROOT", root)

    def refuse(*args):
        raise ValueError("private-detail-that-must-not-be-exported")

    monkeypatch.setattr(smoke, "inspect_pair", refuse)
    monkeypatch.setattr(smoke, "baseline_call", lambda *_: pytest.fail("baseline services must not start"))
    if cleanup_fails:
        def broken_cleanup(*args):
            raise OSError("private-cleanup-detail")
        monkeypatch.setattr(smoke, "cleanup", broken_cleanup)
    assert smoke.run(baseline) == 1
    evidence = root / ".local/upgrade-smoke/artifacts/evidence.json"
    report = json.loads(evidence.read_text())
    assert report["status"] == "failed" and report["failure_stage"] == "pair-preflight"
    assert report["failure_type"] == "ValueError"
    assert report["upgrade_verified"] is report["checkpoint_rollback_verified"] is False
    assert report["checks"] == [] and report["cleanup_verified"] is (not cleanup_fails)
    assert evidence.stat().st_mode & 0o777 == 0o600
    if cleanup_fails:
        assert report["cleanup_errors"] == [{"failure_type": "OSError"}]
    output = capsys.readouterr().out + evidence.read_text()
    assert "private-detail-that-must-not-be-exported" not in output
    assert "private-cleanup-detail" not in output


@pytest.mark.parametrize("option", ["--source", "--state", "--archive", "--key-file", "--url"])
def test_entrypoint_does_not_accept_a_user_deployment_or_credentials(tmp_path, monkeypatch, option):
    monkeypatch.setattr(smoke, "run", lambda *_: pytest.fail("unsupported arguments reached the runner"))
    with pytest.raises(SystemExit) as stopped:
        smoke.main(["--baseline-checkout", str(tmp_path), option, "untrusted-input"])
    assert stopped.value.code == 2


def test_entrypoint_rejects_baseline_outside_private_checkout_area(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(smoke, "ROOT", root)
    monkeypatch.setattr(smoke, "run", lambda *_: pytest.fail("outside path reached the runner"))
    assert smoke.main(["--baseline-checkout", str(tmp_path / "outside")]) == 2


@pytest.mark.parametrize("fault", [None, "image", "code", "mount", "foreign-volume"])
def test_live_identity_checks_loaded_code_pinned_images_and_actual_mounts(checkout, options, monkeypatch, fault):
    # Only the inspection function is tested with explicit Docker observations.
    # Snapshot authentication and creation are tested by test_upgrade_restore.
    selected = checkout / ".local/live-unit"
    owner = ds.init(checkout, selected, options)
    spec = ds.layout(selected, owner)
    image = "sha256:" + "c" * 64
    for service in spec["services"].values():
        service["image"] = image
    (selected / "compose.json").write_text(json.dumps(spec))
    records = []
    for name, service in spec["services"].items():
        mounts = []
        for item in service.get("volumes", []):
            if isinstance(item, dict):
                mounts.append({"Destination": item["target"], "Type": "bind",
                               "Source": item["source"], "RW": False})
            else:
                logical, destination = item.split(":", 1)
                mounts.append({"Destination": destination, "Type": "volume",
                               "Name": owner["project"] + "_" + logical})
        records.append({"Config": {"Labels": {"com.docker.compose.service": name}},
                        "Image": image, "State": {"Running": True}, "Mounts": mounts})
    if fault == "image":
        records[-1]["Image"] = "sha256:" + "d" * 64
    elif fault == "mount":
        next(m for r in records for m in r["Mounts"] if m["Type"] == "bind")["RW"] = True
    elif fault == "foreign-volume":
        next(m for r in records for m in r["Mounts"] if m["Type"] == "volume")["Name"] = "another-project_data"
    python_root = selected / "runtime/services/agent/shop_agent_stack"
    loaded = {str(p.relative_to(python_root)): ds.sha(p) for p in python_root.rglob("*.py")}
    if fault == "code":
        loaded["__init__.py"] = "0" * 64
    monkeypatch.setattr(runtime, "resources", lambda *_: {"container": records})
    monkeypatch.setattr(runtime, "command", lambda *_: json.dumps(loaded))
    if fault:
        with pytest.raises(runtime.DemoError):
            smoke.live_identity(selected, owner["project"], checkout)
    else:
        result = smoke.live_identity(selected, owner["project"], checkout)
        assert result["bind_mounts_verified"] and result["loaded_python_verified"]
        assert set(result["images"]) == set(spec["services"])
