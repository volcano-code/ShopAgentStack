"""Versioned private demo snapshots. Never adopt, migrate or reset an existing database."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from tools.shop_e2e.stack import service_layout, validate_state, write_private

ROOT = Path(__file__).resolve().parents[2]
LABEL = "io.shopagentstack.demo"
PROJECT = re.compile(r"shop-demo-[0-9a-f]{32}\Z")
SCHEMA = "shop-local-demo-v1"
SECRET_NAMES = ("DB_PASSWORD", "DB_ROOT_PASSWORD", "MQ_PASSWORD", "PORTAL_JWT_SECRET",
                "ADMIN_JWT_SECRET", "BOOTSTRAP_ADMIN_PASSWORD", "BOOTSTRAP_SERVICE_PASSWORD")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def no_symlinks(path: Path, boundary: Path) -> None:
    if not path.absolute().is_relative_to(boundary.absolute()) or not path.resolve().is_relative_to(boundary.resolve()):
        raise ValueError("path outside the selected checkout/state")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("symlink in demo inputs/state")
        if part == boundary:
            break


def validate_options(options: dict) -> None:
    if set(options) - {"seed_import"} != {"retrieval", "port", "fixture", "model_network"}:
        raise ValueError("unknown or missing demo options")
    if options["retrieval"] not in ("bm25", "hybrid"):
        raise ValueError("retrieval must be bm25 or hybrid")
    if type(options["port"]) is not int or not 1024 <= options["port"] <= 65535:
        raise ValueError("choose a loopback port between 1024 and 65535")
    if any(type(options[k]) is not bool for k in ("fixture", "model_network")):
        raise ValueError("demo flags must be booleans")
    if type(options.get("seed_import", False)) is not bool:
        raise ValueError("seed_import must be a boolean")
    if options["fixture"] and options["model_network"]:
        raise ValueError("fixture and model egress are mutually exclusive")


def layout(state: Path, owner: dict) -> dict:
    """Reuse tested wiring but never the test runner's credentials or cleanup."""
    project, options = owner["project"], owner["options"]
    if not PROJECT.fullmatch(project):
        raise ValueError("invalid demo project")
    validate_options(options)
    spec = service_layout(state / "runtime", state, project, label={LABEL: project},
                          test_mode=False, hybrid=options["retrieval"] == "hybrid")
    services = spec["services"]
    if options.get("seed_import", False):
        services["admin"]["environment"]["SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED"] = "true"
    services["web"]["ports"][0]["published"] = str(options["port"])
    services["agent"]["environment"]["SHOP_AGENT_STACK_ENABLE_TEST_PROVIDER"] = str(options["fixture"]).lower()
    services["redis"]["command"] = ["redis-server", "--appendonly", "yes"]
    services["redis"]["volumes"] = ["redis_data:/data"]
    spec["volumes"]["redis_data"] = {"labels": {LABEL: project}}
    # No restart-unless-stopped: starting Docker must not silently start a demo/model.
    if options["model_network"]:
        spec["networks"]["model-egress"] = {"labels": {LABEL: project}}
        services["agent"]["networks"] = ["business", "model-egress"]
    if options["retrieval"] == "hybrid":
        # The offline worker doesn't need the test harness executable mounted at all.
        for name in ("model-prefetch", "retrieval-worker"):
            services[name]["volumes"] = [v for v in services[name]["volumes"]
                if not (isinstance(v, dict) and v["target"] == "/workspace/tools/shop_e2e")]
    if "recovery" in owner:
        recovery = owner["recovery"]
        if (options["retrieval"] != "bm25" or options["model_network"] or
                set(recovery) != {"images", "rabbit_hostname"} or
                set(recovery["images"]) != set(services) or
                not all(isinstance(v, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", v) for v in recovery["images"].values()) or
                not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}", recovery["rabbit_hostname"])):
            raise ValueError("invalid pinned cold-recovery configuration")
        for name, image in recovery["images"].items():
            services[name]["image"] = image
            services[name]["pull_policy"] = "never"
            services[name].pop("build", None)
        services["rabbitmq"]["hostname"] = recovery["rabbit_hostname"]
    return spec


def inputs(root: Path) -> list[Path]:
    """Only tracked-style source and built UI/jars; never copy .env, DBs or caches."""
    required = [root / f"services/commerce/mall-{side}/target/mall-{side}-1.0-SNAPSHOT.jar"
                for side in ("portal", "admin")]
    required += [root / "apps/web/dist/index.html", root / "evaluation/retrieval-matrix.json",
                 root / "deploy/nginx/shop_agent_stack-p2.conf"]
    for side in ("agent", "retrieval"):
        required += [root / f"services/{side}/Dockerfile", root / f"services/{side}/requirements.lock"]
    required.append(root / "services/agent/requirements-observability.txt")
    if any(not f.is_file() for f in required):
        raise ValueError("build Java and Web first: python -m tools.shop_demo build")
    result = set(required)
    for folder, pattern in (("services/agent/shop_agent_stack", "*.py"), ("services/agent/tests", "*.py"),
                            ("services/retrieval", "*.py"), ("deploy/config", "*"),
                            ("deploy/retrieval", "*-e2e.yaml"), ("apps/web/dist", "*")):
        for path in (root / folder).rglob(pattern):
            if any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(root / folder).parts):
                continue
            no_symlinks(path, root)
            if path.is_file():
                result.add(path)
    for path in result:
        no_symlinks(path, root)
    return sorted(result)


def _atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    write_private(tmp, json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def init(root: Path, state: Path, options: dict) -> dict:
    root = root.resolve()
    state = validate_state(root, state)
    if state.resolve() == root / ".local":
        raise ValueError("choose a dedicated demo subdirectory, not the shared .local directory")
    validate_options(options)
    selected = inputs(root)
    migrations = sorted((root / "deploy/mysql/init").glob("*.sql")) + sorted((root / "deploy/mysql/migrations").glob("*.sql"))
    if len(migrations) < 10:
        raise ValueError("schema/migrations incomplete")
    for path in migrations:
        no_symlinks(path, root)
    # Atomic mkdir reserves a new state; an existing/partial state is never overwritten.
    state.mkdir(parents=True, mode=0o700, exist_ok=False)
    project = "shop-demo-" + secrets.token_hex(16)
    owner = {"schema": SCHEMA, "project": project, "root": str(root), "options": options}
    write_private(state / "owner.json", json.dumps(owner, indent=2))
    values = {"SHOP_AGENT_STACK_" + k: secrets.token_urlsafe(48) for k in SECRET_NAMES}
    write_private(state / ".env", "\n".join(k + "=" + v for k, v in values.items()) + "\n")
    write_private(state / "accounts.json", json.dumps([
        {"role": k, "username": "shop_agent_stack_" + k.lower(),
         "password": values["SHOP_AGENT_STACK_BOOTSTRAP_" + k + "_PASSWORD"]}
        for k in ("ADMIN", "SERVICE")], indent=2))
    write_private(state / "agent-key", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    (state / "agent-key").chmod(0o644)  # protected by 0700 host parent; directly mounted for UID 10001
    if options["retrieval"] == "hybrid":
        write_private(state / "index-key", secrets.token_hex(32))
        (state / "index-key").chmod(0o644)
    for source in selected:
        target = state / "runtime" / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (state / "init").mkdir()
    for index, source in enumerate(migrations):
        (state / "init" / f"{index:03d}-{source.name}").write_bytes(source.read_bytes())
    (state / "logs").mkdir(mode=0o700)
    spec = layout(state, owner)
    write_private(state / "compose.json", json.dumps(spec, indent=2))
    bound = [f for f in state.rglob("*") if f.is_file()]
    write_private(state / "manifest.json", json.dumps({"schema": SCHEMA,
        "files": {str(f.relative_to(state)): sha(f) for f in sorted(bound)}}, indent=2))
    _atomic(state / "lifecycle.json", {"phase": "initialized", "volumes": None})
    return owner


def load(root: Path, state: Path) -> tuple[dict, dict]:
    state = validate_state(root, state)
    for path in state.rglob("*"):
        no_symlinks(path, state)
    if os.name == "posix":
        if stat.S_IMODE(state.stat().st_mode) & 0o077:
            raise ValueError("demo state must be private (chmod 700)")
        for name in (".env", "accounts.json"):
            if stat.S_IMODE((state / name).stat().st_mode) & 0o077:
                raise ValueError("demo credentials must be private (chmod 600)")
    manifest = json.loads((state / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or not isinstance(manifest.get("files"), dict):
        raise ValueError("unsupported demo manifest")
    files = manifest["files"]
    for name in ("owner.json", "compose.json", ".env", "accounts.json", "agent-key"):
        if name not in files:
            raise ValueError("incomplete demo manifest")
    for name, digest in files.items():
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
            raise ValueError("invalid snapshot manifest path")
        path = state / name
        no_symlinks(path, state)
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or sha(path) != digest:
            raise ValueError("demo snapshot changed; do not edit a initialized state")
    # Detect unmanifested files in mounted directories too, not merely changed known files.
    actual = {str(p.relative_to(state)) for directory in ("runtime", "init")
              for p in (state / directory).rglob("*") if p.is_file()}
    expected = {name for name in files if name.startswith(("runtime/", "init/"))}
    if not actual or actual != expected:
        raise ValueError("snapshot file inventory changed")
    owner = json.loads((state / "owner.json").read_text())
    if not isinstance(owner, dict) or owner.get("schema") != SCHEMA or owner.get("root") != str(root.resolve()):
        raise ValueError("not a demo owned by this checkout")
    spec = layout(state, owner)
    if json.loads((state / "compose.json").read_text()) != spec:
        raise ValueError("demo configuration changed")
    return owner, spec


@contextmanager
def locked(root: Path, state: Path):
    validate_state(root, state)
    fd = os.open(state / "command.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        (state / "command.lock").unlink()
