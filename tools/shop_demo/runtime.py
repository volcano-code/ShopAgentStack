"""Bounded lifecycle commands. stop retains volumes; deletion requires exact confirmation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import urllib.request

from tools.shop_demo.state import LABEL, ROOT, _atomic, load, locked

DOCKER = ["docker", "--context", "default"]


class DemoError(RuntimeError):
    pass


def environment() -> dict[str, str]:
    # Compose prioritizes shell env over --env-file. Never inherit a different deployment.
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("SHOP_AGENT_STACK_", "SHOP_E2E_", "SHOP_DEMO_", "COMPOSE_", "DOCKER_"))}


def command(state: Path, stage: str, args: list[str], *, timeout: int = 120,
            input_text: str | None = None) -> str:
    print("shop-demo: " + stage, flush=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = state / "logs" / (stamp + "-" + stage + ".log")
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(args, env=environment(), cwd=ROOT, input=input_text,
                                    text=True, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            stream.write(type(exc).__name__)
            raise DemoError(f"{stage}: {type(exc).__name__}; private log: {log.name}") from None
        stream.write(result.stdout + result.stderr)
    if result.returncode:
        raise DemoError(f"{stage}: exit {result.returncode}; private log: {log.name}")
    return result.stdout


def base(state: Path, project: str) -> list[str]:
    return [*DOCKER, "compose", "--project-name", project, "--env-file", str(state / ".env"),
            "-f", str(state / "compose.json")]


def local_engine(state: Path) -> None:
    context = json.loads(command(state, "docker-context", [*DOCKER, "context", "inspect", "default"]))
    host = context[0]["Endpoints"]["docker"]["Host"]
    if not host.startswith(("unix://", "npipe://")):
        raise ValueError("only a local Docker endpoint is supported")
    command(state, "docker-ready", [*DOCKER, "info", "--format", "{{.ServerVersion}}"], timeout=30)


def resources(state: Path, project: str) -> dict[str, list[dict]]:
    """Inspect labels before every lifecycle mutation, including normal stop/up."""
    result = {}
    for kind, args in (("container", ["ps", "-aq"]), ("network", ["network", "ls", "-q"]),
                       ("volume", ["volume", "ls", "-q"])):
        ids = command(state, "list-" + kind, [*DOCKER, *args, "--filter",
                      "label=com.docker.compose.project=" + project], timeout=30).split()
        items = json.loads(command(state, "inspect-" + kind, [*DOCKER, kind, "inspect", *ids], timeout=30)) if ids else []
        for item in items:
            labels = (item.get("Config", {}).get("Labels", {}) if kind == "container" else item.get("Labels", {})) or {}
            if labels.get(LABEL) != project or labels.get("com.docker.compose.project") != project:
                raise ValueError("refusing to operate on a foreign Docker resource")
        result[kind] = items
    return result


def volume_identity(items: list[dict]) -> dict:
    return {item["Name"]: item["CreatedAt"] for item in items}


def require_volumes(life: dict, current: dict) -> None:
    saved = life.get("volumes")
    if saved is not None and saved != volume_identity(current["volume"]):
        raise ValueError("persistent volumes missing or replaced; refusing automatic reinitialization")


def _life(state: Path) -> dict:
    value = json.loads((state / "lifecycle.json").read_text())
    if not isinstance(value, dict) or value.get("phase") not in ("initialized", "starting", "ready", "stopped", "destroyed"):
        raise ValueError("invalid demo lifecycle")
    return value


def _status(state: Path, owner: dict, spec: dict, current: dict) -> dict:
    records = []
    for item in current["container"]:
        name = item["Config"]["Labels"]["com.docker.compose.service"]
        status = item["State"]
        records.append({"service": name, "state": status["Status"],
                        "health": status.get("Health", {}).get("Status", "none")})
    expected = {k for k, v in spec["services"].items() if not v.get("profiles")}
    ready = ({v["service"] for v in records} == expected and
             all(v["state"] == "running" and v["health"] in ("healthy", "none") for v in records))
    url = f"http://127.0.0.1:{owner['options']['port']}"
    return {"project": owner["project"], "url": url, "ready": ready, "services": sorted(records, key=lambda x: x["service"]),
            "retrieval": owner["options"]["retrieval"], "fixture_enabled": owner["options"]["fixture"],
            "model_network_enabled": owner["options"]["model_network"], "public_deployment": False}


# Alive is not READY. Inspect the authenticated worker and Java revision using only
# the internal network. This command does not send a prompt or call a generation model.
HYBRID_READY = """
import json, time, urllib.request
from pathlib import Path
headers = {"X-ShopAgentStack-Index": Path('/run/secrets/index_key').read_text().strip()}
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as response:
        return json.load(response)
for attempt in range(120):
    worker = get('http://127.0.0.1:8020/status')
    authority = get('http://portal:8085/shop_agent_stack/internal/agent/index/state')
    current = authority.get('data', {})
    job = next((v for v in current.get('jobs', []) if v.get('revision') == current.get('epoch')), {})
    if (authority.get('code') == 200 and worker.get('ready') and worker.get('epoch') == current.get('epoch')
            and job.get('status') == 'READY' and job.get('collection_name') == worker.get('collection')):
        print(json.dumps({'ready': True, 'epoch': current['epoch'], 'generation': worker['collection']}))
        break
    time.sleep(2)
else:
    raise SystemExit('hybrid worker did not reach the authoritative revision')
"""


def up(state: Path) -> dict:
    with locked(ROOT, state):
        owner, spec = load(ROOT, state)
        life = _life(state)
        if life["phase"] == "destroyed":
            raise ValueError("destroyed state cannot be restarted; initialize a new state")
        local_engine(state)
        project = owner["project"]
        current = resources(state, project)
        if life["phase"] == "initialized" and any(current.values()):
            raise ValueError("new project collides with existing resources")
        require_volumes(life, current)
        expected_volumes = {project + "_" + name for name in spec["volumes"]}
        if life.get("volumes") is not None and set(life["volumes"]) != expected_volumes:
            raise ValueError("incomplete saved volume inventory; inspect the private state before recovery")
        if life.get("volumes") is None and any(current.values()):
            raise ValueError("unbound resources from an interrupted first start; refusing to adopt them")
        cli = base(state, project)
        command(state, "validate-compose", [*cli, "config", "--quiet"], timeout=30)
        first = life["phase"] in ("initialized", "starting") and life.get("volumes") is None
        _atomic(state / "lifecycle.json", {**life, "phase": "starting"})
        if first:
            command(state, "build-agent", [*cli, "build", "commerce-mcp"], timeout=900)
            if owner["options"]["retrieval"] == "hybrid":
                command(state, "build-retrieval", [*cli, "--profile", "prepare", "build", "model-prefetch"], timeout=1200)
                command(state, "prefetch-public-models", [*cli, "--profile", "prepare", "run", "--rm", "--no-deps", "model-prefetch"], timeout=1200)
        if first:
            # Provision containers/volumes WITHOUT starting business services. Persist
            # identities before a health timeout can leave an initialized database.
            command(state, "prepare-containers", [*cli, "create", "--no-build"], timeout=600)
            provisioned = resources(state, project)
            identities = volume_identity(provisioned["volume"])
            if set(identities) != expected_volumes:
                raise ValueError("provisioned volume inventory incomplete; no business service started")
            life = {"phase": "starting", "volumes": identities}
            _atomic(state / "lifecycle.json", life)
        command(state, "start", [*cli, "up", "-d", "--wait", "--wait-timeout", "300"], timeout=600)
        current = resources(state, project)
        require_volumes(life, current)
        if set(volume_identity(current["volume"])) != expected_volumes:
            raise ValueError("persistent volume inventory incomplete")
        summary = _status(state, owner, spec, current)
        if not summary["ready"]:
            raise DemoError("not all expected services are healthy")
        _atomic(state / "lifecycle.json", {"phase": "starting", "volumes": volume_identity(current["volume"])})
        if owner["options"]["retrieval"] == "hybrid":
            readiness = json.loads(command(state, "hybrid-ready", [*cli, "exec", "-T", "retrieval-worker",
                                   "python", "-c", HYBRID_READY], timeout=300))
            if readiness.get("ready") is not True or not readiness.get("generation"):
                raise DemoError("hybrid readiness evidence missing")
            summary["hybrid"] = readiness
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(summary["url"] + "/api/agent/health", timeout=10) as response:
            if response.status != 200:
                raise DemoError("proxy to Agent health failed")
        _atomic(state / "lifecycle.json", {"phase": "ready", "volumes": volume_identity(current["volume"])})
        return summary


def status(state: Path) -> dict:
    with locked(ROOT, state):
        owner, spec = load(ROOT, state)
        local_engine(state)
        current = resources(state, owner["project"])
        life = _life(state)
        if life["phase"] != "destroyed":
            require_volumes(life, current)
        return {**_status(state, owner, spec, current), "phase": life["phase"]}


def stop(state: Path) -> dict:
    with locked(ROOT, state):
        owner, _ = load(ROOT, state)
        life = _life(state)
        if life["phase"] == "destroyed":
            raise ValueError("demo was explicitly destroyed")
        local_engine(state)
        current = resources(state, owner["project"])
        require_volumes(life, current)
        before = volume_identity(current["volume"])
        command(state, "stop-retain-data", [*base(state, owner["project"]), "stop", "--timeout", "30"], timeout=180)
        current = resources(state, owner["project"])
        if before != volume_identity(current["volume"]) or any(v["State"]["Running"] for v in current["container"]):
            raise DemoError("stop did not retain volumes or a container is still running")
        _atomic(state / "lifecycle.json", {**life, "phase": "stopped" if life.get("volumes") is not None else "starting"})
        return {"project": owner["project"], "stopped": True, "volumes_retained": True}


def destroy(state: Path, confirmation: str) -> dict:
    with locked(ROOT, state):
        owner, _ = load(ROOT, state)
        project = owner["project"]
        if confirmation != project:
            raise ValueError("deletion requires --confirm followed by this exact demo project name")
        local_engine(state)
        resources(state, project)  # validates every resource; never use the E2E cleanup entrypoint
        command(state, "destroy-confirmed-demo", [*base(state, project), "down", "--volumes", "--remove-orphans"], timeout=180)
        if any(resources(state, project).values()):
            raise DemoError("demo resources remain after cleanup")
        _atomic(state / "lifecycle.json", {"phase": "destroyed", "volumes": None})
        return {"project": project, "destroyed": True, "source_and_private_state_retained": True}
