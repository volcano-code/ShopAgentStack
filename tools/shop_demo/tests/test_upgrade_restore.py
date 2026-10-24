"""Real encrypted archives and file copies; Docker is an explicit unit double.

These checks validate the new-target contract. Only the separate CI rehearsal
can establish that actual old/new applications read and write the copied data.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from tools.shop_demo import backup as b, backup_crypto as crypto, backup_volume as volume
from tools.shop_demo import runtime, state as ds, upgrade_restore as upgrade


BASE_IMAGE = "sha256:" + "a" * 64
CANDIDATE_IMAGE = "sha256:" + "b" * 64


def fingerprint(folder):
    return {str(p.relative_to(folder)): ds.sha(p)
            for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def rehearsal(checkout, options, monkeypatch):
    """Prepare authentic archive bytes without claiming a real database service."""
    for module in (b, runtime, upgrade):
        monkeypatch.setattr(module, "ROOT", checkout)
    monkeypatch.setattr(b, "contract", lambda: "c" * 64)
    unit = SimpleNamespace(root=checkout, commands=[], transfers=[], created=False,
                           candidate_image=CANDIDATE_IMAGE, failure=None,
                           engine="unit-local-engine", restored={}, source=None)
    monkeypatch.setattr(b, "engine", lambda _: unit.engine)

    def resources(state, project):
        if unit.source is not None and project == unit.owner["project"]:
            return copy.deepcopy(unit.source_resources)
        if not unit.created:
            return {"volume": [], "network": [], "container": []}
        owner = json.loads((state / "owner.json").read_text())
        return {"volume": [{"Name": project + "_" + n, "CreatedAt": "unit-target"}
                           for n in sorted(b.VOLUMES)], "network": [],
                "container": [{"State": {"Running": False}, "Image": image}
                              for image in owner["recovery"]["images"].values()]}

    def command(state, stage, args, **kwargs):
        unit.commands.append((stage, list(args)))
        if stage == "candidate-agent-image":
            return unit.candidate_image
        if stage == "upgrade-create-without-start":
            unit.created = True
        return ""

    def helper(state, folder, image, name, actual, action):
        unit.transfers.append((action, name, actual))
        archive = folder / (name + ".tgz")
        if action == "pack":
            digest = volume.pack(unit.raw / name, archive, name)
        elif action == "verify":
            digest = volume.inventory(archive)[1]
        elif action == "unpack":
            if unit.failure == name:
                raise ValueError("synthetic unit copy failure")
            destination = checkout / ".local/fake-target-volumes" / actual
            destination.mkdir(parents=True, mode=0o700)
            digest = volume.unpack(archive, destination)
            unit.restored[name] = destination
        else:
            pytest.fail("unexpected helper operation")
        return {"verified": True, "inventory_sha256": digest}

    monkeypatch.setattr(runtime, "resources", resources)
    monkeypatch.setattr(runtime, "command", command)
    monkeypatch.setattr(b, "helper", helper)

    def prepare(**overrides):
        unit.source = checkout / ".local/source"
        unit.owner = ds.init(checkout, unit.source,
                             {**options, "seed_import": True, **overrides})
        spec = ds.layout(unit.source, unit.owner)
        unit.source_resources = {
            "volume": [{"Name": unit.owner["project"] + "_" + n,
                        "CreatedAt": "unit-source"} for n in sorted(b.VOLUMES)],
            "network": [],
            "container": [{"Id": name, "Image": BASE_IMAGE,
                           "Config": {"Hostname": "unit-original-rabbit", "Labels": {
                               "com.docker.compose.service": name}},
                           "State": {"Status": "exited", "Running": False, "ExitCode": 0}}
                          for name in spec["services"]]}
        ds._atomic(unit.source / "lifecycle.json", {"phase": "stopped",
                   "volumes": runtime.volume_identity(unit.source_resources["volume"])})
        unit.raw = checkout / ".local/fake-source-volumes"
        for name in sorted(b.VOLUMES):
            folder = unit.raw / name
            folder.mkdir(parents=True, mode=0o700)
            (folder / "synthetic.txt").write_text("unit payload for " + name)
            (folder / "synthetic.txt").chmod(0o600)
        vault = checkout / ".local/vault"
        vault.mkdir(mode=0o700)
        unit.key, unit.archive = vault / "key", vault / "backup.enc"
        crypto.keygen(unit.key)
        created = b.backup(unit.source, unit.archive, unit.key)
        unit.confirmation = created["archive_sha256"]
        unit.target = checkout / ".local/upgraded"
        unit.original_files = fingerprint(unit.source)
        # Candidate build outputs deliberately differ from the archived baseline.
        for name in ("services/agent/shop_agent_stack/__init__.py",
                     "services/commerce/mall-portal/target/mall-portal-1.0-SNAPSHOT.jar",
                     "services/commerce/mall-admin/target/mall-admin-1.0-SNAPSHOT.jar",
                     "apps/web/dist/index.html"):
            (checkout / name).write_text("synthetic candidate bytes: " + name)
        unit.commands.clear()
        unit.transfers.clear()
        return unit

    return prepare


def restore(unit, **kwargs):
    return upgrade.restore_candidate(unit.archive, unit.key,
                                     kwargs.get("target", unit.target),
                                     kwargs.get("port", 18031),
                                     kwargs.get("confirmation", unit.confirmation))


def test_upgrade_builds_new_candidate_snapshot_and_preserves_original_data(rehearsal):
    unit = rehearsal()
    result = restore(unit)
    owner, spec = ds.load(unit.root, unit.target)
    assert result["project"] == owner["project"] != unit.owner["project"]
    assert result["volume_count"] == 5 and result["target_still_stopped"]
    assert result["application_verified"] is False
    assert result["infrastructure_images_unchanged"] is True
    for name in (".env", "accounts.json", "agent-key"):
        assert (unit.target / name).read_bytes() == (unit.source / name).read_bytes()
    for name in ("services/agent/shop_agent_stack/__init__.py",
                 "services/commerce/mall-portal/target/mall-portal-1.0-SNAPSHOT.jar",
                 "services/commerce/mall-admin/target/mall-admin-1.0-SNAPSHOT.jar",
                 "apps/web/dist/index.html"):
        assert (unit.target / "runtime" / name).read_bytes() == (unit.root / name).read_bytes()
        assert (unit.target / "runtime" / name).read_bytes() != (unit.source / "runtime" / name).read_bytes()
    for name, service in spec["services"].items():
        expected = CANDIDATE_IMAGE if name in {"agent", "commerce-mcp"} else BASE_IMAGE
        assert service["image"] == expected
        assert "build" not in service and service["pull_policy"] == "never"
    assert spec["services"]["rabbitmq"]["hostname"] == "unit-original-rabbit"
    assert spec["services"]["web"]["ports"][0]["host_ip"] == "127.0.0.1"
    writes = [(name, actual) for action, name, actual in unit.transfers if action == "unpack"]
    assert {name for name, _ in writes} == b.VOLUMES
    assert all(actual == owner["project"] + "_" + name for name, actual in writes)
    assert [action for action, _, _ in unit.transfers] == ["verify"] * 5 + ["unpack"] * 5
    for name, folder in unit.restored.items():
        assert (folder / "synthetic.txt").read_bytes() == (unit.raw / name / "synthetic.txt").read_bytes()
        assert (folder / "synthetic.txt").stat().st_mode & 0o777 == 0o600
    create = next(args for stage, args in unit.commands if stage == "upgrade-create-without-start")
    assert create[-4:] == ["create", "--no-build", "--pull", "never"]
    assert not any("up" in args or "restart" in args for _, args in unit.commands)
    assert runtime._life(unit.target)["phase"] == "stopped"
    assert runtime._life(unit.target)["recovery_pending"] is False
    assert fingerprint(unit.source) == unit.original_files


@pytest.mark.parametrize("case", ["changed", "added", "removed"])
def test_unknown_mysql_schema_is_rejected_before_new_resources(rehearsal, case):
    unit = rehearsal()
    sql = unit.root / "deploy/mysql/init/00-test.sql"
    if case == "changed":
        sql.write_text("ALTER TABLE unknown DROP COLUMN data;")
    elif case == "added":
        (sql.parent / "11-new.sql").write_text("CREATE TABLE unknown(id INT);")
    else:
        sql.unlink()
    with pytest.raises(ValueError, match="MySQL schema"):
        restore(unit)
    assert not unit.target.exists() and not unit.commands and not unit.transfers
    assert fingerprint(unit.source) == unit.original_files


@pytest.mark.parametrize("target_kind", ["existing-source", "nested-source", "vault"])
def test_existing_nested_or_backup_storage_targets_are_refused(rehearsal, target_kind):
    unit = rehearsal()
    target = {"existing-source": unit.source, "nested-source": unit.source / "child",
              "vault": unit.archive.parent / "child"}[target_kind]
    with pytest.raises((ValueError, FileExistsError)):
        restore(unit, target=target)
    assert not unit.commands and not unit.transfers
    assert fingerprint(unit.source) == unit.original_files


def test_wrong_confirmation_does_not_decrypt_or_touch_target(rehearsal, monkeypatch):
    unit = rehearsal()
    monkeypatch.setattr(b, "opened", lambda *_: pytest.fail("unconfirmed backup was opened"))
    with pytest.raises(ValueError, match="SHA256"):
        restore(unit, confirmation="0" * 64)
    assert not unit.target.exists() and not unit.commands


def test_wrong_key_rejected_before_target_creation(rehearsal):
    unit = rehearsal()
    wrong = unit.key.parent / "wrong-key"
    crypto.keygen(wrong)
    with pytest.raises(ValueError):
        upgrade.restore_candidate(unit.archive, wrong, unit.target, 18031, unit.confirmation)
    assert not unit.target.exists() and not unit.commands and not unit.transfers


@pytest.mark.parametrize("changes", [{"fixture": True}, {"seed_import": False}])
def test_non_rehearsal_source_options_are_refused(rehearsal, changes):
    unit = rehearsal(**changes)
    with pytest.raises(ValueError, match="isolated synthetic"):
        restore(unit)
    assert not unit.target.exists() and not unit.commands and not unit.transfers


def test_source_loopback_port_cannot_be_reused(rehearsal):
    unit = rehearsal()
    with pytest.raises(ValueError, match="isolated synthetic"):
        restore(unit, port=unit.owner["options"]["port"])
    assert not unit.target.exists() and not unit.commands


@pytest.mark.parametrize("image", [BASE_IMAGE, "mutable:tag", "sha256:invalid"])
def test_same_or_unpinned_candidate_image_never_provisions_volumes(rehearsal, image):
    unit = rehearsal()
    unit.candidate_image = image
    with pytest.raises(ValueError, match="candidate Agent image"):
        restore(unit)
    assert not unit.created and not unit.restored
    assert not any(action == "unpack" for action, _, _ in unit.transfers)
    assert not (unit.target / "manifest.json").exists()
    assert fingerprint(unit.source) == unit.original_files


def test_different_engine_is_refused_before_any_volume_access(rehearsal):
    unit = rehearsal()
    unit.engine = "different-unit-engine"
    with pytest.raises(ValueError, match="same local Docker Engine"):
        restore(unit)
    assert not unit.commands and not unit.transfers and not unit.created
    assert fingerprint(unit.source) == unit.original_files


def test_copy_failure_keeps_new_target_unstartable_and_source_unchanged(rehearsal):
    unit = rehearsal()
    unit.failure = "mongo_data"
    with pytest.raises(ValueError, match="unit copy failure"):
        restore(unit)
    ds.load(unit.root, unit.target)
    assert unit.created and runtime._life(unit.target)["recovery_pending"] is True
    with pytest.raises(ValueError, match="incomplete restore"):
        runtime.up(unit.target)
    assert fingerprint(unit.source) == unit.original_files
    assert all(actual.startswith(json.loads((unit.target / "owner.json").read_text())["project"] + "_")
               for action, _, actual in unit.transfers if action == "unpack")
