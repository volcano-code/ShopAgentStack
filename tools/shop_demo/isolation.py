"""Read-only comparison of validated local-demo snapshots, NOT runtime isolation.

Reuse state.load; never instantiate Agent Store, call Docker, start services, read
business databases or call models. Results contain codes and argument positions,
not credentials, paths, project names or service configuration. Only the existing
shop-local-demo format is accepted; this is not an arbitrary Compose auditor.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import combinations
import json
from pathlib import Path
import re

from .state import ROOT, load, validate_state


@dataclass(frozen=True)
class Inventory:
    state: Path
    project: str
    volumes: frozenset[str]
    networks: frozenset[str]
    ports: frozenset[tuple[str, int]]
    binds: tuple[tuple[Path, bool], ...]


def _name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}", value):
        raise ValueError("unsupported resource name")
    return value


def _resources(spec: dict, kind: str) -> frozenset[str]:
    """Explicit names are NOT project-scoped. Refuse opaque storage drivers."""
    definitions = spec.get(kind, {})
    if not isinstance(definitions, dict):
        raise ValueError("unsupported resource declaration")
    names = set()
    for key, config in definitions.items():
        _name(key)
        config = {} if config is None else config
        allowed = {"name", "external", "labels"} | ({"internal"} if kind == "networks" else set())
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("unsupported resource attributes")
        if type(config.get("external", False)) is not bool:
            raise ValueError("unsupported external resource")
        default = key if config.get("external") else f"{spec['name']}_{key}"
        names.add(_name(config.get("name", default)))
    return frozenset(names)


def inventory(state: Path, spec: dict) -> Inventory:
    """Pure comparison input; the CLI only supplies state.load-validated specs."""
    project = _name(spec["name"])
    services = spec["services"]
    if not isinstance(services, dict) or not services:
        raise ValueError("missing services")
    ports, binds = set(), []
    for service in services.values():
        if not isinstance(service, dict) or any(k in service for k in
                ("network_mode", "volumes_from", "container_name", "extends", "devices")):
            raise ValueError("unsupported service isolation")
        for port in service.get("ports", []):
            if not isinstance(port, dict):
                raise ValueError("only generated long port syntax is supported")
            published, protocol = port.get("published"), port.get("protocol", "tcp")
            if type(published) is int:
                published = str(published)
            if not isinstance(published, str) or not published.isascii() or not published.isdigit():
                raise ValueError("unresolved port")
            number = int(published)
            if not 0 <= number <= 65535 or protocol not in ("tcp", "udp"):
                raise ValueError("invalid port")
            # Conservatively flag equal numbers even on different host addresses.
            # Published zero requests a dynamic port, whose assignment is unverified.
            if number:
                ports.add((protocol, number))
        for mount in service.get("volumes", []):
            if isinstance(mount, str):
                parts = mount.split(":")
                if len(parts) not in (2, 3) or parts[0] not in spec.get("volumes", {}) or not parts[1].startswith("/"):
                    raise ValueError("unsupported short mount")
                if len(parts) == 3 and parts[2] not in ("ro", "rw"):
                    raise ValueError("unsupported mount options")
            elif isinstance(mount, dict) and mount.get("type") == "bind":
                source, readonly = mount.get("source"), mount.get("read_only", False)
                if not isinstance(source, str) or "$" in source or not Path(source).is_absolute() or type(readonly) is not bool:
                    raise ValueError("unsupported bind")
                path = Path(source).resolve()
                if path in (Path("/var/run/docker.sock"), Path("/run/docker.sock")):
                    raise ValueError("docker socket is not a data mount")
                binds.append((path, readonly))
            else:
                raise ValueError("unsupported mount")
    return Inventory(state.resolve(), project, _resources(spec, "volumes"),
                     _resources(spec, "networks"), frozenset(ports), tuple(binds))


def _overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def compare(entries: list[Inventory]) -> list[dict]:
    issues = []
    for (i, left), (j, right) in combinations(enumerate(entries, 1), 2):
        codes = set()
        if _overlap(left.state, right.state):
            codes.add("STATE_PATH_OVERLAP")
        if left.project == right.project:
            codes.add("PROJECT_REUSED")
        if left.volumes & right.volumes:
            codes.add("VOLUME_REUSED")
        if left.networks & right.networks:
            codes.add("NETWORK_REUSED")
        if left.ports & right.ports:
            codes.add("PUBLISHED_PORT_OVERLAP")
        if any(_overlap(a, b) and not (a_ro and b_ro)
               for a, a_ro in left.binds for b, b_ro in right.binds):
            codes.add("WRITABLE_BIND_OVERLAP")
        # Even read-only access to the other environment's private state is refused.
        if any(_overlap(path, right.state) for path, _ in left.binds) or any(
                _overlap(path, left.state) for path, _ in right.binds):
            codes.add("CROSS_STATE_BIND")
        issues.extend({"code": code, "states": [i, j]} for code in sorted(codes))
    return issues


def audit(root: Path, states: list[Path]) -> dict:
    report = {"schema": "shop-demo-isolation-v1", "scope": "declared_local_demo_configuration",
              "configuration_disjoint": False, "runtime_verified": False,
              "states_requested": len(states), "states_validated": 0, "issues": []}
    if not 2 <= len(states) <= 8:
        report["issues"].append({"code": "STATE_COUNT_INVALID", "states": []})
        return report
    entries = []
    for index, path in enumerate(states, 1):
        try:
            selected = validate_state(root, path.absolute())
            # Lifecycle commands use an exclusive marker. Do not acquire a lock here:
            # this command is strictly read-only. This is not a race-free attestation.
            if (selected / "command.lock").exists():
                report["issues"].append({"code": "STATE_BUSY", "states": [index]})
                continue
            owner, spec = load(root, selected)
            if owner["project"] != spec["name"]:
                raise ValueError("project mismatch")
            entries.append(inventory(selected, spec))
            report["states_validated"] += 1
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            report["issues"].append({"code": "STATE_INVALID", "states": [index]})
    # Do not renumber a partial selection: argument positions must remain exact.
    if not report["issues"]:
        report["issues"] = compare(entries)
        report["configuration_disjoint"] = not report["issues"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", type=Path, required=True,
                        help="repeat for 2-8 existing local-demo snapshots in this checkout")
    args = parser.parse_args(argv)
    report = audit(ROOT, args.state)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["configuration_disjoint"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
