"""Fresh-state hosted acceptance, not a command to run against someone's demo data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from . import runtime
from .state import ROOT, init, sha, write_private


def request(base: str, path: str, *, data=None, form=False, bearer="", method=None):
    headers = {"Authorization": bearer}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        body = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    with response:
        return response.status, json.load(response)


def expect(condition: bool, stage: str):
    if not condition:
        raise runtime.DemoError(stage)


def business(url, path, **kwargs):
    status, body = request(url, path, **kwargs)
    expect(status == 200 and body.get("code") == 200, "business request rejected")
    return body["data"]


def agent(url, path, bearer, **kwargs):
    status, body = request(url, "/api/agent" + path, bearer=bearer, **kwargs)
    expect(status == 200, "Agent request rejected")
    return body


def run(retrieval: str, output: Path) -> int:
    # Create only a NEW private state; never accept an existing demo from the caller.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    owner = init(ROOT, output, {"retrieval": retrieval, "port": port, "fixture": False, "model_network": False})
    (output / "artifacts").mkdir()
    report = {"scope": "M1.4a-persistent-local-demo", "status": "failed", "retrieval": retrieval,
              "live_llm_called": False, "public_deployment": False, "cleanup_verified": False, "checks": []}
    code = 1
    try:
        source = subprocess.check_output(["git", "rev-parse", "HEAD", "HEAD^{tree}"], cwd=ROOT, text=True).split()
        report["checkout_sha"], report["tree_sha"] = source
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True)
        expect(not dirty.strip(), "smoke requires a clean source checkout")
        report["snapshot_sha256"] = sha(output / "manifest.json")
        # Initialization must be non-destructive and refuse repeated init before Docker.
        before = sha(output / ".env")
        try:
            init(ROOT, output, owner["options"])
        except FileExistsError:
            pass
        else:
            raise runtime.DemoError("repeated init unexpectedly accepted")
        expect(sha(output / ".env") == before, "repeated init changed credentials")
        report["checks"].append("reinitialization-refused-without-secret-change")
        ready = runtime.up(output)
        expect(ready["ready"], "first startup not ready")
        report["checks"].append("proxy-and-application-startup")
        if retrieval == "hybrid":
            report["hybrid_ready"] = ready["hybrid"]
        url = ready["url"]
        accounts = json.loads((output / "accounts.json").read_text())
        for account in accounts:
            credentials = {k: account[k] for k in ("username", "password")}
            business(url, "/api/admin/admin/login", data=credentials)
        user = {"username": "demo_" + uuid4().hex[:12], "password": uuid4().hex + "Aa9!"}
        telephone = "000" + str(secrets.randbelow(10**8)).zfill(8)
        otp = business(url, "/api/portal/sso/getAuthCode?telephone=" + telephone)
        business(url, "/api/portal/sso/register", data={**user, "telephone": telephone, "authCode": otp}, form=True)
        login = business(url, "/api/portal/sso/login", data=user, form=True)
        token = login["tokenHead"] + login["token"]
        providers = agent(url, "/providers", token)
        expect(all(not v["configured"] for v in providers), "unexpected configured generation provider")
        expect(not any(v["id"] == "fixture" and v["configured"] for v in providers), "silent fixture enablement")
        session = agent(url, "/sessions", token, data={})
        status, _ = request(url, f"/api/agent/sessions/{session['id']}/runs", bearer=token,
                            data={"message": "demo unconfigured check", "provider": "fixture", "request_id": str(uuid4())})
        expect(status == 503, "unconfigured model fell back")
        report["checks"].append("no-model-and-no-fixture-fallback")
        business(url, "/api/portal/cart/add", bearer=token,
                 data={"productId": 1, "productSkuId": 1, "quantity": 1, "price": 0.01, "productCategoryId": 1})
        cart = business(url, "/api/portal/cart/list", bearer=token)
        expect(len(cart) == 1, "persistent cart missing")
        agent(url, "/settings", token, data={"nickname": "Persistent synthetic demo", "compact": True})
        # Saving/decrypting a synthetic key uses no outbound connection. Never test a provider.
        agent(url, "/settings/providers/custom", token,
              data={"model": "not-a-live-model", "base_url": "https://model.example.invalid", "api_key": "synthetic-" + uuid4().hex})
        metadata = {k: sha(output / k) for k in (".env", "agent-key", "accounts.json", "manifest.json")}
        before_volumes = runtime.volume_identity(runtime.resources(output, owner["project"])["volume"])
        runtime.stop(output)
        stopped = runtime.status(output)
        expect(not stopped["ready"] and all(v["state"] == "exited" for v in stopped["services"]), "stop did not stop services")
        resumed = runtime.up(output)
        expect(resumed["ready"], "restart not ready")
        # Also test repeated up. It must not rotate credentials, reseed, or recreate volumes.
        runtime.up(output)
        expect(before_volumes == runtime.volume_identity(runtime.resources(output, owner["project"])["volume"]), "volume identity changed")
        expect(metadata == {k: sha(output / k) for k in metadata}, "snapshot/credentials changed")
        for account in accounts:
            business(url, "/api/admin/admin/login", data={k: account[k] for k in ("username", "password")})
        business(url, "/api/portal/sso/login", data=user, form=True)
        expect(business(url, "/api/portal/cart/list", bearer=token) == cart, "MySQL cart not retained")
        expect(agent(url, "/sessions/" + session["id"], token)["id"] == session["id"], "SQLite session not retained")
        expect(agent(url, "/settings", token)["nickname"] == "Persistent synthetic demo", "preferences not retained")
        public_key = agent(url, "/settings/providers/custom", token)
        expect(public_key["has_key"] and "api_key" not in public_key, "encrypted configuration not readable or secret disclosed")
        report["checks"] += ["stop-and-start-retain-volume-identities", "idempotent-up", "staff-and-customer-login-retained",
                             "mysql-cart-retained", "sqlite-session-and-preferences-retained", "encrypted-model-setting-retained-without-network"]
        report["service_count"] = len(resumed["services"])
        report["volume_count"] = len(before_volumes)
        report["status"] = "passed"
        code = 0
    except Exception as exc:
        report["status"] = "failed"
        report["failure_type"] = type(exc).__name__
        if isinstance(exc, runtime.DemoError):
            report["failure_stage"] = str(exc)  # controlled stage names, not service payloads
        from tools.shop_e2e.__main__ import redact
        logs = sorted((output / "logs").glob("*.log"))
        if logs:
            write_private(output / "artifacts/failure-excerpt.txt", redact(output, logs[-1].read_text()[-12000:]))
    finally:
        try:
            runtime.destroy(output, owner["project"])
            report["cleanup_verified"] = True
        except Exception as exc:
            report["cleanup_failure_type"] = type(exc).__name__
            report["status"] = "failed"
            code = 1
        write_private(output / "artifacts/evidence.json", json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval", choices=("bm25", "hybrid"), required=True)
    args = parser.parse_args()
    return run(args.retrieval, ROOT / (".local/demo-smoke-" + args.retrieval))


if __name__ == "__main__":
    raise SystemExit(main())
