"""Fail-closed combined UI/retrieval evidence; no running service is substituted here."""
from copy import deepcopy
from pathlib import Path
import pytest
import yaml
from tools.shop_demo.review_probe import validate
from tools.shop_demo.review_smoke import verify
from tools.shop_demo.runtime import DemoError
ROOT=Path(__file__).resolve().parents[3]


def search(mode="hybrid"):
    return {"retrieval":{"method":"HYBRID_RRF_RERANK" if mode=="hybrid" else "BM25",
        "epoch":97,"clauses":241,"degraded":False,"index_generation":"test-generation"},
        "evidence":[{"policy_id":1}]}


def evidence(mode="hybrid"):
    return {"retrieval":mode,"database":{"products":100,"skus":100,"published":96,"drafts":0,
        "stock":180,"customerClauses":241,"staffClauses":48,"epoch":97},
        "retrieval_probe":{"method":"HYBRID_RRF_RERANK" if mode=="hybrid" else "BM25","epoch":97,"hits":3,
                           "sources_verified":True,"hybrid_verified":mode=="hybrid"},
        "hybrid":{"ready":True,"epoch":97,"generation":"test-generation"},
        "browser_tests_passed":1,"viewports":[360,1440],"cleanup_verified":True,
        "live_llm_called":False,"browser_mocked":False}


@pytest.mark.parametrize("mode",["bm25","hybrid"])
def test_correct_combination_accepted(mode):
    validate(search(mode),mode,97,"test-generation" if mode=="hybrid" else None)
    verify(evidence(mode))


@pytest.mark.parametrize("key,value",[("method","BM25"),("epoch",96),("clauses",0),
                                       ("degraded",True),("index_generation","stale")])
def test_hybrid_cannot_pass_on_degradation_or_stale_index(key,value):
    data=search();data["retrieval"][key]=value
    with pytest.raises(ValueError):validate(data,"hybrid",97,"test-generation")


@pytest.mark.parametrize("bad",[[],None,{},[{}]*21])
def test_probe_requires_bounded_actual_hits(bad):
    data=search();data["evidence"]=bad
    with pytest.raises(ValueError):validate(data,"hybrid",97,"test-generation")


@pytest.mark.parametrize("path,value",[
    (("database","published"),0),(("database","stock"),179),(("database","customerClauses"),289),
    (("retrieval_probe","method"),"BM25"),(("retrieval_probe","epoch"),96),
    (("retrieval_probe","sources_verified"),False),(("retrieval_probe","hits"),0),
    (("retrieval_probe","hybrid_verified"),False),(("hybrid","ready"),False),
    (("hybrid","epoch"),96),(("hybrid","generation"),""),
    (("cleanup_verified",),False),(("live_llm_called",),True),(("browser_mocked",),True),
    (("browser_tests_passed",),0),(("viewports",),[1440])])
def test_missing_combination_contract_refused(path,value):
    data=deepcopy(evidence());cursor=data
    for key in path[:-1]:cursor=cursor[key]
    cursor[path[-1]]=value
    with pytest.raises(DemoError):verify(data)


def test_matrix_retains_bm25_adds_hybrid_and_uploads_only_named_artifacts():
    config=yaml.load((ROOT/".github/workflows/admin-review-ui.yml").read_text(),Loader=yaml.BaseLoader)
    job=config["jobs"]["browser-review"]
    assert job["strategy"]["matrix"]["retrieval"]==["bm25","hybrid"]
    assert job["strategy"]["fail-fast"]=="false"
    steps=job["steps"]
    assert any("review_smoke --retrieval ${{ matrix.retrieval }}" in s.get("run","") for s in steps)
    upload=next(s["with"] for s in steps if s.get("uses","").startswith("actions/upload-artifact@"))
    assert "${{ matrix.retrieval }}" in upload["name"]
    assert "mobile-review.png" in upload["path"] and "desktop-review.png" in upload["path"]
    assert "accounts.json" not in upload["path"] and "/logs/" not in upload["path"]
    assert "**/*" not in upload["path"]


def test_probe_does_not_import_models_or_mutation_tools():
    import ast
    tree=ast.parse((ROOT/"tools/shop_demo/review_probe.py").read_text())
    names=[x.args[1].value for x in ast.walk(tree) if isinstance(x,ast.Call) and
           isinstance(x.func,ast.Attribute) and x.func.attr=="call" and len(x.args)>1 and isinstance(x.args[1],ast.Constant)]
    assert set(names)=={"search_policies","get_policy_source"}
