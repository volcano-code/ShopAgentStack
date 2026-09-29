"""Build a fresh test-only Compose project without importing local deployment state."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil

LABEL = "io.shopagentstack.e2e"
PROJECT = re.compile(r"shop-e2e-[0-9a-f]{32}\Z")


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def validate_state(root: Path, state: Path) -> Path:
    root = root.resolve()
    state = state.absolute()
    if state.is_symlink() or not state.resolve().is_relative_to(root / ".local"):
        raise ValueError("state must be inside this checkout's .local directory")
    # Do not follow intermediate symlinks, even when they point back into .local.
    for part in [state, *state.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("state path contains a symlink")
    return state


def compose(root: Path, state: Path, project: str, *, tracing: bool = False, hybrid: bool = False) -> dict:
    if not PROJECT.fullmatch(project):
        raise ValueError("invalid isolated project")
    label = {LABEL: project}
    def bind(source: Path, target: str) -> dict:
        return {"type": "bind", "source": str(source), "target": target,
                "read_only": True, "bind": {"create_host_path": False}}
    def health(command: list[str], retries: int = 48) -> dict:
        return {"test": command, "interval": "5s", "timeout": "5s", "retries": retries}
    db_env = {
        "MYSQL_DATABASE": "shop_agent_stack", "MYSQL_USER": "shop_agent_stack",
        "MYSQL_PASSWORD": "${SHOP_AGENT_STACK_DB_PASSWORD:?isolated test secret required}",
        "MYSQL_ROOT_PASSWORD": "${SHOP_AGENT_STACK_DB_ROOT_PASSWORD:?isolated test secret required}",
    }
    services = {
        "mysql": {"image": "mysql:8.4", "environment": db_env,
                  "volumes": ["mysql_data:/var/lib/mysql", bind(state / "init", "/docker-entrypoint-initdb.d")],
                  "healthcheck": health(["CMD-SHELL", 'MYSQL_PWD="$$MYSQL_PASSWORD" mysql -h127.0.0.1 -ushop_agent_stack -Dshop_agent_stack -e "SELECT COUNT(*) FROM shop_agent_stack_shipment_event" >/dev/null 2>&1'])},
        "redis": {"image": "redis:7.4-alpine", "healthcheck": health(["CMD", "redis-cli", "ping"])},
        "rabbitmq": {"image": "rabbitmq:3.13-management",
                     "environment": {"RABBITMQ_DEFAULT_USER": "shop_agent_stack", "RABBITMQ_DEFAULT_PASS": "${SHOP_AGENT_STACK_MQ_PASSWORD:?isolated test secret required}", "RABBITMQ_DEFAULT_VHOST": "/shop_agent_stack"},
                     "volumes": ["rabbit_data:/var/lib/rabbitmq"], "healthcheck": health(["CMD", "rabbitmq-diagnostics", "-q", "ping"])},
        "mongo": {"image": "mongo:7.0", "volumes": ["mongo_data:/data/db"],
                  "healthcheck": health(["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"])},
    }
    depends = {k: {"condition": "service_healthy"} for k in services}
    for side, module, port in [("portal", "mall-portal", 8085), ("admin", "mall-admin", 8080)]:
        env = {"SHOP_AGENT_STACK_DB_PASSWORD": "${SHOP_AGENT_STACK_DB_PASSWORD}",
               "SHOP_AGENT_STACK_MQ_PASSWORD": "${SHOP_AGENT_STACK_MQ_PASSWORD}",
               "SHOP_AGENT_STACK_JWT_SECRET": "${SHOP_AGENT_STACK_" + side.upper() + "_JWT_SECRET}",
               "SHOP_AGENT_STACK_REFUND_ENABLED": str(side == "admin").lower()}
        if side == "admin":
            for role in ("ADMIN", "SERVICE"):
                key = f"SHOP_AGENT_STACK_BOOTSTRAP_{role}_PASSWORD"
                env[key] = "${" + key + "}"
        services[side] = {"image": "maven:3.9.9-eclipse-temurin-17",
            "command": ["java", "-Xms128m", "-Xmx512m", "-jar", "/app/app.jar", "--spring.config.additional-location=file:/config/", f"--server.port={port}"],
            "environment": env, "depends_on": depends,
            "volumes": [bind(root / f"services/commerce/{module}/target/{module}-1.0-SNAPSHOT.jar", "/app/app.jar"), bind(root / "deploy/config", "/config")],
            "healthcheck": health(["CMD", "curl", "--fail", "--silent", f"http://localhost:{port}/actuator/health"])}
    image = project + "-agent:local"
    services["commerce-mcp"] = {
        "image": image, "build": {"context": str(root / "services/agent"), "args": {"SHOP_AGENT_STACK_WITH_OTEL": "true"}},
        "command": ["uvicorn", "shop_agent_stack.mcp_server:app", "--host", "0.0.0.0", "--port", "8011", "--no-access-log"],
        "environment": {"SHOP_AGENT_STACK_PORTAL_URL": "http://portal:8085", "SHOP_AGENT_STACK_RETRIEVAL_MODE": "bm25"},
        "depends_on": {"portal": {"condition": "service_healthy"}},
        "healthcheck": health(["CMD", "python", "-c", "import socket; socket.create_connection(('127.0.0.1',8011),2).close()"]),
    }
    services["agent"] = {"image": image,
        "environment": {"SHOP_AGENT_STACK_PORTAL_URL": "http://portal:8085", "SHOP_AGENT_STACK_MCP_URL": "http://commerce-mcp:8011/mcp", "SHOP_AGENT_STACK_ENABLE_TEST_PROVIDER": "true"},
        "volumes": ["agent_data:/data", bind(state / "agent-key", "/run/secrets/agent_key")],
        "depends_on": {"commerce-mcp": {"condition": "service_healthy"}},
        "healthcheck": health(["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health',timeout=2)"])}
    services["web"] = {"image": "nginx:1.28-alpine",
        "ports": [{"target": 80, "host_ip": "127.0.0.1", "published": "0", "protocol": "tcp"}],
        "networks": ["business", "edge"],
        "volumes": [bind(root / "apps/web/dist", "/usr/share/nginx/html"), bind(root / "deploy/nginx/shop_agent_stack-p2.conf", "/etc/nginx/conf.d/default.conf")],
        "depends_on": {k: {"condition": "service_healthy"} for k in ("portal", "admin", "agent")},
        "healthcheck": health(["CMD", "wget", "--spider", "-q", "http://127.0.0.1/"])}
    if tracing:
        add_tracing(root, services, bind)
    extra_volumes = []
    if hybrid:
        from .hybrid_stack import add_hybrid
        extra_volumes = add_hybrid(root, state, project, services, bind)
    for name, service in services.items():
        service.setdefault("networks", ["business"])
        service["labels"] = label.copy()
        service.setdefault("mem_limit", "1g" if name in ("mysql", "rabbitmq", "portal", "admin") else "512m")
    return {"name": project, "services": services,
            "networks": {"business": {"internal": True, "labels": label}, "edge": {"labels": label}},
            "volumes": {k: {"labels": label} for k in ["mysql_data", "rabbit_data", "mongo_data", "agent_data", *extra_volumes]}}


def prepare(root: Path, state: Path, *, tracing: bool = False, hybrid: bool = False) -> dict:
    state = validate_state(root, state)
    required = [root / f"services/commerce/mall-{s}/target/mall-{s}-1.0-SNAPSHOT.jar" for s in ("portal", "admin")]
    required.append(root / "apps/web/dist/index.html")
    if any(not p.is_file() for p in required):
        raise ValueError("build the Java jars and frontend before running business E2E")
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    project = "shop-e2e-" + secrets.token_hex(16)
    write_private(state / "owner.json", json.dumps({"project": project, "root": str(root.resolve()), "scope": "synthetic-business-e2e-v1", "tracing": tracing, "hybrid": hybrid}))
    values = {f"SHOP_AGENT_STACK_{key}": secrets.token_urlsafe(48) for key in (
        "DB_PASSWORD", "DB_ROOT_PASSWORD", "MQ_PASSWORD", "PORTAL_JWT_SECRET", "ADMIN_JWT_SECRET", "BOOTSTRAP_ADMIN_PASSWORD", "BOOTSTRAP_SERVICE_PASSWORD")}
    write_private(state / ".env", "\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    write_private(state / "accounts.json", json.dumps([
        {"role": role, "username": "shop_agent_stack_" + role.lower(), "password": values[f"SHOP_AGENT_STACK_BOOTSTRAP_{role}_PASSWORD"]} for role in ("ADMIN", "SERVICE")]))
    write_private(state / "agent-key", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    if hybrid:
        write_private(state / "index-key", secrets.token_hex(32))
        (state / "index-key").chmod(0o644)
        (state / "accounts.json").chmod(0o644)  # parent 0700; direct mount for UID 10001
    # Parent is 0700 on the host; Docker mounts this file directly for UID 10001.
    (state / "agent-key").chmod(0o644)
    (state / "init").mkdir()
    sources = sorted((root / "deploy/mysql/init").glob("*.sql")) + sorted((root / "deploy/mysql/migrations").glob("*.sql"))
    if len(sources) < 10:
        raise ValueError("missing schema or migrations")
    for index, source in enumerate(sources):
        shutil.copyfile(source, state / "init" / f"{index:03d}-{source.name}")
    (state / "artifacts").mkdir()
    (state / "receipts").mkdir()
    spec = compose(root, state, project, tracing=tracing, hybrid=hybrid)
    write_private(state / "compose.json", json.dumps(spec, indent=2))
    return spec


def add_tracing(root: Path, services: dict, bind) -> None:
    """Collector and Tempo stay on this test's internal network; no host trace ports."""
    services["tempo"] = {
        "image": "grafana/tempo:3.0.3", "command": ["-config.file=/etc/tempo/config.yaml", "-target=all"],
        "user": "10001:10001", "read_only": True, "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [bind(root / "deploy/observability/tempo.yaml", "/etc/tempo/config.yaml")],
        "tmpfs": ["/var/tempo:rw,uid=10001,gid=10001,mode=700,size=268435456", "/tmp:rw,size=16777216"],
    }
    services["otel-collector"] = {
        "image": "otel/opentelemetry-collector-contrib:0.161.0",
        "command": ["--config=/etc/otelcol/config.yaml"], "depends_on": ["tempo"],
        "read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "volumes": [bind(root / "deploy/observability/otel-collector.yaml", "/etc/otelcol/config.yaml")],
    }
    for name in ("agent", "commerce-mcp", "portal", "admin"):
        services[name]["environment"].update({
            "SHOP_AGENT_STACK_OTEL_ENABLED": "true", "SHOP_AGENT_STACK_OTEL_EXPORTER": "otlp",
            "SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT": "http://otel-collector:4318/v1/traces",
            "SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO": "1",  # Test only; production defaults to 0.1.
        })
    services["commerce-mcp"]["command"][1] = "shop_agent_stack.mcp_observed:app"
    services["commerce-mcp"]["environment"]["SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL"] = "true"
    services["portal"]["environment"]["SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL"] = "true"
    for side in ("portal", "admin"):
        services[side]["environment"]["SHOP_AGENT_STACK_OTEL_SERVICE"] = "shop-commerce-" + side
