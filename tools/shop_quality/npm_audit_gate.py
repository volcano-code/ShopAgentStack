"""Run npm's full lockfile audit; fail closed on high/critical or unavailable reports.

Exit codes: 0 = valid report below high, 1 = high/critical advisories,
2 = execution, schema, evidence or input failure. No raw npm output is published.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SEVERITIES = ("info", "low", "moderate", "high", "critical")
MAX_REPORT_BYTES = 4 * 1024 * 1024
REGISTRY = "https://registry.npmjs.org"
PACKAGE = r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+"
NODE = re.compile(rf"(?:node_modules/{PACKAGE}/)*node_modules/{PACKAGE}")


class AuditError(ValueError):
    """Only fixed, non-sensitive rejection codes belong in this exception."""


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise AuditError("non_json_constant")


def _json(raw: bytes) -> dict:
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_REPORT_BYTES:
            raise AuditError("invalid_report_size")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(value, dict):
            raise AuditError("invalid_report_object")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError("invalid_json") from None


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= 1000000


def lock_nodes(raw: bytes) -> frozenset[str]:
    lock = _json(raw)
    packages = lock.get("packages")
    if type(lock.get("lockfileVersion")) is not int or lock["lockfileVersion"] != 3:
        raise AuditError("unsupported_lockfile")
    if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
        raise AuditError("invalid_lockfile")
    nodes = frozenset(key for key in packages if key)
    if not nodes or any(not NODE.fullmatch(node) or not isinstance(packages[node], dict) for node in nodes):
        raise AuditError("unsupported_lock_layout")
    return nodes


def evaluate(raw: bytes, npm_exit: int, nodes: frozenset[str]) -> dict:
    """Validate npm audit v2 against the exact lockfile; never default missing counts to zero.

    All dependency classes must be audited. Counters are package-vulnerability
    counts (npm v2 semantics), not incident counts or proof of exploitability.
    This pure function performs no network requests; the CLI does the live audit.
    """
    result: dict = {"passed": False, "status": "unavailable", "exit_code": 2,
                   "counts": None, "affected_packages": [], "reasons": []}
    try:
        if type(npm_exit) is not int or npm_exit not in (0, 1):
            raise AuditError("npm_execution_failed")
        report = _json(raw)
        if "error" in report:
            raise AuditError("npm_registry_error")
        if type(report.get("auditReportVersion")) is not int or report["auditReportVersion"] != 2:
            raise AuditError("unsupported_audit_schema")
        vulnerabilities = report.get("vulnerabilities")
        metadata = report.get("metadata")
        if not isinstance(vulnerabilities, dict) or not isinstance(metadata, dict):
            raise AuditError("incomplete_audit_report")
        counts = metadata.get("vulnerabilities")
        dependencies = metadata.get("dependencies")
        if (not isinstance(counts, dict) or set(counts) != {*SEVERITIES, "total"}
                or not all(_integer(value) for value in counts.values())):
            raise AuditError("invalid_severity_counts")
        if (not isinstance(dependencies, dict)
                or not {"prod", "dev", "optional", "peer", "peerOptional", "total"} <= set(dependencies)
                or not all(_integer(value) for value in dependencies.values())
                or dependencies["total"] != len(nodes)):
            raise AuditError("dependency_inventory_mismatch")
        histogram: Counter = Counter()
        findings = []
        for name, item in vulnerabilities.items():
            if (not re.fullmatch(PACKAGE, name) or not isinstance(item, dict)
                    or item.get("name") != name or item.get("severity") not in SEVERITIES
                    or type(item.get("isDirect")) is not bool):
                raise AuditError("invalid_vulnerability")
            locations = item.get("nodes")
            if (not isinstance(locations, list) or not locations
                    or any(not isinstance(node, str) or node not in nodes
                           or not node.endswith("node_modules/" + name) for node in locations)
                    or len(set(locations)) != len(locations)):
                raise AuditError("vulnerability_not_in_lockfile")
            via = item.get("via")
            if not isinstance(via, list) or not via:
                raise AuditError("missing_advisory_evidence")
            for advisory in via:
                if isinstance(advisory, str):
                    if advisory not in vulnerabilities or advisory == name:
                        raise AuditError("invalid_advisory_reference")
                elif isinstance(advisory, dict):
                    severity = advisory.get("severity")
                    if (severity not in SEVERITIES or type(advisory.get("source")) is not int
                            or not 0 < advisory["source"] <= 2**53 - 1
                            or SEVERITIES.index(severity) > SEVERITIES.index(item["severity"])):
                        raise AuditError("invalid_advisory_severity")
                else:
                    raise AuditError("invalid_advisory_evidence")
            histogram[item["severity"]] += 1
            findings.append({"package": name, "severity": item["severity"], "nodes": sorted(locations)})
        if (counts["total"] != sum(counts[key] for key in SEVERITIES)
                or counts["total"] != len(vulnerabilities)
                or any(counts[key] != histogram[key] for key in SEVERITIES)):
            raise AuditError("severity_count_mismatch")
        blocked = bool(counts["high"] or counts["critical"])
        if npm_exit != int(blocked):
            raise AuditError("npm_exit_mismatch")
        result.update(passed=not blocked, status="advisories" if blocked else "pass",
                      exit_code=int(blocked), counts=counts,
                      affected_packages=sorted(findings, key=lambda item: item["package"]),
                      reasons=["high_or_critical_advisory"] if blocked else [])
    except AuditError as exc:
        result["reasons"] = [str(exc)]
    return result


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def markdown(report: dict) -> str:
    counts = report["counts"]
    lines = ["# Frontend dependency audit", "", f"Status: **{report['status']}**", "",
             "Scope: all locked npm dependencies, including development dependencies.",
             "Threshold: high/critical. Not a full application security assessment.", "",
             "| Severity | Packages |", "|---|---:|"]
    lines += [f"| {severity} | {counts[severity] if counts is not None else 'unknown'} |" for severity in SEVERITIES]
    lines += ["", "Rejection codes: " + (", ".join(report["reasons"]) or "none"), "",
              "Lockfile SHA256: `" + report.get("lockfile_sha256", "unavailable") + "`", "",
              "Raw npm messages, advisory titles, URLs and credentials are not included.", ""]
    return "\n".join(lines)


def run(project: Path, out: Path, timeout: int = 120) -> int:
    # Refuse replacing earlier evidence, including evidence from a failed attempt.
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("npm audit: evidence destination unavailable", file=sys.stderr)
        return 2
    report: dict = {"schema": "shop-npm-audit/v1", "passed": False, "status": "unavailable",
                   "exit_code": 2, "counts": None, "affected_packages": [], "reasons": [],
                   "registry": REGISTRY, "threshold": "high", "npm_command_executed": False,
                   "scope": "full_lockfile_including_dev_optional_peer",
                   "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    try:
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise AuditError("invalid_timeout")
        lock = project / "package-lock.json"
        manifest = project / "package.json"
        if lock.is_symlink() or manifest.is_symlink() or (project / "npm-shrinkwrap.json").exists():
            raise AuditError("unsupported_project_input")
        lock_raw = lock.read_bytes()
        manifest_raw = manifest.read_bytes()
        nodes = lock_nodes(lock_raw)
        _json(manifest_raw)
        report.update(lockfile_sha256=_sha(lock_raw), package_json_sha256=_sha(manifest_raw),
                      gate_sha256=_sha(Path(__file__).read_bytes()), locked_dependencies=len(nodes))
        npm = shutil.which("npm")
        if not npm:
            raise AuditError("npm_not_available")
        # CLI inclusion flags override NODE_ENV=production and inherited omit settings.
        command = [npm, "audit", "--json", "--audit-level=high", "--package-lock-only",
                   "--include=prod", "--include=dev", "--include=optional", "--include=peer",
                   "--ignore-scripts", "--workspaces=false", "--registry=" + REGISTRY,
                   "--fetch-retries=0", "--fetch-timeout=30000", "--no-fund"]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            report["npm_command_executed"] = True
            completed = subprocess.run(command, cwd=project, stdout=stdout, stderr=stderr,
                                       timeout=timeout, check=False)
            stdout.seek(0)
            raw = stdout.read(MAX_REPORT_BYTES + 1)
        # No registry response, stdout or stderr is copied to public evidence.
        report["npm_exit_code"] = completed.returncode
        report["report_sha256"] = _sha(raw) if len(raw) <= MAX_REPORT_BYTES else None
        report.update(evaluate(raw, completed.returncode, nodes))
        if lock.read_bytes() != lock_raw or manifest.read_bytes() != manifest_raw:
            raise AuditError("project_changed_during_audit")
    except (AuditError, OSError, subprocess.TimeoutExpired) as exc:
        reason = str(exc) if isinstance(exc, AuditError) else (
            "npm_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "audit_io_error")
        report.update(passed=False, status="unavailable", exit_code=2, counts=None,
                      affected_packages=[], reasons=[reason])
    try:
        (out / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        text = markdown(report)
        (out / "audit.md").write_text(text, encoding="utf-8")
        if summary := os.getenv("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text)
    except OSError:
        print("npm audit: evidence write failed", file=sys.stderr)
        return 2
    print("npm audit: " + report["status"] + " (" + (", ".join(report["reasons"]) or "below high threshold") + ")")
    return report["exit_code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    return run(args.project, args.out, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
