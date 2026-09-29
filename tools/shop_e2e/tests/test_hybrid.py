"""Fixtures below test the acceptance gate, NOT real model/Milvus behavior."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import pytest
import yaml
from tools.shop_e2e import hybrid as h, stack
from tools.shop_e2e import __main__ as runner

ROOT = Path(__file__).resolve().parents[3]
P = "shop-e2e-" + "a" * 32
CONFIG = json.loads((ROOT / "evaluation/retrieval-matrix.json").read_text())
CONFIG_HASH = hashlib.sha256((ROOT / "evaluation/retrieval-matrix.json").read_bytes()).hexdigest()


def specimen():
    p = {"first": 2, "staff": 3, "draft": 4, "revised": 5}
    receipts = []
    for index, stage in enumerate(h.STAGES):
        epoch = [3, 4, 4, 4, 6, 6][index]
        generation = f"shop_agent_stack_live_{'b'*12}_{epoch}_{'c'*16}"
        hit_id = 2 if index == 0 else 5 if index < 4 else 1
        r = {"stage": stage, "verified": True, "epoch": epoch, "policy_ids": dict(p),
             "method": "HYBRID_RRF_RERANK" if stage in h.ONLINE else "BM25", "degraded": stage not in h.ONLINE,
             "generation": generation if stage in h.ONLINE else None, "source_hashes_verified": True,
             "invisible_sources_denied": True, "hits": [{"policy_id": hit_id, "version": 2 if hit_id == 5 else 1,
                                                        "clause_no": 1, "content_hash": "d"*64}]}
        if stage in h.ONLINE:
            r["worker"] = {"epoch": epoch, "collection": generation, "digest": "c"*64, "clauses": 1,
                           "model_revisions": CONFIG["model_revisions"]}
        if index == 0:
            r["policy_ids"].pop("revised")
            r.update(service_auth_verified=True, stale_snapshots_denied=True, repeat_publish_epoch_unchanged=True)
        receipts.append(r)
    final = receipts[-1]
    vector = {"verified": True, "experimental_preserved": True, "collection": final["generation"],
              "count": 1, "epoch": 6, "digest": "c"*64,
              "collections": [final["generation"], "shop_agent_stack_eval_m13c_sentinel"],
              "model_manifest": {"config_sha256": CONFIG_HASH, "models": {k: {
                  "repository": CONFIG[k], "revision": CONFIG["model_revisions"][k],
                  "files_sha256": {f: "d"*64 for f in ("model.safetensors", "config.json", "tokenizer.json")}}
                  for k in ("embedding", "reranker")}}}
    database = {"visible_clauses": 1, "draft_clauses": 0, "epoch": 6, "job_status": "READY", "collection": final["generation"]}
    for name, status in (("first", "SUPERSEDED"), ("revised", "WITHDRAWN"), ("staff", "WITHDRAWN"), ("draft", "DRAFT")):
        database.update({name+"_status": status, name+"_family": 2 if name == "revised" else p[name], name+"_version": 2 if name == "revised" else 1})
    return receipts, vector, database


def verify(args):
    return h.verify(*args, CONFIG, CONFIG_HASH)


def test_gate_positive_has_no_llm_or_benchmark_claim():
    result = verify(specimen())
    assert result["verified"] and not result["live_llm_verified"] and not result["ranking_quality_benchmark"]


@pytest.mark.parametrize("mutation", ["missing", "repeat", "reorder", "false", "bool", "silent", "wrong_method", "stale_generation", "epoch",
                                      "invisible", "old", "empty", "hash", "payload", "id", "model", "no_auth", "snapshot", "index", "oversized"])
def test_bad_stage_cannot_be_accepted(mutation):
    receipts, v, d = specimen()
    if mutation == "missing": receipts.pop()
    if mutation == "repeat": receipts[-1] = receipts[0]
    if mutation == "reorder": receipts.reverse()
    if mutation == "false": receipts[1]["verified"] = False
    if mutation == "bool": receipts[1]["verified"] = 1
    if mutation == "silent": receipts[3]["degraded"] = False
    if mutation == "wrong_method": receipts[3]["method"] = "HYBRID_RRF_RERANK"
    if mutation == "stale_generation": receipts[2]["generation"] = receipts[0]["generation"]
    if mutation == "epoch": receipts[2]["epoch"] = 3
    if mutation == "invisible": receipts[0]["hits"][0]["policy_id"] = 3
    if mutation == "old": receipts[-1]["hits"][0]["policy_id"] = 5
    if mutation == "empty": receipts[0]["hits"] = []
    if mutation == "hash": receipts[0]["hits"][0]["content_hash"] = "fake"
    if mutation == "payload": receipts[0]["hits"][0]["text"] = "private"
    if mutation == "id": receipts[1]["policy_ids"]["first"] = True
    if mutation == "model": receipts[2]["worker"]["model_revisions"] = {"embedding": "main"}
    if mutation == "no_auth": receipts[0]["service_auth_verified"] = False
    if mutation == "snapshot": receipts[0]["stale_snapshots_denied"] = False
    if mutation == "index": receipts[-1]["generation"] = "shop_agent_stack_eval_poison"
    if mutation == "oversized": receipts[0]["hits"] *= 6
    with pytest.raises(ValueError): verify((receipts, v, d))


@pytest.mark.parametrize("key", list(specimen()[2]))
def test_each_database_invariant_independent_of_green_probe(key):
    receipts, v, d = specimen()
    d[key] = "WRONG"
    with pytest.raises(ValueError): verify((receipts, v, d))


@pytest.mark.parametrize("mutation", ["missing_safe", "pickle", "revision", "model", "digest", "count", "old_collection", "lost_sentinel", "config"])
def test_vector_and_model_binding(mutation):
    r, v, d = specimen()
    model = v["model_manifest"]["models"]["embedding"]
    if mutation == "missing_safe": model["files_sha256"].pop("model.safetensors")
    if mutation == "pickle": model["files_sha256"]["pytorch_model.bin"] = "a"*64
    if mutation == "revision": model["revision"] = "main"
    if mutation == "model": model["repository"] = "unrelated"
    if mutation == "digest": v["digest"] = "b"*64
    if mutation == "count": v["count"] = 9
    if mutation == "old_collection": v["collections"].append(r[0]["generation"])
    if mutation == "lost_sentinel": v["experimental_preserved"] = False
    if mutation == "config": v["model_manifest"]["config_sha256"] = "wrong"
    with pytest.raises(ValueError): verify((r, v, d))


@pytest.mark.parametrize("value", [True, 0, -2, "1; DROP TABLE x", 2**53])
def test_sql_uses_only_validated_ids(value):
    p = specimen()[0][-1]["policy_ids"]
    p["first"] = value
    with pytest.raises(ValueError): h.readback_sql(p)


def test_sql_includes_effectivity_and_outbox():
    sql = h.readback_sql(specimen()[0][-1]["policy_ids"])
    assert "effective_to>NOW(6)" in sql and "policy_index_job" in sql and "CUSTOMER" in sql


def test_hybrid_stack_has_no_foreign_volume_or_online_worker():
    spec = stack.compose(ROOT, ROOT / ".local/test", P, hybrid=True)
    services = spec["services"]
    assert services["model-prefetch"]["networks"] == ["edge"]
    assert services["model-prefetch"]["profiles"] == ["prepare"]
    assert "depends_on" not in services["model-prefetch"]
    assert all("secret" not in str(v) for v in services["model-prefetch"]["volumes"])
    assert "retrieval" in services["model-prefetch"]["build"]["context"]
    for name in ("milvus", "etcd", "minio", "retrieval-worker"):
        assert "ports" not in services[name] and services[name]["networks"] == ["business"]
        assert services[name]["labels"] == {stack.LABEL: P}
    worker = services["retrieval-worker"]
    assert worker["environment"]["HF_HUB_OFFLINE"] == "1"
    assert worker["environment"]["TRANSFORMERS_OFFLINE"] == "1"
    assert "hybrid_models:/models:ro" in worker["volumes"]
    assert "--network host" not in json.dumps(spec) and "docker.sock" not in json.dumps(spec)
    assert all(not v.get("external") and not v.get("name") for v in spec["volumes"].values())
    assert services["commerce-mcp"]["environment"]["SHOP_AGENT_STACK_RETRIEVAL_MODE"] == "hybrid"
    assert not any("API_KEY" in k for v in services.values() for k in v.get("environment", {}))


@pytest.mark.parametrize("tracing", [False, True])
def test_combined_modes_retain_explicit_boundaries(tracing):
    spec = stack.compose(ROOT, ROOT / ".local/test", P, hybrid=True, tracing=tracing)
    assert ("tempo" in spec["services"]) is tracing
    assert "milvus" in spec["services"]


@pytest.mark.parametrize("stop_fails,probe_fails,restore_fails", [(True, False, False), (False, True, False), (False, False, True), (False, True, True), (False, False, False)])
def test_faults_restore_and_preserve_primary(tmp_path, stop_fails, probe_fails, restore_fails):
    calls = []
    def command(state, stage, args, **kwargs):
        calls.append(stage)
        if ("stop" in stage and stop_fails) or ("restore" in stage and restore_fails):
            raise runner.StageError(stage)
    def action():
        if probe_fails: raise ValueError("primary-probe")
        return 17
    if stop_fails or probe_fails or restore_fails:
        with pytest.raises((ValueError, runner.StageError)) as exc:
            h.fault(tmp_path, [], command, "milvus", action)
        if probe_fails and not stop_fails: assert str(exc.value) == "primary-probe"
    else:
        assert h.fault(tmp_path, [], command, "milvus", action) == 17
    assert calls[-1] == "hybrid-restore-milvus"


def test_prefetch_requires_pin_and_safe_files(tmp_path):
    spec = importlib.util.spec_from_file_location("cache_models_test", ROOT / "services/retrieval/cache_models.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    for filename in ("model.safetensors", "config.json", "tokenizer.json"):
        (tmp_path / filename).write_bytes(b"test-double-not-model")
    called = []
    def download(repo, **kwargs):
        called.append(kwargs)
        return tmp_path
    output = module.manifest(CONFIG, download)
    assert len(output["models"]) == 2 and all(x["token"] is False for x in called)
    (tmp_path / "pytorch_model.bin").write_bytes(b"rejected")
    with pytest.raises(ValueError): module.manifest(CONFIG, download)
    wrong = copy.deepcopy(CONFIG); wrong["model_revisions"]["embedding"] = "main"
    with pytest.raises(ValueError): module.manifest(wrong, download)


def test_probe_has_no_fake_encoder_or_environment_fail_open():
    text = (ROOT / "tools/shop_e2e/hybrid_probe.py").read_text()
    assert "tools.connect" in text and "get_policy_source" in text
    assert "mock" not in text.lower() and "pytest.skip" not in text
    vector = (ROOT / "tools/shop_e2e/hybrid_vector.py").read_text()
    assert 'output_fields=["count(*)"]' in vector and "MilvusClient" in vector


def test_hybrid_workflow_uses_real_entrypoint_and_reviewed_artifacts():
    w = yaml.load((ROOT / '.github/workflows/hybrid-retrieval-e2e.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(w['on']) == {'pull_request', 'workflow_dispatch'}
    assert w['permissions'] == {'contents': 'read'}
    for step in w['jobs']['hybrid']['steps']:
        assert 'continue-on-error' not in step
        if 'uses' in step:
            assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', step['uses'])
            if step['uses'].startswith('actions/checkout@'):
                assert step['with']['persist-credentials'] == 'false'
            if step['uses'].startswith('actions/upload-artifact@'):
                assert step['with']['path'] == '.local/hybrid-e2e/artifacts/'
                assert step['if'] == 'always()'
        if 'run' in step:
            assert '|| true' not in step['run'] and 'secrets.' not in step['run']
            assert subprocess.run(['bash', '-n'], input=step['run'], capture_output=True, text=True).returncode == 0
    text = str(w)
    assert 'run --hybrid --state .local/hybrid-e2e' in text
    assert 'cleanup --state .local/hybrid-e2e' in text
