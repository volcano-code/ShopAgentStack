"""Run only against a disposable local Docker context; always remove this test stack."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import evidence as ev
from .stack import LABEL, PROJECT, compose, prepare, validate_state

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ["docker", "--context", "default"]


class StageError(RuntimeError):
    pass


def environment(extra: dict | None = None) -> dict:
    # Compose shell variables override env-file values: do not inherit deployment credentials.
    result = {k: v for k, v in os.environ.items()
              if not k.startswith(("SHOP_AGENT_STACK_", "SHOP_E2E_", "COMPOSE_", "DOCKER_"))}
    result.update(extra or {})
    return result


def redact(state: Path, text: str) -> str:
    """Bounded failure excerpt; exclude generated secrets and opaque runtime credentials."""
    for line in (state / ".env").read_text().splitlines():
        if "=" in line:
            value = line.split("=", 1)[1]
            if value:
                text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    text = re.sub(r"[A-Za-z0-9_+/=-]{32,}(?:\.[A-Za-z0-9_+/=-]+)*", "[OPAQUE]", text)
    return text[-6000:]


def command(state: Path, name: str, args: list[str], *, timeout: int = 300,
            input_text: str | None = None, extra: dict | None = None, cwd: Path = ROOT) -> str:
    print(f"business-e2e: {name}", flush=True)
    try:
        result = subprocess.run(args, cwd=cwd, env=environment(extra), input=input_text,
                                text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StageError(name + ": " + type(exc).__name__) from None
    # Raw output may include synthetic credentials or response bodies. Never upload it.
    (state / f"private-{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise StageError(f"{name}: exit {result.returncode}")
    return result.stdout


def load_state(state: Path) -> tuple[str, list[str]]:
    validate_state(ROOT, state)
    owner = json.loads((state / "owner.json").read_text())
    project = owner.get("project", "")
    if owner.get("scope") != "synthetic-business-e2e-v1" or owner.get("root") != str(ROOT) or not PROJECT.fullmatch(project):
        raise ValueError("not an owned test state")
    tracing = owner.get("tracing", False)
    if type(tracing) is not bool:
        raise ValueError("invalid test tracing mode")
    if json.loads((state / "compose.json").read_text()) != compose(ROOT, state, project, tracing=tracing):
        raise ValueError("test Compose changed after preparation")
    return project, [*DOCKER, "compose", "--project-name", project, "--env-file", str(state / ".env"), "-f", str(state / "compose.json")]


def resources(state: Path, project: str) -> dict[str, list[str]]:
    result = {}
    for kind, args in [("container", ["ps", "-aq"]), ("volume", ["volume", "ls", "-q"]), ("network", ["network", "ls", "-q"])]:
        raw = command(state, "list-" + kind, [*DOCKER, *args, "--filter", "label=com.docker.compose.project=" + project], timeout=30)
        result[kind] = raw.split()
    return result


def cleanup(state: Path) -> None:
    project, base = load_state(state)
    current = resources(state, project)
    for kind, ids in current.items():
        if ids:
            inspected = json.loads(command(state, "inspect-" + kind, [*DOCKER, kind, "inspect", *ids], timeout=30))
            for item in inspected:
                labels = item.get("Config", {}).get("Labels", {}) if kind == "container" else item.get("Labels", {})
                if (labels or {}).get(LABEL) != project:
                    raise ValueError("refusing cleanup of a foreign resource")
    command(state, "cleanup", [*base, "down", "--volumes", "--remove-orphans"], timeout=120)
    if any(resources(state, project).values()):
        raise StageError("owned test resources remain")


def public_images(state: Path, base: list[str]) -> list[dict]:
    ids = command(state, "container-ids", [*base, "ps", "-q"], timeout=30).split()
    if not ids:
        raise StageError("no running business containers")
    data = json.loads(command(state, "images-private", [*DOCKER, "container", "inspect", *ids], timeout=30))
    return [{"service": v["Config"]["Labels"]["com.docker.compose.service"],
             "image_id": v["Image"], "configured_image": v["Config"]["Image"]} for v in data]


def run(state: Path, *, tracing: bool = False) -> int:
    if tracing:
        prepare(ROOT, state, tracing=True)
    else:
        prepare(ROOT, state)
    project, base = load_state(state)
    report = {"scope": "M1.3a-isolated-synthetic-business", "status": "failed", "project": project,
              "live_model_verified": False, "real_payment_verified": False,
              "hybrid_retrieval_verified": False, "distributed_trace_verified": False,
              "cleanup_verified": False, "stages": []}
    if tracing:
        report["scope"] = "M1.3b-isolated-synthetic-business-tracing"
    code = 1
    try:
        # This fails instead of adopting another stack, even in the astronomically unlikely collision.
        if any(resources(state, project).values()):
            raise StageError("isolated project already exists")
        source = command(state, "source", ["git", "rev-parse", "HEAD", "HEAD^{tree}"], timeout=10).split()
        report["checkout_sha"], report["tree_sha"] = source
        dirty = command(state, "worktree-check", ["git", "status", "--porcelain", "--untracked-files=all"], timeout=10)
        if dirty.strip():
            raise StageError("worktree-check: commit or remove untracked source changes before acceptance")
        report["worktree_clean"] = True
        inputs = [*sorted((state / "init").glob("*.sql")), ROOT / "services/agent/requirements.lock", ROOT / "apps/web/package-lock.json",
                  *[ROOT / f"services/commerce/mall-{s}/target/mall-{s}-1.0-SNAPSHOT.jar" for s in ("portal", "admin")]]
        report["input_sha256"] = {str(p.relative_to(state) if p.is_relative_to(state) else p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        command(state, "config", [*base, "config", "--quiet"], timeout=30)
        command(state, "image-build", [*base, "build", "commerce-mcp"], timeout=600)
        command(state, "startup", [*base, "up", "-d", "--wait", "--wait-timeout", "240"], timeout=600)
        report["stages"].append("healthy-business-stack")
        report["images"] = public_images(state, base)
        address = command(state, "web-port", [*base, "port", "web", "80"], timeout=30).strip()
        if not re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", address) or int(address.split(":")[1]) > 65535:
            raise StageError("unexpected published address")
        # The two selected tests exercise real MCP and Java, not an ASGI test double.
        command(state, "mcp", [*base, "exec", "-T", "-e", "SHOP_AGENT_STACK_INTEGRATION=true", "agent", "python", "-m", "pytest",
                    "tests/test_mcp_integration.py", "-q", "-p", "no:cacheprovider", "-k",
                    "real_mcp_confirmation_ownership_and_replay or cancelled_operation_and_invalid_execution_grant",
                    "--junitxml=/tmp/m13-mcp.xml"], timeout=180)
        command(state, "copy-mcp", [*base, "cp", "agent:/tmp/m13-mcp.xml", str(state / "mcp.xml")], timeout=30)
        report["mcp"] = ev.test_report(state / "mcp.xml", ev.MCP_TESTS)
        report["stages"].append("real-mcp-contracts")
        command(state, "browser", ["npm", "exec", "--no", "--", "playwright", "test", "--config", "playwright.business.config.ts"],
                cwd=ROOT / "apps/web", timeout=480, extra={"SHOP_E2E_URL": "http://" + address, "SHOP_E2E_STATE": str(state), "SHOP_E2E_TRACING": str(tracing).lower()})
        report["browser"] = ev.test_report(state / "browser.xml", ev.BROWSER_TESTS)
        report["stages"].append("browser-confirmation-and-refund")
        for name, refunded in [("refunded", True), ("cancelled", False)]:
            data = ev.receipt(state / "receipts" / (name + ".json"), refunded)
            raw = command(state, "readback-" + name, [*base, "exec", "-T", "mysql", "sh", "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mysql --batch --skip-column-names --raw --default-character-set=utf8mb4 -ushop_agent_stack -Dshop_agent_stack'],
                input_text=ev.readback_sql(data), timeout=30)
            report[name] = ev.verify_readback(data, json.loads(raw.strip()), refunded)
        report["stages"].append("independent-mysql-readback")
        if tracing:
            from .traces import collect
            report["traces"] = collect(state, base, command, report["refunded"]["case_id"])
            report["distributed_trace_verified"] = True
            report["distributed_trace_scope"] = "agent-mcp-java-preview-and-approval-outbox-rabbitmq-refund"
            report["stages"].append("real-business-tempo-readback")
        code = 0
    except (StageError, ValueError, OSError, KeyError, TypeError, ev.ET.ParseError) as exc:
        # Fixed class/stage names only. Never dump a provider response, token or SQL result.
        report["failure_type"] = type(exc).__name__
        if isinstance(exc, StageError):
            report["failure_stage"] = str(exc)
        details = str(exc)
        if isinstance(exc, StageError):
            log = state / ("private-" + str(exc).split(":", 1)[0] + ".log")
            if log.is_file():
                details += "\n" + log.read_text()[-12000:]
        (state / "artifacts" / "failure-excerpt.txt").write_text(redact(state, details), encoding="utf-8")
        try:
            service_log = command(state, "failure-services", [*base, "logs", "--no-color", "--tail", "20", "portal", "admin", "mysql", "agent", "commerce-mcp"], timeout=30)
            (state / "artifacts" / "service-excerpt.txt").write_text(redact(state, service_log), encoding="utf-8")
        except (StageError, OSError):
            pass
        print("business-e2e failed; private diagnostics remain in the test state", file=sys.stderr)
    finally:
        try:
            cleanup(state)
            report["cleanup_verified"] = True
        except (StageError, ValueError, OSError, KeyError, TypeError):
            code = 1
            report["cleanup_verified"] = False
        report["status"] = "passed" if code == 0 else "failed"
        (state / "artifacts" / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "cleanup_verified": report["cleanup_verified"]}))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "cleanup"])
    parser.add_argument("--state", type=Path, default=ROOT / ".local/business-e2e")
    parser.add_argument("--tracing", action="store_true", help="require real business trace readback from an isolated Collector/Tempo")
    args = parser.parse_args(argv)
    state = args.state.absolute()
    try:
        validate_state(ROOT, state)
        if args.action == "cleanup":
            if not state.exists():
                return 0
            cleanup(state)
            return 0
        return run(state, tracing=args.tracing)
    except (OSError, ValueError, StageError, KeyError, TypeError) as exc:
        print("business-e2e refused: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
