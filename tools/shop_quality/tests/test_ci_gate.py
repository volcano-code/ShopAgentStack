"""Exercise the real prerequisite decision function and CLI, not hosted-job simulations."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools.shop_quality.ci_gate import REQUIRED_JOBS, MAX_INPUT, evaluate, markdown

ROOT = Path(__file__).resolve().parents[3]


def source():
    return dict(repository="volcano-code/ShopAgentStack", run_id="123", run_attempt="2",
                event="pull_request", head_sha="a"*40, event_sha="b"*40,
                checkout_sha="b"*40, tree_sha="c"*40)


def jobs():
    return {name: {"result": "success", "outputs": {}} for name in REQUIRED_JOBS}


def test_complete_needs_pass_and_provenance_distinguishes_head_from_checkout():
    report = evaluate(json.dumps(jobs()), source())
    assert report["passed"]
    assert report["source"]["head_sha"] != report["source"]["checkout_sha"]
    assert report["source"]["run_attempt"] == "2"
    assert report["scope"] == "quality_prerequisites_only"


@pytest.mark.parametrize("name", REQUIRED_JOBS)
def test_previously_accepted_partial_needs_now_fail(name):
    data = jobs(); del data[name]
    # Reproduces the old gate: all remaining values being success was sufficient.
    assert data and all(v["result"] == "success" for v in data.values())
    report = evaluate(json.dumps(data), source())
    assert not report["passed"] and "missing_job" in report["reasons"]


@pytest.mark.parametrize("name", REQUIRED_JOBS)
@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped"])
def test_all_non_success_prerequisites_fail(name, status):
    data = jobs(); data[name]["result"] = status
    assert not evaluate(json.dumps(data), source())["passed"]


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "success", {"result": True}, {"result": "SUCCESS"}])
def test_non_contract_job_values_fail(value):
    data = jobs(); data["web"] = value
    assert not evaluate(json.dumps(data), source())["passed"]


@pytest.mark.parametrize("raw", ["", "{}", "[]", "null", "true", '{"web":', '{"web":{},"web":{}}', '{"x":NaN}', " "*(MAX_INPUT+1), '['*1100+']'*1100])
def test_malformed_missing_duplicate_or_unbounded_input_fails(raw):
    assert not evaluate(raw, source())["passed"]


def test_nested_duplicate_rejected():
    raw = json.dumps(jobs()).replace('"outputs": {}', '"outputs": {}, "outputs": {}', 1)
    assert "invalid_needs" in evaluate(raw, source())["reasons"]


def test_unexpected_jobs_and_secrets_not_echoed():
    data = jobs(); secret = "DO_NOT_LEAK_THIS_PRIVATE_VALUE"
    data[secret] = {"result": "success"}
    data["web"]["result"] = secret
    data["commerce"]["outputs"] = {"credential": secret}
    report = evaluate(json.dumps(data), source())
    assert not report["passed"] and "unexpected_job" in report["reasons"]
    assert secret not in json.dumps(report) + markdown(report)


def test_outputs_are_ignored_even_on_success():
    data = jobs(); data["web"]["outputs"] = {"secret": "do-not-copy"}
    report = evaluate(json.dumps(data), source())
    assert report["passed"] and "do-not-copy" not in json.dumps(report) + markdown(report)


@pytest.mark.parametrize("key", list(source()))
def test_required_source_fields_are_validated(key):
    data = source(); data[key] = "DO_NOT_ECHO_PRIVATE_VALUE\n"
    report = evaluate(json.dumps(jobs()), data)
    assert not report["passed"] and "invalid_source" in report["reasons"]
    assert "DO_NOT_ECHO" not in json.dumps(report) + markdown(report)


def test_wrong_checkout_fails():
    data = source(); data["checkout_sha"] = "d"*40
    assert "checkout_mismatch" in evaluate(json.dumps(jobs()), data)["reasons"]


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_non_pr_head_must_match_event(event):
    data = source(); data["event"] = event
    assert not evaluate(json.dumps(jobs()), data)["passed"]
    data["head_sha"] = data["event_sha"]
    assert evaluate(json.dumps(jobs()), data)["passed"]


@pytest.mark.parametrize("raw,expected", [(json.dumps(jobs()), 0), ("{}", 1), ("not-json-secret", 1)])
def test_actual_cli_writes_both_success_and_failure_evidence(tmp_path, raw, expected):
    out = tmp_path / "report"
    summary = tmp_path / "summary.md"
    env = {**os.environ, "GITHUB_REPOSITORY": "volcano-code/ShopAgentStack", "GITHUB_RUN_ID": "123",
           "GITHUB_RUN_ATTEMPT": "2", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": "b"*40,
           "CI_HEAD_SHA": "a"*40, "JOB_RESULTS": raw, "GITHUB_STEP_SUMMARY": str(summary)}
    cmd = [sys.executable, "-m", "tools.shop_quality.ci_gate", "--checkout-sha", "b"*40,
           "--tree-sha", "c"*40, "--out", str(out)]
    result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stderr
    report = json.loads((out / "required.json").read_text())
    assert report["passed"] is (expected == 0)
    assert summary.read_text() == (out / "required.md").read_text()
    assert "not-json-secret" not in result.stdout + result.stderr + summary.read_text()
    before = (out / "required.json").read_bytes()
    assert subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=10).returncode == 1
    assert (out / "required.json").read_bytes() == before


def test_workflow_runs_real_gate_and_always_keeps_its_decision():
    workflow = yaml.load((ROOT / ".github/workflows/shop-quality.yml").read_text(), Loader=yaml.BaseLoader)
    gate = workflow["jobs"]["required"]
    assert set(gate["needs"]) == set(REQUIRED_JOBS)
    assert gate["if"] == "always()"
    assert gate["env"]["JOB_RESULTS"] == "${{ toJSON(needs) }}"
    steps = gate["steps"]
    assert steps[0]["with"]["persist-credentials"] == "false"
    commands = "\n".join(s.get("run", "") for s in steps)
    assert "python3 -m tools.shop_quality.ci_gate" in commands
    assert "git rev-parse HEAD" in commands and "HEAD^{tree}" in commands
    upload = steps[-1]
    assert upload["if"] == "always()" and upload["with"]["if-no-files-found"] == "error"
    assert "github.run_attempt" in upload["with"]["name"]
