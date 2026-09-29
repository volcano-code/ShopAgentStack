"""Host orchestrator and fail-closed acceptance for the real isolated hybrid stack."""
from __future__ import annotations
import json
from pathlib import Path
import re

STAGES = ("initial", "revision_offline", "revision_online", "milvus_outage", "withdraw_offline", "recovered")
ONLINE = {"initial", "revision_online", "recovered"}
GENERATION = re.compile(r"shop_agent_stack_live_[a-f0-9]{12}_[0-9]+_[a-f0-9]{16}")


def ids(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"first", "staff", "draft", "revised"}:
        raise ValueError("incomplete policy identities")
    if any(type(v) is not int or not 0 < v < 2**53 for v in value.values()) or len(set(value.values())) != 4:
        raise ValueError("invalid policy identities")
    return value


def readback_sql(policy_ids: dict) -> str:
    p = ids(policy_ids)
    visible = "p.status='PUBLISHED' AND m.visibility='CUSTOMER' AND m.index_status='READY' AND m.effective_from<=NOW(6) AND (m.effective_to IS NULL OR m.effective_to>NOW(6))"
    properties = []
    for name, identifier in sorted(p.items()):
        properties.extend([f"'{name}_status',(SELECT status FROM shop_agent_stack_policy WHERE id={identifier})",
                           f"'{name}_version',(SELECT version FROM shop_agent_stack_policy WHERE id={identifier})",
                           f"'{name}_family',(SELECT family_id FROM shop_agent_stack_policy_meta WHERE policy_id={identifier})"])
    properties.extend([
        "'visible_clauses',(SELECT COUNT(*) FROM shop_agent_stack_policy_clause c JOIN shop_agent_stack_policy p ON p.id=c.policy_id JOIN shop_agent_stack_policy_meta m ON m.policy_id=p.id WHERE " + visible + ")",
        "'draft_clauses',(SELECT COUNT(*) FROM shop_agent_stack_policy_clause WHERE policy_id=" + str(p["draft"]) + ")",
        "'epoch',e.revision", "'job_status',j.status", "'collection',j.collection_name"])
    return "SELECT JSON_OBJECT(" + ",".join(properties) + ") FROM shop_agent_stack_knowledge_epoch e JOIN shop_agent_stack_policy_index_job j ON j.revision=e.revision WHERE e.id=1;"


def verify(receipts: list[dict], vector: dict, database: dict, config: dict, config_hash: str) -> dict:
    if len(receipts) != len(STAGES) or [r.get("stage") for r in receipts] != list(STAGES):
        raise ValueError("missing, repeated or out-of-order hybrid stage")
    policy_ids = ids(receipts[-1]["policy_ids"])
    for item in receipts:
        stage = item["stage"]
        if any(item.get(k) is not True for k in ("verified", "source_hashes_verified", "invisible_sources_denied")):
            raise ValueError("unverified policy evidence")
        if item["method"] != ("HYBRID_RRF_RERANK" if stage in ONLINE else "BM25") or item["degraded"] is not (stage not in ONLINE):
            raise ValueError("unexpected hybrid/degradation result")
        if type(item["epoch"]) is not int or item["epoch"] <= 0:
            raise ValueError("invalid catalog epoch")
        if item["policy_ids"] != {k: v for k, v in policy_ids.items() if stage != "initial" or k != "revised"}:
            raise ValueError("stage policy identities changed")
        if not isinstance(item["hits"], list) or len(item["hits"]) > 5:
            raise ValueError("invalid retrieval hit budget")
        forbidden = {policy_ids["staff"], policy_ids["draft"]}
        if stage != "initial": forbidden.add(policy_ids["first"])
        if stage in ("withdraw_offline", "recovered"): forbidden.add(policy_ids["revised"])
        hit_ids = set()
        for hit in item["hits"]:
            if set(hit) != {"policy_id", "version", "clause_no", "content_hash"}:
                raise ValueError("unexpected public evidence fields")
            if any(type(hit[k]) is not int or hit[k] <= 0 for k in ("policy_id", "version", "clause_no")):
                raise ValueError("invalid hit identity")
            if hit["policy_id"] in forbidden or not re.fullmatch(r"[0-9a-f]{64}", hit["content_hash"]):
                raise ValueError("invalid or invisible evidence")
            hit_ids.add(hit["policy_id"])
        if stage not in ("withdraw_offline", "recovered"):
            expected_id = policy_ids["first" if stage == "initial" else "revised"]
            if expected_id not in hit_ids:
                raise ValueError("current policy missing")
        if stage in ONLINE:
            generation = item["generation"]
            w = item["worker"]
            if not isinstance(generation, str) or not GENERATION.fullmatch(generation) or w["collection"] != generation:
                raise ValueError("wrong live collection")
            if w["epoch"] != item["epoch"] or w["model_revisions"] != config["model_revisions"]:
                raise ValueError("worker epoch/model mismatch")
        elif item["generation"] is not None:
            raise ValueError("fallback claimed a vector generation")
    initial, rev_off, rev_on, outage, withdrawn, final = receipts
    if any(initial.get(k) is not True for k in ("service_auth_verified", "stale_snapshots_denied", "repeat_publish_epoch_unchanged")):
        raise ValueError("service boundary checks missing")
    if not (initial["epoch"] < rev_off["epoch"] == rev_on["epoch"] == outage["epoch"] < withdrawn["epoch"] == final["epoch"]):
        raise ValueError("publication epochs not consistent")
    if len({r["generation"] for r in receipts if r["stage"] in ONLINE}) != 3:
        raise ValueError("revision or withdrawal reused stale generation")
    if vector.get("verified") is not True or vector.get("experimental_preserved") is not True:
        raise ValueError("Milvus evidence incomplete")
    if vector["collection"] != final["generation"] or vector["epoch"] != final["epoch"] or vector["digest"] != final["worker"]["digest"]:
        raise ValueError("Milvus/worker snapshot mismatch")
    if sorted(vector["collections"]) != sorted([vector["collection"], "shop_agent_stack_eval_m13c_sentinel"]):
        raise ValueError("live collection cleanup or experimental isolation failed")
    manifest = vector["model_manifest"]
    if manifest.get("config_sha256") != config_hash or set(manifest.get("models", {})) != {"embedding", "reranker"}:
        raise ValueError("model provenance mismatch")
    for kind, model in manifest["models"].items():
        if model["repository"] != config[kind] or model["revision"] != config["model_revisions"][kind]:
            raise ValueError("unpinned model")
        files = model["files_sha256"]
        if not {"model.safetensors", "tokenizer.json", "config.json"}.issubset(files):
            raise ValueError("missing model files")
        if any(p.endswith((".py", ".pkl", ".bin")) or not re.fullmatch(r"[0-9a-f]{64}", digest) for p, digest in files.items()):
            raise ValueError("unsafe model evidence")
    expected = {"visible_clauses": vector["count"], "draft_clauses": 0, "epoch": final["epoch"], "job_status": "READY", "collection": final["generation"]}
    for name, status in (("first", "SUPERSEDED"), ("revised", "WITHDRAWN"), ("staff", "WITHDRAWN"), ("draft", "DRAFT")):
        expected[name + "_status"] = status
        expected[name + "_version"] = 2 if name == "revised" else 1
        expected[name + "_family"] = policy_ids["first"] if name == "revised" else policy_ids[name]
    if type(vector["count"]) is not int or vector["count"] < 1 or vector["count"] != final["worker"]["clauses"]:
        raise ValueError("invalid Milvus count")
    if database != expected or any(isinstance(v, bool) for v in database.values()):
        raise ValueError("independent MySQL/Milvus lifecycle invariant failed")
    return {"verified": True, "scope": "small-synthetic-corpus-real-bge-milvus-mcp-java-lifecycle",
            "stages": receipts, "database": database, "milvus": vector,
            "live_llm_verified": False, "ranking_quality_benchmark": False}


def fault(state: Path, base: list[str], command, service: str, action):
    """Restore even if injection/probe fails. Never replace the first failure with recovery."""
    primary = None
    try:
        command(state, "hybrid-stop-" + service, [*base, "stop", "-t", "5", service], timeout=40)
        return action()
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            command(state, "hybrid-restore-" + service,
                    [*base, "up", "-d", "--wait", "--wait-timeout", "180", service], timeout=240)
        except Exception as exc:
            if primary is None:
                raise
            primary.add_note("owned service restoration also failed: " + type(exc).__name__)


def collect(state: Path, base: list[str], command, root: Path) -> dict:
    import hashlib
    receipts = []
    executable = [*base, "exec", "-T", "-w", "/app", "agent", "python", "/opt/shop_e2e/hybrid_probe.py"]
    check = json.loads(command(state, "hybrid-import-preflight", [*executable, "check-imports"], timeout=30).strip())
    if not isinstance(check, dict) or set(check) != {"imports_verified"} or check["imports_verified"] is not True:
        raise ValueError("Agent probe import preflight did not execute")
    def probe(stage):
        raw = command(state, "hybrid-" + stage, [*executable, stage], timeout=300)
        item = json.loads(raw.strip())
        receipts.append(item)
        # No credentials/content in these reviewed aggregate receipts. Retain partial outcomes.
        (state / "artifacts/hybrid-progress.json").write_text(json.dumps(receipts, indent=2))
    probe("initial")
    command(state, "hybrid-experimental-seed", [*base, "exec", "-T", "retrieval-worker", "python", "tools/shop_e2e/hybrid_vector.py", "seed"], timeout=60)
    fault(state, base, command, "retrieval-worker", lambda: probe("revision_offline"))
    probe("revision_online")
    def outage():
        probe("milvus_outage")
        probe("withdraw_offline")
    fault(state, base, command, "milvus", outage)
    probe("recovered")
    vector = json.loads(command(state, "hybrid-vector-readback", [*base, "exec", "-T", "retrieval-worker", "python", "tools/shop_e2e/hybrid_vector.py", "readback"], timeout=60).strip())
    raw = command(state, "hybrid-mysql-readback", [*base, "exec", "-T", "mysql", "sh", "-c",
        'MYSQL_PWD="$MYSQL_PASSWORD" mysql --batch --skip-column-names --raw --default-character-set=utf8mb4 -ushop_agent_stack -Dshop_agent_stack'],
        input_text=readback_sql(receipts[-1]["policy_ids"]), timeout=30)
    path = root / "evaluation/retrieval-matrix.json"
    return verify(receipts, vector, json.loads(raw.strip()), json.loads(path.read_text()), hashlib.sha256(path.read_bytes()).hexdigest())
