"""Construct a NEW synthetic upgrade target; ordinary cold restore stays unchanged.

Only the version rehearsal calls this module. It is not a command for upgrading a
user's existing deployment. MySQL inputs and the cold-backup contract must match;
infrastructure images, credentials and the broker identity come from the backup.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets

from . import backup, runtime, state as ds

ROOT = ds.ROOT
APPLICATION_IMAGES = {"agent", "commerce-mcp"}


def check_sql_snapshot(payload: Path, root: Path) -> None:
    """Existing databases never replay initialization SQL to guess a migration."""
    paths = sorted((root / "deploy/mysql/init").glob("*.sql"))
    paths += sorted((root / "deploy/mysql/migrations").glob("*.sql"))
    expected = {f"{i:03d}-{p.name}": ds.sha(p) for i, p in enumerate(paths)}
    actual = {p.name: ds.sha(p) for p in (payload / "snapshot/init").iterdir() if p.is_file()}
    if len(expected) < 10 or actual != expected:
        raise ValueError("unknown MySQL schema change; no migration is authorized by this rehearsal")


def _target_path(target: Path, archive: Path) -> Path:
    target = backup.path_in_private_area(target)
    if target.exists():
        raise FileExistsError("upgrade target must be new")
    for parent in target.parents:
        if parent == ROOT.resolve() / ".local":
            break
        if (parent / "owner.json").exists() or (parent / "manifest.json").exists():
            raise ValueError("upgrade target cannot be nested in another demo")
    if target.is_relative_to(archive.parent) or archive.is_relative_to(target):
        raise ValueError("upgrade target must be separate from backup storage")
    return target


def restore_candidate(archive: Path, key_file: Path, target: Path,
                      port: int, confirmation: str) -> dict:
    """Restore five authenticated volumes into new resources with current code.

    No existing state is edited. The new state has no usable manifest until its
    candidate snapshot and pinned images are complete. It remains unstartable
    until all volume inventories have been verified.
    """
    archive = backup.path_in_private_area(archive)
    target = _target_path(target, archive)
    backup.crypto.private_file(archive)
    if confirmation != ds.sha(archive):
        raise ValueError("upgrade requires the full authenticated archive SHA256")
    selected = ds.inputs(ROOT)
    with backup.opened(archive, key_file) as (meta, payload, original):
        options = {**original["options"], "port": port}
        ds.validate_options(options)
        if (port == original["options"]["port"] or options["retrieval"] != "bm25"
                or options["model_network"] or options["fixture"]
                or not options.get("seed_import") or options.get("seed_review_ui")):
            raise ValueError("upgrade rehearsal requires isolated synthetic BM25 without model egress")
        check_sql_snapshot(payload, ROOT)
        if original.get("recovery"):
            raise ValueError("baseline must be a freshly initialized synthetic source")
        target.mkdir(parents=True, mode=0o700, exist_ok=False)
        (target / "logs").mkdir(mode=0o700)
        with ds.locked(ROOT, target):
            if backup.engine(target) != meta["engine_id"]:
                raise ValueError("upgrade rehearsal requires the same local Docker Engine")
            for image in sorted(set(meta["images"].values())):
                runtime.command(target, "require-baseline-image",
                                [*runtime.DOCKER, "image", "inspect", image], timeout=30)
            for volume in sorted(backup.VOLUMES):
                verified = backup.helper(target, payload / "volumes", meta["images"]["agent"],
                                         volume, None, "verify")
                if verified != meta["volumes"][volume]:
                    raise ValueError("baseline volume archive verification failed")
            project = "shop-demo-" + secrets.token_hex(16)
            if any(runtime.resources(target, project).values()):
                raise ValueError("upgrade namespace collision")
            for name in (".env", "accounts.json", "agent-key"):
                backup._copy(payload / "snapshot" / name, target / name)
            (target / "agent-key").chmod(0o644)
            for source in selected:
                backup._copy(source, target / "runtime" / source.relative_to(ROOT))
            for source in sorted((payload / "snapshot/init").iterdir()):
                backup._copy(source, target / "init" / source.name)

            owner = {"schema": ds.SCHEMA, "root": str(ROOT.resolve()),
                     "project": project, "options": options}
            initial = ds.layout(target, owner)
            tag = initial["services"]["agent"]["image"]
            runtime.command(target, "build-candidate-agent", [*runtime.DOCKER, "build",
                            "--build-arg", "SHOP_AGENT_STACK_WITH_OTEL=true", "--tag", tag,
                            str(target / "runtime/services/agent")], timeout=1200)
            candidate_image = runtime.command(target, "candidate-agent-image", [*runtime.DOCKER,
                              "image", "inspect", "--format", "{{.Id}}", tag], timeout=30).strip()
            if not backup.IMAGE.fullmatch(candidate_image) or candidate_image == meta["images"]["agent"]:
                raise ValueError("candidate Agent image must differ from the baseline image")
            images = {**meta["images"], **{name: candidate_image for name in APPLICATION_IMAGES}}
            owner["recovery"] = {"images": images, "rabbit_hostname": meta["rabbit_hostname"]}
            spec = ds.layout(target, owner)
            ds.write_private(target / "owner.json", json.dumps(owner, indent=2))
            ds.write_private(target / "compose.json", json.dumps(spec, indent=2))
            bound = [p for p in target.rglob("*") if p.is_file()
                     and not p.is_relative_to(target / "logs") and p.name != "command.lock"]
            ds.write_private(target / "manifest.json", json.dumps({"schema": ds.SCHEMA,
                             "files": {str(p.relative_to(target)): ds.sha(p) for p in sorted(bound)}}))
            ds._atomic(target / "lifecycle.json", {"phase": "starting", "volumes": None,
                                                   "recovery_pending": True})
            ds.load(ROOT, target)
            cli = runtime.base(target, project)
            runtime.command(target, "upgrade-create-without-start",
                            [*cli, "create", "--no-build", "--pull", "never"], timeout=600)
            current = runtime.resources(target, project)
            identities = runtime.volume_identity(current["volume"])
            if (set(identities) != {project + "_" + n for n in backup.VOLUMES}
                    or any(v["State"].get("Running") for v in current["container"])):
                raise ValueError("upgrade target was not provisioned empty and stopped")
            ds._atomic(target / "lifecycle.json", {"phase": "starting", "volumes": identities,
                                                   "recovery_pending": True})
            for volume in sorted(backup.VOLUMES):
                result = backup.helper(target, payload / "volumes", meta["images"]["agent"],
                                       volume, project + "_" + volume, "unpack")
                if result != meta["volumes"][volume]:
                    raise ValueError("upgraded clone volume bytes differ from the baseline backup")
            runtime.require_volumes({"volumes": identities}, runtime.resources(target, project))
            ds._atomic(target / "lifecycle.json", {"phase": "stopped", "volumes": identities,
                                                   "recovery_pending": False})
        return {"project": project, "volume_count": len(backup.VOLUMES),
                "target_still_stopped": True, "application_verified": False,
                "candidate_agent_image": candidate_image, "archive_sha256": confirmation,
                "infrastructure_images_unchanged": all(images[n] == meta["images"][n]
                                                       for n in images if n not in APPLICATION_IMAGES)}
