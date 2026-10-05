"""Explicitly enabled Java-authoritative demo import. No direct database writes or model calls."""
from __future__ import annotations

import json
from pathlib import Path
import re
import urllib.error
import urllib.request

from . import runtime
from .state import ROOT, load, locked, sha, write_private
from .seed_data import bundle

ENDPOINT = "/api/admin/shop_agent_stack/demo-imports"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward login credentials or a bearer token to a redirect.


class AdminAPI:
    def __init__(self, port: int, credentials: dict):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("invalid demo port")
        self.url = f"http://127.0.0.1:{port}"
        self.token = ""
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        result = self.call("/api/admin/admin/login", {k: credentials[k] for k in ("username", "password")})
        self.token = result["tokenHead"] + result["token"]
        identity = self.call("/api/admin/shop_agent_stack/me")
        if identity.get("role") != "ADMIN":
            raise ValueError("seed import requires a current administrator")

    def call(self, path: str, data=None):
        if not path.startswith("/api/admin/") or "?" in path and not path.startswith(ENDPOINT + "/preview?action="):
            raise ValueError("unsupported demo import path")
        request = urllib.request.Request(self.url + path,
            data=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": self.token})
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if response.status != 200 or len(raw) > 2 * 1024 * 1024:
                    raise ValueError("invalid import response")
                body = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise runtime.DemoError("demo import HTTP failed; no automatic mutation retry") from None
        if not isinstance(body, dict) or body.get("code") != 200 or body.get("data") is None:
            raise runtime.DemoError("demo import rejected; review the plan, expiry, role and catalog conflicts")
        return body["data"]


def _api(state: Path):
    owner, spec = load(ROOT, state)
    if not owner["options"].get("seed_import", False):
        raise ValueError("initialize a NEW demo with --enable-seed-import; existing snapshots are never modified")
    runtime.local_engine(state)
    life = runtime._life(state)
    current = runtime.resources(state, owner["project"])
    runtime.require_volumes(life, current)
    if life["phase"] != "ready" or not runtime._status(state, owner, spec, current)["ready"]:
        raise ValueError("start this demo before previewing or applying a seed import")
    credentials = next(a for a in json.loads((state / "accounts.json").read_text()) if a["role"] == "ADMIN")
    return owner, AdminAPI(owner["options"]["port"], credentials)


def preview(state: Path, *, publish: bool = False) -> dict:
    with locked(ROOT, state):
        owner, api = _api(state)
        payload = bundle(ROOT, state)
        plan = api.call(ENDPOINT + "/preview?action=" + ("PUBLISH" if publish else "SEED"), payload)
        if not re.fullmatch(r"[0-9a-f-]{36}", str(plan.get("id", ""))) or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("confirmation_hash", ""))):
            raise ValueError("malformed server preview")
        directory = state / "imports"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (plan["id"] + ".json")
        write_private(path, json.dumps({"project": owner["project"], "snapshot": sha(state / "manifest.json"), "plan": plan}, ensure_ascii=False, indent=2))
        return {"preview_id": plan["id"], "action": plan["action"], "confirmation": plan["confirmation_hash"],
                "expires_at": plan["expires_at"], "precondition": plan["precondition"],
                "products": len(payload["products"]), "policies": len(payload["policies"]),
                "review_file": str(path), "business_mutations": 0,
                "notice": "SEED creates saleable synthetic products and policy DRAFTS only; PUBLISH requires a separate reviewed confirmation."}


def apply(state: Path, preview_id: str, confirmation: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", preview_id) or not re.fullmatch(r"[0-9a-f]{64}", confirmation):
        raise ValueError("exact preview ID and confirmation digest required")
    with locked(ROOT, state):
        owner, api = _api(state)
        path = state / "imports" / (preview_id + ".json")
        saved = json.loads(path.read_text())
        if saved.get("project") != owner["project"] or saved.get("snapshot") != sha(state / "manifest.json"):
            raise ValueError("preview belongs to another demo snapshot")
        plan = api.call(ENDPOINT + "/" + preview_id)
        # Status/result may change after a successful response was lost; receipt replay is safe.
        for key in ("id", "action", "bundle_hash", "confirmation_hash", "precondition", "review", "expires_at"):
            if plan.get(key) != saved["plan"].get(key):
                raise ValueError("preview changed; review a new server preview")
        if confirmation != plan["confirmation_hash"]:
            raise ValueError("confirmation does not match the reviewed preview")
        result = api.call(ENDPOINT + "/" + preview_id + "/apply", {"confirmation": confirmation})
        if result.get("status") != "APPLIED" or result.get("previewId") != preview_id or result.get("bundleHash") != plan["bundle_hash"]:
            raise ValueError("apply receipt missing; query the same preview before retrying")
        return result
