"""Bounded loopback health checks; readiness is NOT evidence of trace delivery."""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ENDPOINTS = {
    "collector": "http://127.0.0.1:13133/",
    "tempo": "http://127.0.0.1:13200/ready",
    "grafana": "http://127.0.0.1:13000/api/health",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(name: str, timeout: float) -> bool:
    """Never honor proxy environment, follow redirects, or log response bodies."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(ENDPOINTS[name], method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def wait_ready(timeout: float = 120.0) -> dict:
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("timeout must be finite and in (0, 600]")
    started = time.monotonic()
    deadline = started + timeout
    checks = {name: False for name in ENDPOINTS}
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        # All services must answer within the same pass; don't keep stale successes.
        checks = {name: False for name in ENDPOINTS}
        for name in ENDPOINTS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            checks[name] = probe(name, min(2.0, remaining))
        if all(checks.values()) and time.monotonic() <= deadline:
            return {"ready": True, "checks": checks, "attempts": attempts,
                    "claim_scope": "health_only_not_trace_delivery"}
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, remaining))
    return {"ready": False, "checks": checks, "attempts": attempts,
            "claim_scope": "health_only_not_trace_delivery"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
        parser.error("--timeout must be finite and in (0, 600]")
    # Reserve the evidence path BEFORE probing; never overwrite earlier failures.
    output = None
    try:
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            output = args.out.open("x", encoding="utf-8")
        result = wait_ready(args.timeout)
        text = json.dumps(result, sort_keys=True)
        if output is not None:
            output.write(text + "\n")
        print(text)
        return 0 if result["ready"] else 1
    except OSError as exc:
        print(json.dumps({"error": type(exc).__name__}))
        return 2
    finally:
        if output is not None:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())
