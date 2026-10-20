"""M2.3 executed-contract coverage and disposable admission evidence tests."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from tools.shop_quality.m23_evidence import REQUIRED_CASES, inspect_junit,     produce, summarize_trials


def write_report(path: Path, *, missing=None, duplicate=None, skipped=None,
                 failed=None, include_noise=False) -> None:
    root = ET.Element("testsuite")
    if include_noise:
        ET.SubElement(root, "testcase", classname="tests.test_unrelated",
                      name="test_unrelated")
    for module, names in REQUIRED_CASES.items():
        for name in names:
            key = (module, name)
            if key == missing:
                continue
            case = ET.SubElement(root, "testcase",
                                 classname=f"tests.{module}", name=name)
            if key == skipped:
                ET.SubElement(case, "skipped").text = "SECRET_SHOULD_NOT_ECHO"
            if key == failed:
                ET.SubElement(case, "failure").text = "PRIVATE_BODY"
            if key == duplicate:
                ET.SubElement(root, "testcase",
                              classname=f"tests.{module}", name=name)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def first_case():
    module = next(iter(REQUIRED_CASES))
    return module, REQUIRED_CASES[module][0]


def test_only_named_executed_agent_junit_passes(tmp_path):
    p = tmp_path / "real.xml"
    write_report(p, include_noise=True)
    summary = inspect_junit(p)
    total = sum(len(names) for names in REQUIRED_CASES.values())
    assert summary["all_required_passed"]
    assert summary["required_case_count"] == total
    assert summary["observed_case_count"] == total
    assert summary["suite_case_count"] == total + 1
    assert len(summary["required_cases"]) == total


@pytest.mark.parametrize("issue", ["missing", "duplicate", "skipped", "failed"])
def test_missing_repeated_skipped_or_failed_required_case_fails_closed(tmp_path, issue):
    p = tmp_path / "strict.xml"
    write_report(p, **{issue: first_case()})
    with pytest.raises(ValueError):
        inspect_junit(p)


def test_empty_malformed_or_missing_junit_rejected(tmp_path):
    with pytest.raises(ValueError):
        inspect_junit(tmp_path / "absent.xml")
    p = tmp_path / "report.xml"
    p.write_text("<testsuite />", encoding="utf-8")
    with pytest.raises(ValueError):
        inspect_junit(p)
    p.write_text("<testsuite><testcase", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        inspect_junit(p)


def good_exercise(**kwargs):
    ceiling = 3 if kwargs["owner_mode"] == "same" else 8
    accepted = min(kwargs["requests"], ceiling)
    return {
        "scope": "disposable_sqlite_admission_only",
        "owner_mode": kwargs["owner_mode"],
        "accepted": accepted,
        "rejected_429": kwargs["requests"] - accepted,
        "persisted_active": accepted,
        "unexpected_errors": 0,
        "p50_ms": 1.1, "p95_ms": 2.2,
        "paid_models_called": False,
        "production_capacity_verified": False,
    }


def test_bounded_repeated_trials_and_source_bearing_report(tmp_path):
    p = tmp_path / "report.xml"
    write_report(p)
    data = produce(p, trials=2, requests=8, workers=2,
                   exercise=good_exercise,
                   source={"checkout_sha": "a"*40, "tree_sha": "b"*40,
                           "pr_head_sha": None})
    assert data["schema_version"] == 1
    assert data["admission"]["same"]["accepted_per_trial"] == [3, 3]
    assert data["admission"]["distinct"]["accepted_per_trial"] == [8, 8]
    assert data["admission"]["distinct"]["median_trial_p95_ms"] == 2.2
    assert data["limitations"]["actual_model_cost_verified"] is False
    assert data["source"]["checkout_sha"] == "a"*40


@pytest.mark.parametrize("field,value", [
    ("accepted", 7), ("rejected_429", 0), ("persisted_active", 6),
    ("unexpected_errors", 1), ("paid_models_called", True),
    ("production_capacity_verified", True), ("p95_ms", -1),
])
def test_changed_invariants_or_claims_cannot_pass(field, value):
    def changed(**kwargs):
        result = good_exercise(**kwargs)
        result[field] = value
        return result
    with pytest.raises(ValueError):
        summarize_trials(changed, trials=2, requests=40, workers=4)


@pytest.mark.parametrize("trials,requests,workers", [
    (1, 40, 12), (9, 40, 12), (2, 7, 12), (2, 65, 12),
    (2, 40, 0), (2, 40, 17),
])
def test_oversized_or_invalid_benchmark_settings_refused(trials, requests, workers):
    with pytest.raises(ValueError):
        summarize_trials(good_exercise, trials=trials,
                         requests=requests, workers=workers)
