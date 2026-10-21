"""Acceptance is based on executed tests AND independent database readback."""
from __future__ import annotations

import json
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

BROWSER_TESTS = {
    "M13 browser confirmation to staff refund and replay",
    "M13 forged confirmation and cancellation write nothing",
    "M13 unconfigured model cannot silently fall back",
}
MCP_TESTS = {
    "test_real_mcp_confirmation_ownership_and_replay",
    "test_cancelled_operation_and_invalid_execution_grant",
    "test_real_mcp_lost_ack_replay_has_single_pending_case",
    "test_m21_real_agent_fixture_on_real_java_mcp_mysql",
}


def test_report(path: Path, expected: set[str]) -> dict:
    """Missing, skipped, repeated, failing or unexpected cases fail the gate."""
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    names = [case.get("name", "") for case in cases]
    if set(names) != expected or len(names) != len(expected):
        raise ValueError("required test identity/coverage mismatch")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("required tests failed or skipped")
    return {"executed": len(cases), "passed": len(cases), "skipped": 0,
            "test_names": sorted(expected)}


def receipt(path: Path, refunded: bool) -> dict:
    if path.stat().st_size > 4096:
        raise ValueError("oversized receipt")
    data = json.loads(path.read_text())
    expected = {"order_id", "operation_id", "case_id"} if refunded else {"order_id", "operation_id"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("receipt must contain identifiers only")
    for key in ("order_id", "case_id"):
        if key in data and (type(data[key]) is not int or not 0 < data[key] < 2**53):
            raise ValueError("invalid business identifier")
    if not isinstance(data["operation_id"], str) or str(uuid.UUID(data["operation_id"])) != data["operation_id"]:
        raise ValueError("invalid operation identifier")
    return data


def readback_sql(data: dict) -> str:
    # Data must have gone through receipt(); defensive validation prevents SQL injection.
    oid, operation = data["order_id"], data["operation_id"]
    if type(oid) is not int or not 0 < oid < 2**53 or str(uuid.UUID(operation)) != operation:
        raise ValueError("invalid SQL identifiers")
    return f"""SELECT JSON_OBJECT(
        'order_id',o.id,'order_status',o.status,'pay_amount',o.pay_amount,
        'sale_count',(SELECT COUNT(*) FROM shop_agent_stack_after_sale WHERE order_id=o.id),
        'refunded_count',(SELECT COUNT(*) FROM shop_agent_stack_after_sale WHERE order_id=o.id AND status='REFUNDED'),
        'case_id',(SELECT MIN(id) FROM shop_agent_stack_after_sale WHERE order_id=o.id),
        'ledger_count',(SELECT COUNT(*) FROM shop_agent_stack_simulated_refund WHERE order_id=o.id),
        'ledger_case_id',(SELECT MIN(case_id) FROM shop_agent_stack_simulated_refund WHERE order_id=o.id),
        'ledger_amount',(SELECT COALESCE(SUM(amount),0) FROM shop_agent_stack_simulated_refund WHERE order_id=o.id),
        'job_count',(SELECT COUNT(*) FROM shop_agent_stack_refund_job j JOIN shop_agent_stack_after_sale a ON j.case_id=a.id WHERE a.order_id=o.id),
        'done_jobs',(SELECT COUNT(*) FROM shop_agent_stack_refund_job j JOIN shop_agent_stack_after_sale a ON j.case_id=a.id WHERE a.order_id=o.id AND j.status='DONE'),
        'refund_events',(SELECT COUNT(*) FROM shop_agent_stack_after_sale_event e JOIN shop_agent_stack_after_sale a ON e.case_id=a.id WHERE a.order_id=o.id AND e.action='REFUNDED'),
        'operation_count',(SELECT COUNT(*) FROM shop_agent_stack_operation WHERE order_id=o.id),
        'operation_status',(SELECT status FROM shop_agent_stack_operation WHERE id='{operation}' AND order_id=o.id),
        'operation_case',(SELECT case_id FROM shop_agent_stack_operation WHERE id='{operation}' AND order_id=o.id),
        'consumed',(SELECT COUNT(*) FROM shop_agent_stack_operation WHERE id='{operation}' AND order_id=o.id AND consumed_at IS NOT NULL)
    ) FROM oms_order o WHERE o.id={oid};"""


def verify_readback(data: dict, actual: dict, refunded: bool) -> dict:
    expected = {
        "order_id": data["order_id"], "order_status": 4 if refunded else 1,
        "pay_amount": 49.9, "sale_count": int(refunded), "refunded_count": int(refunded),
        "case_id": data.get("case_id"), "ledger_count": int(refunded),
        "ledger_case_id": data.get("case_id"),
        "ledger_amount": 49.9 if refunded else 0, "job_count": int(refunded),
        "done_jobs": int(refunded), "refund_events": int(refunded), "operation_count": 1,
        "operation_status": "SUCCEEDED" if refunded else "CANCELLED",
        "operation_case": data.get("case_id"), "consumed": int(refunded),
    }
    if not isinstance(actual, dict) or set(actual) != set(expected):
        raise ValueError("incomplete database evidence")
    for key, value in expected.items():
        if actual[key] != value or isinstance(actual[key], bool):
            raise ValueError(f"database invariant failed: {key}")
    return {"verified": True, **expected}


def verify_pending_submission(data: dict, actual: dict) -> dict:
    """Independent MySQL assertion for a successful, not-yet-approved after-sale.

    Java operation must be consumed only once; no simulated refund is allowed
    before staff review/approval. The database snapshot has the same 18 fields
    as readback_sql() for the existing refund/cancel acceptance.
    """
    expected = {
        "order_id": data["order_id"], "order_status": 1,
        "pay_amount": 49.9, "sale_count": 1, "refunded_count": 0,
        "case_id": data["case_id"], "ledger_count": 0,
        "ledger_case_id": None, "ledger_amount": 0,
        "job_count": 0, "done_jobs": 0, "refund_events": 0,
        "operation_count": 1, "operation_status": "SUCCEEDED",
        "operation_case": data["case_id"], "consumed": 1,
    }
    if not isinstance(actual, dict) or set(actual) != set(expected):
        raise ValueError("incomplete lost-ack independent database evidence")
    for key, value in expected.items():
        if actual[key] != value or isinstance(actual[key], bool):
            raise ValueError(f"lost-ack invariant failed: {key}")
    return {"verified": True, **expected}


def verify_m21_real_fixture(record: dict) -> dict:
    """Only predetermined non-sensitive fixture evidence may become CI evidence."""
    expected = {"scope":"M2.1_real_agent_fixture_real_java_mcp_mysql",
        "fixture_only":True, "paid_model_called":False,
        "real_agent_http":True, "owner_enforced":True,
        "real_java_mcp_order_revalidated":True, "bounded_pages":True,
        "no_business_write_events":True, "request_idempotency":True,
        "answer_accuracy_measured":False, "funds_safety_verified":False}
    if not isinstance(record, dict) or record != expected:
        raise ValueError("incomplete or overclaimed M2.1 real-service fixture")
    return {**expected, "verified": True}
