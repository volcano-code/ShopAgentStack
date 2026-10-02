from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import pytest
from tools.shop_quality.__main__ import main
from tools.shop_quality.contracts import ContractError, load_json, load_jsonl, parse_json, sha256, validate_cases, validate_runs
from tools.shop_quality.evaluator import apply_gates, evaluate
from tools.shop_quality.metrics import mean, quantile, retrieval
from tools.shop_quality.compare import compare_reports
from tools.shop_quality.reporting import html_report, write_reports
from tools.shop_quality.junit_gate import summarize

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

@pytest.fixture
def data():
    return (load_jsonl(FIXTURES / "cases.jsonl"), load_jsonl(FIXTURES / "runs-valid.jsonl"),
            load_json(FIXTURES / "manifest.json"), sha256(FIXTURES / "cases.jsonl"))


def ev(data):
    return evaluate(*data)


def val(report, name):
    return report["metrics"][name]["value"]


def test_hand_computed_retrieval():
    values = retrieval(["z", "b", "a"], {"a": 3, "b": 1}, 3)
    assert values["recall@3"] == 1
    assert values["mrr@3"] == .5
    assert values["ndcg@3"] == pytest.approx((1/math.log2(3)+7/math.log2(4))/(7+1/math.log2(3)))


def test_recall_uses_all_relevant_documents():
    assert retrieval(["a"], {"a": 1, "b": 1, "c": 1}, 1)["recall@1"] == pytest.approx(1/3)


def test_empty_qrels_undefined():
    assert all(v is None for v in retrieval(["a"], {}, 5).values())


def test_duplicate_ranked_ids_rejected():
    with pytest.raises(ValueError):
        retrieval(["a", "a"], {"a": 1}, 5)


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_k(k):
    with pytest.raises(ValueError):
        retrieval([], {}, k)


def test_percentile_includes_tail():
    assert quantile([100,200,300,400,500], .95) == 480
    assert quantile([42], .95) == 42
    assert quantile([], .95) is None
    with pytest.raises(ValueError):
        quantile([1], 1.1)


def test_mean_missing_not_zero():
    assert mean([None,None]) is None
    assert mean([None,2,4]) == 3


def test_valid_fixture_is_not_real_benchmark(data):
    report = ev(data)
    assert report["claim_scope"] == "fixture_only"
    assert val(report, "task_success_rate") == 1
    assert report["metrics"]["recall@5"]["n"] == 4
    assert apply_gates(report, load_json(FIXTURES / "gates.json"))["passed"]


def test_missing_case_not_removed_from_denominator(data):
    data[1].pop()
    report = ev(data)
    assert report["dataset_cases"] == 6
    assert report["counts"]["missing"] == 1
    assert val(report, "task_success_rate") == pytest.approx(5/6)
    assert val(report, "recall@5") == .75
    assert not apply_gates(report, load_json(FIXTURES / "gates.json"))["passed"]


@pytest.mark.parametrize("status", ["error", "timeout", "skipped"])
def test_failure_status_penalized(data, status):
    data[1][0]["status"] = status
    assert val(ev(data), "task_success_rate") == pytest.approx(5/6)
    assert val(ev(data), "recall@5") == .75


def test_timeout_latency_not_dropped(data):
    data[1][0].update(status="timeout", latency_ms=100000)
    assert val(ev(data), "latency_p95_ms") > 50000


def test_unknown_judgment_is_not_pass(data):
    data[1][0].pop("task_judgment")
    report = ev(data)
    assert val(report, "task_judgment_coverage") == pytest.approx(5/6)
    assert val(report, "task_success_rate") == pytest.approx(5/6)


def test_no_evidence_is_not_perfect_retrieval(data):
    for c in data[0]:
        c["qrels"] = {}
    report = ev(data)
    assert val(report, "recall@5") is None
    assert not apply_gates(report, load_json(FIXTURES / "gates.json"))["passed"]


def test_unsupported_citation_penalized(data):
    data[1][0]["citation_ids"].append("withdrawn-policy")
    assert val(ev(data), "citation_id_precision") == pytest.approx(.875)


def test_absent_citations_penalized(data):
    data[1][0]["citation_ids"] = []
    assert val(ev(data), "citation_id_recall") == .75


def test_wrong_abstention_penalized(data):
    data[1][3]["abstained"] = False
    assert val(ev(data), "correct_abstention_rate") == .5


def test_model_self_judgment_rejected(data):
    data[1][0]["task_judgment"]["method"] = "llm_self_report"
    with pytest.raises(ContractError): ev(data)


def test_missing_audit_is_unknown_not_safe(data):
    data[1][0].pop("write_audit")
    report = ev(data)
    assert report["counts"]["unknown_write_cases"] == 1
    assert val(report, "safe_write_case_rate") == pytest.approx(5/6)


def test_unconfirmed_commit_hard_fails_even_permissive_threshold(data):
    data[1][0]["write_audit"]["events"] = [{"operation_id":"op1","committed":True,"confirmation_validated":False,"authorized":True}]
    report = ev(data)
    gates = {"schema_version":1,"min_cases":1,"thresholds":{"task_success_rate":{"min":0,"min_samples":1}}}
    assert not apply_gates(report,gates)["passed"]
    assert report["counts"]["unconfirmed_write_events"] == 1


def test_observed_unsafe_writes_not_ignored_in_non_safety_case(data):
    data[0][0]["check_write_safety"] = False
    data[1][0]["write_audit"]["events"] = [{"operation_id":"op1","committed":True,"confirmation_validated":True,"authorized":False}]
    assert ev(data)["counts"]["unsafe_write_cases"] == 1


@pytest.mark.parametrize("field", ["confirmation_validated", "authorized"])
def test_unknown_authorization_fails_closed(data, field):
    event = {"operation_id":"op1","committed":True,"confirmation_validated":True,"authorized":True}
    event[field] = None
    data[1][0]["write_audit"]["events"] = [event]
    assert ev(data)["counts"]["unknown_write_cases"] == 1


def test_failed_write_is_not_committed_violation(data):
    data[1][0]["write_audit"]["events"] = [{"operation_id":"op1","committed":False,"confirmation_validated":False,"authorized":False}]
    assert ev(data)["counts"].get("unsafe_write_cases",0) == 0


def test_bad_commit_kept_when_audit_incomplete(data):
    data[1][0]["write_audit"] = {"complete":False,"source":"fixture","events":[{"operation_id":"op1","committed":True,"confirmation_validated":False,"authorized":True}]}
    report = ev(data)
    assert report["counts"]["unsafe_write_cases"] == 1
    assert report["counts"]["unknown_write_cases"] == 1


def test_forbidden_tool_hard_fails(data):
    data[1][0]["selected_tools"].append("direct_refund")
    report = ev(data)
    assert report["counts"]["forbidden_tool_cases"] == 1
    assert not apply_gates(report, load_json(FIXTURES/"gates.json"))["passed"]


def test_live_manifest_rejects_unknown_git_revision(data):
    data[2]["source_kind"] = "live"
    with pytest.raises(ContractError): ev(data)


def test_fixture_audit_does_not_count_for_live(data):
    data[2]["source_kind"] = "live"
    data[2]["system"]["git_revision"] = "a"*40
    assert val(ev(data),"write_audit_coverage") == 0


def test_live_with_backend_audit_is_supplied_observations_not_certificate(data):
    data[2]["source_kind"] = "recorded"
    data[2]["system"]["git_revision"] = "a"*40
    for row in data[1]: row["write_audit"]["source"] = "backend"
    assert ev(data)["claim_scope"] == "supplied_observations_only"


def test_dataset_hash_mismatch(data):
    data[2]["dataset_sha256"] = "a"*64
    with pytest.raises(ContractError): ev(data)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "1"])
def test_invalid_latency(data,bad):
    data[1][0]["latency_ms"] = bad
    with pytest.raises(ContractError): ev(data)


def test_missing_timeout_latency(data):
    data[1][0].pop("latency_ms")
    with pytest.raises(ContractError): ev(data)


@pytest.mark.parametrize("field", ["retrieved_ids", "citation_ids", "selected_tools"])
def test_duplicate_output_ids(data,field):
    data[1][0][field] = ["a","a"]
    with pytest.raises(ContractError): ev(data)


def test_unknown_output_field(data):
    data[1][0]["success"] = True
    with pytest.raises(ContractError): ev(data)


def test_duplicate_case(data):
    data[0].append(copy.deepcopy(data[0][0]))
    with pytest.raises(ContractError): ev(data)


def test_duplicate_run(data):
    data[1].append(copy.deepcopy(data[1][0]))
    with pytest.raises(ContractError): ev(data)


def test_unknown_run(data):
    data[1][0]["case_id"] = "not-in-gold"
    with pytest.raises(ContractError): ev(data)


def test_empty_cases():
    with pytest.raises(ContractError): validate_cases([])


def test_no_selected_split(data):
    with pytest.raises(ContractError): evaluate(*data, split="dev")


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{not json}'])
def test_malformed_json(raw):
    with pytest.raises(ContractError): parse_json(raw)


def test_gate_typo_does_not_silently_pass(data):
    config={"schema_version":1,"min_cases":1,"thresholds":{"recal@5":{"min":0,"min_samples":1}}}
    with pytest.raises(ContractError): apply_gates(ev(data),config)


def test_gate_requires_enough_samples(data):
    config={"schema_version":1,"min_cases":1,"thresholds":{"recall@5":{"min":0,"min_samples":100}}}
    assert not apply_gates(ev(data),config)["passed"]


def test_bad_gate_bounds(data):
    config={"schema_version":1,"min_cases":1,"thresholds":{"recall@5":{"min":1,"max":0,"min_samples":1}}}
    with pytest.raises(ContractError): apply_gates(ev(data),config)


def test_comparison_is_paired_and_deterministic(data):
    before=ev(data)
    data[1][0]["retrieved_ids"] = []
    after=ev(data)
    a=compare_reports(before,after,samples=100)
    assert a == compare_reports(before,after,samples=100)
    assert a["candidate_minus_baseline"] == -.25
    assert a["source_kind"] == "fixture"


def test_comparison_rejects_different_data(data):
    before=ev(data);after=copy.deepcopy(before)
    after["manifest"]["dataset_sha256"] = "x"*64
    with pytest.raises(ContractError): compare_reports(before,after)


def test_comparison_rejects_different_missingness(data):
    before=ev(data);after=copy.deepcopy(before)
    after["per_case"][0]["values"]["ndcg@5"] = None
    with pytest.raises(ContractError): compare_reports(before,after)


def test_html_escapes_untrusted_ids(data):
    data[0][0]["case_id"] = data[1][0]["case_id"] = '<script>alert("x")</script>'
    output=html_report(ev(data))
    assert "<script>" not in output
    assert "&lt;script&gt;" in output


def test_report_writes_machine_and_human_outputs(data,tmp_path):
    write_reports(ev(data),tmp_path)
    assert {f.name for f in tmp_path.iterdir()} == {"report.json","report.md","report.html"}
    assert load_json(tmp_path/"report.json")["dataset_cases"] == 6


def cli_args(tmp_path,filename="runs-valid.jsonl"):
    return ["evaluate","--cases",str(FIXTURES/"cases.jsonl"),"--runs",str(FIXTURES/filename),"--manifest",str(FIXTURES/"manifest.json"),"--gates",str(FIXTURES/"gates.json"),"--out",str(tmp_path)]


def test_cli_fixture_requires_explicit_opt_in(tmp_path):
    assert main(cli_args(tmp_path)) == 2
    assert not (tmp_path/"report.json").exists()


def test_cli_success(tmp_path):
    assert main(cli_args(tmp_path)+["--allow-fixture"]) == 0
    assert "input_hashes" in load_json(tmp_path/"report.json")


def test_cli_gate_failure_returns_one_and_writes_report(tmp_path):
    assert main(cli_args(tmp_path,"runs-adversarial.jsonl")+["--allow-fixture"]) == 1
    assert not load_json(tmp_path/"report.json")["gates"]["passed"]


def test_junit_uses_testcases_not_forged_summary(tmp_path):
    f=tmp_path/"junit.xml";f.write_text('<testsuite tests="999"><testcase/><testcase><skipped/></testcase><testcase><failure/></testcase></testsuite>')
    result=summarize([f])
    assert result["tests"] == 3 and result["executed"] == 2 and result["failures"] == 1
    assert summarize([])["executed"] == 0


def test_huge_integer_is_contract_error_not_process_crash(data):
    data[1][0]['latency_ms'] = 10**400
    with pytest.raises(ContractError, match="out of range"):
        ev(data)
