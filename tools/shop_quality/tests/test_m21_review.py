"""Strict M2.1 rubric review is offline and cannot grade its own model."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from tools.shop_quality import m21_review

ROOT = Path(__file__).resolve().parents[3]
D = hashlib.sha256(b"synthetic answer").hexdigest()


def fixture():
    plan = m21_review.plan_cases(
        ROOT / "evaluation/shopping-dialogues-v1.json", ["dev-reference"])
    rows = [{
        "case_id": "dev-reference", "turn": n, "run_id": str(uuid4()),
        "answer_sha256": D, "answer_present": True, "status":"COMPLETED",
        "citation_ids":["G10003"], "tool_started":["get_product"],
    } for n in (1,2)]
    obs = {"schema_version":1, "mode":"live_service_observation",
           "claim_scope":"unreviewed_provider_and_service_observations",
           "dataset_sha256":plan["dataset_sha256"],"split":"dev",
           "case_ids":["dev-reference"],"expected_turns":2,
           "observed_turns":2,"status":"observed","rows":rows,
           "answer_accuracy":None,"task_success_rate":None,
           "business_write_safety_verified":False,
           "server_git_sha_verified":False,
           "model_cost_verified":False}
    return plan,obs


def verdicts(obs, positive=True):
    return [{
       "case_id":r["case_id"],"turn":r["turn"],
       "run_id":r["run_id"],"answer_sha256":r["answer_sha256"],
       "method":"human","reviewer_reference":"review-1",
       "answer_correct": positive, "citation_support":positive,
       "clarification_quality":None,
    } for r in obs["rows"]]


def test_blank_review_template_cannot_claim_answer_quality():
    plan,obs=fixture()
    template=m21_review.review_template(obs,plan)
    assert len(template)==2
    assert all(item["answer_correct"] is None for item in template)
    assert all(item["reviewer_reference"] is None for item in template)
    assert "远山" not in json.dumps(template)  # rubric only, no test prompt/reply


def test_review_template_contains_public_per_turn_rubrics_and_expected_contracts():
    plan, obs = fixture()
    sheet = m21_review.review_template(obs, plan)
    assert "500mL" in sheet[0]["rubric"]
    assert "先重查" in sheet[1]["rubric"]
    assert sheet[0]["expected_contract"] == {"products": [10003]}
    assert sheet[1]["expected_contract"] == {"products": [10003]}
    assert all("input" not in entry and "PRIVATE" not in str(entry)
               for entry in sheet)


def test_blank_or_missing_review_rubric_fails_closed():
    import copy
    plan, obs = fixture()
    for bad in (None, "", "   ", "x" * 2001):
        broken = copy.deepcopy(plan)
        broken["cases"][0]["turns"][0]["rubric"] = bad
        with pytest.raises(m21_review.ProbeError):
            m21_review.review_template(obs, broken)


def test_unjudged_reviews_cannot_create_fake_accuracy():
    plan,obs=fixture()
    out=m21_review.score(obs,plan,[])
    assert out["human_rubric_pass_rate"] is None
    assert out["task_success_rate"] is None
    assert out["human_review_coverage"] == 0
    assert out["human_rubric_lower_bound"] == 0
    assert not out["complete_human_review"]


def test_explicit_human_labels_observe_full_denominator_but_not_business_audit():
    plan,obs=fixture()
    done=m21_review.score(obs,plan,verdicts(obs))
    assert done["complete_human_review"] is True
    assert done["human_rubric_pass_rate"] == 1
    assert done["citation_support_rate"] == 1
    assert done["task_success_rate"] is None
    assert done["business_write_safety_verified"] is False
    assert done["reviewer_origin_authenticated"] is False


def test_partial_or_incomplete_run_does_not_claim_full_accuracy():
    plan,obs=fixture()
    only_first=m21_review.score(obs,plan,verdicts(obs)[:1])
    assert not only_first["complete_human_review"]
    assert only_first["human_rubric_pass_rate"] is None
    assert only_first["human_rubric_lower_bound"] == 0.5
    obs["rows"].pop()
    obs["observed_turns"]=1
    obs["status"]="incomplete"
    incomplete=m21_review.score(obs,plan,verdicts(obs))
    assert incomplete["human_rubric_pass_rate"] is None


@pytest.mark.parametrize("bad", ["model", "run", "hash", "repeat", "unknown", "strbool"])
def test_forged_self_judgments_mismatched_provenance_and_duplicates_fail(bad):
    plan,obs=fixture()
    rows=verdicts(obs)
    if bad=="model": rows[0]["method"]="model"
    if bad=="run": rows[0]["run_id"]=str(uuid4())
    if bad=="hash": rows[0]["answer_sha256"]="0"*64
    if bad=="repeat": rows.append(dict(rows[0]))
    if bad=="unknown": rows[0]["case_id"]="other"
    if bad=="strbool": rows[0]["answer_correct"]="true"
    with pytest.raises(m21_review.ProbeError):
        m21_review.score(obs,plan,rows)


def test_report_cannot_be_self_graded_or_leak_raw_text():
    plan,obs=fixture()
    obs["answer_accuracy"]=1.0
    with pytest.raises(m21_review.ProbeError):
        m21_review.review_template(obs,plan)
    obs["answer_accuracy"]=None
    obs["rows"][0]["answer"]="PRIVATE MODEL ANSWER"
    with pytest.raises(m21_review.ProbeError):
        m21_review.review_template(obs,plan)


def test_private_review_file_rejects_invalid_and_enforces_permissions(tmp_path):
    plan,obs=fixture()
    dest=tmp_path/"review.jsonl"
    template=m21_review.review_template(obs,plan)
    data="".join(json.dumps(x,ensure_ascii=False)+"\n" for x in template)
    m21_review.private_bytes(dest,data)
    assert dest.stat().st_mode & 0o777 == 0o600
    with pytest.raises(m21_review.ProbeError):
        m21_review.private_bytes(dest,data)
    # A template is not a completed review; editable rubric/instructions
    # cannot silently enter the scored review contract.
    with pytest.raises(m21_review.ProbeError):
        m21_review.load_review(dest)
    completed=tmp_path/"completed.jsonl"
    m21_review.private_bytes(completed,"".join(json.dumps(r)+"\n" for r in verdicts(obs)))
    assert m21_review.score(obs,plan,m21_review.load_review(completed))["complete_human_review"]


def test_failed_or_answerless_turn_never_counts_as_complete_human_evaluation():
    plan,obs=fixture()
    obs["rows"][1]["status"]="FAILED"
    partial=m21_review.score(obs,plan,verdicts(obs)[:1])
    assert partial["human_rubric_pass_rate"] is None
    assert partial["complete_human_review"] is False
    obs["rows"][1]["status"]="COMPLETED"
    obs["rows"][1]["answer_present"]=False
    obs["rows"][1]["answer_sha256"]=None
    partial=m21_review.score(obs,plan,verdicts(obs)[:1])
    assert partial["human_rubric_pass_rate"] is None
    assert not partial["complete_human_review"]
