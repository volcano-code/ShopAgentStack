"""Deterministic unit checks; HTTP probes below are explicit test doubles."""
from io import BytesIO
import json
from urllib.error import URLError

import pytest

from tools.shop_observability import wait_ready as w


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(w.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(w.time, "sleep", clock.sleep)
    return clock


def test_all_endpoints_must_succeed_in_one_pass(monkeypatch, clock):
    probes = []
    def probe(name, timeout):
        probes.append((name, timeout))
        return name != "tempo" or clock.now >= 0.5
    monkeypatch.setattr(w, "probe", probe)
    result = w.wait_ready(3)
    assert result["ready"] and result["attempts"] == 2
    assert len(probes) == 6
    assert result["claim_scope"] == "health_only_not_trace_delivery"


def test_stale_success_cannot_pass(monkeypatch, clock):
    monkeypatch.setattr(w, "probe", lambda name, timeout:
                        name == "collector" if clock.now == 0 else name != "collector")
    assert not w.wait_ready(1)["ready"]


def test_hung_probe_shares_one_deadline(monkeypatch, clock):
    timeouts = []
    def probe(name, timeout):
        timeouts.append(timeout)
        clock.sleep(timeout)
        return False
    monkeypatch.setattr(w, "probe", probe)
    assert not w.wait_ready(3)["ready"]
    assert timeouts == [2, 1] and clock.now == 3


def test_success_after_deadline_does_not_pass(monkeypatch, clock):
    def probe(name, timeout):
        clock.sleep(2)
        return True
    monkeypatch.setattr(w, "probe", probe)
    assert not w.wait_ready(1)["ready"]


@pytest.mark.parametrize("timeout", [0, -1, 601, float("inf"), float("nan")])
def test_invalid_budget(timeout):
    with pytest.raises(ValueError):
        w.wait_ready(timeout)


@pytest.mark.parametrize("status", [200, 204, 302, 500])
def test_http_status_no_proxy_and_no_response_body(monkeypatch, status):
    class Response(BytesIO):
        def read(self, *args):
            raise AssertionError("health response bodies must not be read")
    response = Response(b"synthetic-private-body")
    response.status = status
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == w.ENDPOINTS["tempo"]
            assert timeout == 0.7
            return response
    def opener(*handlers):
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], w.NoRedirect)
        return Opener()
    monkeypatch.setattr(w, "build_opener", opener)
    assert w.probe("tempo", 0.7) == (status == 200)
    assert response.closed


def test_redirects_are_not_followed():
    assert w.NoRedirect().redirect_request(None, None, 302, None, {}, "https://example.org") is None


def test_network_error_is_not_logged(monkeypatch, capsys):
    class Opener:
        def open(self, *args, **kwargs):
            raise URLError("synthetic-secret-body")
    monkeypatch.setattr(w, "build_opener", lambda *args: Opener())
    assert not w.probe("collector", 1)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("ready,code", [(True, 0), (False, 1)])
def test_cli_evidence_and_no_overwrite(monkeypatch, tmp_path, ready, code):
    monkeypatch.setattr(w, "wait_ready", lambda timeout: {"ready": ready})
    path = tmp_path / "health.json"
    assert w.main(["--out", str(path)]) == code
    assert json.loads(path.read_text())["ready"] == ready
    def unexpected(_):
        raise AssertionError("existing evidence must fail before probing")
    monkeypatch.setattr(w, "wait_ready", unexpected)
    assert w.main(["--out", str(path)]) == 2
    assert json.loads(path.read_text())["ready"] == ready


def test_invalid_cli_does_not_create_evidence(tmp_path):
    path = tmp_path / "health.json"
    with pytest.raises(SystemExit) as exc:
        w.main(["--timeout", "nan", "--out", str(path)])
    assert exc.value.code == 2 and not path.exists()
