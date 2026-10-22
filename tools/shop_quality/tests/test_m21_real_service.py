"""No-network M2.1 real Agent API probe contracts and fail-closed semantics."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tools.shop_quality import m21_real_service as m21

ROOT = Path(__file__).resolve().parents[3]


def plan():
    return m21.plan_cases(ROOT / "evaluation/shopping-dialogues-v1.json",
                          ["dev-reference"])


def test_default_preflight_never_contacts_model_or_service(capsys):
    rc = m21.main(["--scenario", "dev-reference"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and report["mode"] == "preflight_only"
    assert report["turn_count"] == 2
    assert report["max_possible_model_attempts"] == 16
    assert report["paid_model_called"] is False and report["network_called"] is False
    assert "查一下远山" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("selection", [["reserved-size"], ["unknown-case"],
                                       ["dev-budget", "dev-budget"]])
def test_reserved_or_invalid_selections_are_rejected(selection):
    with pytest.raises(m21.ProbeError):
        m21.plan_cases(ROOT / "evaluation/shopping-dialogues-v1.json",
                       selection)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:18136/api/agent", "http://localhost:18136/api/agent",
    "http://192.168.1.10:18136/api/agent", "http://127.0.0.1:18136/private",
    "http://bad@127.0.0.1:18136/api/agent", "http://127.0.0.1:18136/api/agent?x=1",
    "http://127.0.0.1:18136/api/agent#foo",
])
def test_real_api_transport_only_accepts_literal_loopback(url):
    with pytest.raises(m21.ProbeError):
        m21.loopback_endpoint(url)


def test_explicit_live_cli_without_owner_credentials_never_reaches_network(monkeypatch, capsys):
    def denied(*args, **kwargs):
        pytest.fail("A live HTTP client must not be constructed without credentials")
    monkeypatch.setattr(m21, "LoopbackAgent", denied)
    args = ["--scenario", "dev-reference", "--execute-live",
            "--provider", "custom", "--consent", m21.CONSENT,
            "--isolated-ack", "I_VERIFIED_A_DISPOSABLE_STACK",
            "--max-model-attempts", "16"]
    assert m21.main(args, env={}) == 2
    assert not capsys.readouterr().out


def test_live_model_needs_both_explicit_consents_and_owned_endpoint():
    args = SimpleNamespace(execute_live=True, provider="custom",
                           consent=m21.CONSENT,
                           isolated_ack="I_VERIFIED_A_DISPOSABLE_STACK",
                           endpoint="http://127.0.0.1:18136/api/agent",
                           max_model_attempts=16, turn_count=2)
    with pytest.raises(m21.ProbeError):
        m21.approve_execution(args, {"SHOP_AGENT_STACK_M21_BEARER":"secret"*10})
    with pytest.raises(m21.ProbeError):
        m21.approve_execution(args, {"SHOP_AGENT_STACK_M21_PAID_OPT_IN":m21.CONSENT})
    good = {"SHOP_AGENT_STACK_M21_BEARER":"Bearer synthetic-safe-only",
            "SHOP_AGENT_STACK_M21_PAID_OPT_IN":m21.CONSENT}
    assert m21.approve_execution(args, good)[0].startswith("http://127.0.0.1")
    args.max_model_attempts = 15
    with pytest.raises(m21.ProbeError):
        m21.approve_execution(args, good)
    args.max_model_attempts = 16
    args.provider = "fixture"
    with pytest.raises(m21.ProbeError):
        m21.approve_execution(args, good)


class FakeAgent:
    def __init__(self, fail=None, status="COMPLETED", corrupt=False):
        self.calls = []
        self.sid = str(uuid4())
        self.rid = str(uuid4())
        self.fail = fail
        self.status = status
        self.corrupt = corrupt
        self.turn = 0

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail == len(self.calls):
            raise m21.ProbeError("agent_http_503")
        if method == "GET" and path == "/sessions":
            return []
        if method == "POST" and path == "/sessions":
            return {"id":self.sid}
        if method == "POST" and path.endswith("/runs"):
            self.turn += 1
            self.rid = str(uuid4())
            assert body["provider"] == "custom"
            return {"id":self.rid, "status":"QUEUED"}
        if path.endswith("?summary_only=true"):
            return {"id":self.rid, "status":self.status,
                    "events":[], "events_complete":False}
        if "/events/page?" in path:
            if self.corrupt:
                return {"events":[{"id":1,"kind":"tool","data":{}}],
                        "next_after":1, "has_more":True}
            return {"events":[{"id":1,"kind":"tool",
                               "data":{"name":"get_product","status":"started"}},
                              {"id":2,"kind":"assistant",
                               "data":{"text":"PRIVATE_ANSWER [G10003]"}}],
                    "next_after":2, "has_more":False}
        if path.endswith("/usage"):
            return {"call_attempts":2,"total_tokens":None,
                    "estimated_cost":None}
        raise AssertionError(path)


def test_real_service_contract_capture_has_no_raw_prompts_and_no_writes():
    fake = FakeAgent()
    result = m21.observe(plan(), fake, "custom", sleep=lambda _:None)
    assert result["status"] == "observed"
    assert result["observed_turns"] == 2
    assert result["answer_accuracy"] is None
    assert result["task_success_rate"] is None
    assert not result["business_write_safety_verified"]
    assert result["rows"][0]["citation_ids"] == ["G10003"]
    assert result["rows"][1]["model_call_attempts"] == 2
    assert all("PRIVATE_ANSWER" not in json.dumps(row) for row in result["rows"])
    assert all("查一下远山" not in json.dumps(row, ensure_ascii=False)
               for row in result["rows"])
    assert sum(method == "POST" for method, _, _ in fake.calls) == 3
    assert not any("/confirm" in path or "/stop" in path or "/reconcile" in path
                   for _, path, _ in fake.calls)


def test_network_error_after_run_post_has_no_replay_or_forged_completion():
    fake = FakeAgent(fail=3)
    result = m21.observe(plan(), fake, "custom", sleep=lambda _:None)
    assert result["status"] == "incomplete"
    assert result["failure_code"] == "agent_http_503"
    assert not result["rows"]
    assert sum(method == "POST" for method, _, _ in fake.calls) == 2
    assert result["answer_accuracy"] is None


def test_unfinished_or_pending_confirm_never_posts_next_turn():
    for status in ("WAITING_CONFIRMATION", "FAILED", "UNCERTAIN"):
        fake = FakeAgent(status=status)
        report = m21.observe(plan(), fake, "custom", sleep=lambda _:None)
        assert report["observed_turns"] == 1
        assert report["rows"][0]["status"] == status
        assert not any("/confirm" in path for _, path, _ in fake.calls)
        assert sum(method=="POST" for method, _, _ in fake.calls) == 2


def test_bad_event_page_fails_closed_without_reporting_answer_quality():
    fake=FakeAgent(corrupt=True)
    with pytest.raises(m21.ProbeError) as exc:
        m21.event_digest(fake, str(uuid4()))
    assert exc.value.args[0] != "PRIVATE_ANSWER"


def test_report_output_permissions_and_no_clobber(tmp_path):
    target = tmp_path / "evaluation.json"
    data = {"mode":"dry_test","cost":None}
    m21.private_write(target, data)
    assert target.read_text().find("dry_test")>=0
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(m21.ProbeError):
        m21.private_write(target, data)
