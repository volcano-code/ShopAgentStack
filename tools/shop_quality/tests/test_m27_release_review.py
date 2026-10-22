"""Fail-closed review gate never confuses CI success with a release approval."""
from copy import deepcopy
import json

import pytest
from tools.shop_quality import m27_release_review as gate

HEAD = "a"*40
BASE = "b"*40
CHECKOUT = "c"*40
TREE = "d"*40


def source():
    return {"repository":gate.REPOSITORY, "head_sha":HEAD,
            "base_sha":BASE, "draft":True}


def valid():
    return {
        "schema":"shopagent-m27-release-review/v1",
        "source":source(),
        "workflows":{name:{
            "head_sha":HEAD, "run_id":40000001 + i,
            "run_attempt":1, "status":"completed", "conclusion":"success"
        } for i,name in enumerate(gate.EXPECTED)}
    }


def evaluate(doc=None):
    return gate.validate(doc or valid(), checkout=CHECKOUT, tree=TREE,
                         parents=[BASE,HEAD], tracked=set(gate.NEEDED_TRACKED))


def test_complete_candidate_only_ready_for_human_review():
    output=evaluate()
    assert output["decision"] == "ready_for_human_review_only"
    assert len(output["checks"])==10
    assert output["production_deployment_approved"] is False
    assert output["signed_provenance_verified"] is False
    assert output["real_deepseek_accuracy_verified"] is False
    assert output["source"]["checkout_sha"] == CHECKOUT


@pytest.mark.parametrize("attribute,value",[
    ("head_sha","e"*40),("status","in_progress"),
    ("conclusion","failure"),("conclusion","cancelled"),
    ("conclusion","skipped"),("run_attempt",0),("run_id",True),
])
def test_wrong_head_missing_result_and_cancellation_rejected(attribute,value):
    x=valid()
    x["workflows"]["Hybrid retrieval E2E"][attribute]=value
    with pytest.raises(ValueError):
        evaluate(x)


def test_absent_extra_duplicate_workflows_all_refused():
    x=valid(); x["workflows"].pop("Admin review UI")
    with pytest.raises(ValueError):
        evaluate(x)
    x=valid();x["workflows"]["Unsafe unknown pipeline"] = dict(next(iter(x["workflows"].values())))
    with pytest.raises(ValueError):
        evaluate(x)
    x=valid()
    x["workflows"]["Admin review UI"]["run_id"]=x["workflows"]["Business E2E"]["run_id"]
    with pytest.raises(ValueError):
        evaluate(x)


@pytest.mark.parametrize("field,bad", [
    ("head_sha","synth"), ("base_sha","f"*39),
    ("repository","someone-else/ShopAgentStack"),
    ("draft",False),
])
def test_mismatched_scope_fails_closed(field,bad):
    x=valid();x["source"][field]=bad
    with pytest.raises(ValueError):
        evaluate(x)


def test_dirty_stale_or_untracked_credentials_are_rejected():
    for parents in ([],[BASE],["f"*40]):
        with pytest.raises(ValueError):
            gate.validate(valid(), checkout=CHECKOUT, tree=TREE,
                          parents=parents,tracked=set(gate.NEEDED_TRACKED))
    for missing in list(gate.NEEDED_TRACKED)[:2]:
        with pytest.raises(ValueError):
            gate.validate(valid(), checkout=CHECKOUT, tree=TREE,
                          parents=[HEAD],tracked=set(gate.NEEDED_TRACKED)-{missing})
    for bad in (".env","deploy/secrets/token","data/private/case.db",
                ".local/demo/owner.json","deploy/keys/session.key"):
        assert gate.prohibited_file(bad)
        with pytest.raises(ValueError):
            gate.validate(valid(),checkout=CHECKOUT,tree=TREE,parents=[HEAD],
                          tracked=set(gate.NEEDED_TRACKED)|{bad})
    assert not gate.prohibited_file("deploy/.env.example")


def test_duplicate_keys_or_nonfinite_json_cannot_be_used_for_review():
    with pytest.raises(ValueError):
        gate.strict_json('{"schema":"ok","schema":"bad"}')
    with pytest.raises(ValueError):
        gate.strict_json('{"score":NaN}')


def test_cli_private_output_and_refuses_clobber(tmp_path,monkeypatch):
    input_file=tmp_path/"check.json"
    dest=tmp_path/"review.json"
    input_file.write_text(json.dumps(valid()))
    monkeypatch.setattr(gate,"checkout_state",lambda _: (
        CHECKOUT,TREE,[BASE,HEAD],set(gate.NEEDED_TRACKED)))
    args=["--evidence",str(input_file),"--out",str(dest),
          "--repo",str(tmp_path)]
    assert gate.main(args)==0
    output=json.loads(dest.read_text())
    assert output["production_deployment_approved"] is False
    assert dest.stat().st_mode & 0o777 == 0o600
    assert gate.main(args)==2
