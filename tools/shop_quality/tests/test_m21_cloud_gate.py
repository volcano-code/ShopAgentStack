"""M2.1 cloud acceptance: evidence truthfulness, identity and data minimization."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.shop_quality import m21_cloud_gate as gate


CHECKOUT, TREE, PR = 'a'*40, 'b'*40, 'c'*40


def valid():
    return {
        "scope": "M1.3a-isolated-synthetic-business",
        "status": "passed", "cleanup_verified": True,
        "worktree_clean": True,
        "checkout_sha": CHECKOUT, "tree_sha": TREE,
        "live_model_verified": False, "real_payment_verified": False,
        "m21_fixture": deepcopy(gate.FIXTURE),
        "mcp": {"executed": 4, "passed": 4, "skipped": 0,
                "test_names": sorted(gate.REQUIRED_MCP)},
        "stages": sorted(gate.REQUIRED_STAGES),
        "private_synthetic_token": "NEVER_COPY_THIS_VALUE",
        "project": "PRIVATE_SYNTHETIC_PROJECT_NAME",
    }


def test_true_fixed_fixture_only_report_and_no_raw_identifiers():
    r = gate.inspect(valid(), checkout_sha=CHECKOUT,
                     tree_sha=TREE, pr_head_sha=PR)
    assert r['status'] == 'passed_fixture_readiness'
    assert r['evidence']['executed_mcp_case_count'] == 4
    assert r['evidence']['m21_fixture_case_executed'] is True
    assert r['source'] == {"checkout_sha": CHECKOUT,
                           "tree_sha": TREE, "pr_head_sha": PR}
    assert all(r['not_verified'].values())
    assert 'NEVER_COPY_THIS_VALUE' not in json.dumps(r)
    assert 'PRIVATE_SYNTHETIC_PROJECT_NAME' not in json.dumps(r)
    assert 'deepseek' in json.dumps(r).lower()


@pytest.mark.parametrize('field, value', [
    ('status', 'failed'), ('cleanup_verified', False),
    ('worktree_clean', False), ('live_model_verified', True),
    ('real_payment_verified', True), ('scope', 'production'),
    ('checkout_sha', '0'*40), ('tree_sha', '0'*40),
])
def test_false_business_or_provenance_claims_refused(field, value):
    raw = valid(); raw[field] = value
    with pytest.raises(ValueError):
        gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE)


@pytest.mark.parametrize('field,value', [
    ('verified', False), ('paid_model_called', True),
    ('fixture_only', False), ('answer_accuracy_measured', True),
    ('funds_safety_verified', True), ('real_agent_http', False),
    ('owner_enforced', False), ('no_business_write_events', False),
])
def test_injected_model_grade_or_missing_fixture_check_refused(field, value):
    raw = valid(); raw['m21_fixture'][field] = value
    with pytest.raises(ValueError):
        gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE)


def test_missing_or_skipped_mcp_test_and_stage_refused():
    for key in ['executed', 'passed', 'skipped']:
        raw = valid(); raw['mcp'][key] = 3 if key != 'skipped' else 1
        with pytest.raises(ValueError):
            gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE)
    raw = valid(); raw['mcp']['test_names'] = raw['mcp']['test_names'][:-1]
    with pytest.raises(ValueError):
        gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE)
    raw = valid(); raw['stages'].remove('real-agent-fixture-java-mcp-readback')
    with pytest.raises(ValueError):
        gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE)
    raw = valid(); raw['mcp']['test_names'].append('test_forged_unknown')
    with pytest.raises(ValueError):
        gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE)


def test_invalid_requested_source_identity_fails():
    raw = valid()
    for bad in [None, 'head', 'a'*39, True, '0'*41]:
        with pytest.raises(ValueError):
            gate.inspect(raw, checkout_sha=bad, tree_sha=TREE)
    with pytest.raises(ValueError):
        gate.inspect(raw, checkout_sha=CHECKOUT, tree_sha=TREE, pr_head_sha='main')


def test_cli_refuses_overwrite_and_writes_private_report(tmp_path, monkeypatch):
    path = tmp_path/'evidence.json'; path.write_text(json.dumps(valid()))
    dest = tmp_path/'readiness.json'
    monkeypatch.setattr(gate, 'git_identity', lambda _root: (CHECKOUT, TREE))
    monkeypatch.setenv('GITHUB_HEAD_SHA', PR)
    assert gate.main(['--evidence', str(path), '--out', str(dest),
                      '--root', str(tmp_path)]) == 0
    report = json.loads(dest.read_text())
    assert report['claim_scope'] == 'real_java_mcp_mysql_deterministic_fixture_only'
    assert dest.stat().st_mode & 0o777 == 0o600
    assert gate.main(['--evidence', str(path), '--out', str(dest),
                      '--root', str(tmp_path)]) == 2
