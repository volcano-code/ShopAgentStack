"""Real SQLite history pages. No models, Java, network, or business writes."""
import base64
import json
import sqlite3
from contextlib import contextmanager
from uuid import UUID
import pytest
from shop_agent_stack.store import Store, StoreError
from shop_agent_stack.history_pages import _cursor


def seed(tmp_path, count=120, statuses=None, ties=False):
    store = Store(str(tmp_path / "pages.sqlite"))
    sid = store.new_session(1)["id"]
    with store.db() as db:
        for i in range(count):
            rid = str(UUID(int=i + 1))
            status = (statuses or {}).get(i, "COMPLETED")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (rid, sid, f"r{i}", f"问题 {i}", "fixture", status, 1.0 if ties else i + .25))
            store._event(db, rid, "assistant", {"text": "合成内容 " * 50})
            store._event(db, rid, "state", {"status": status})
    return store, sid


def collect(store, sid, lane="history", limit=12):
    cursor = None; pages = []
    for _ in range(1000):
        page = store.session_page(sid, 1, lane=lane, limit=limit, cursor=cursor)
        pages.append(page)
        if page["next_cursor"] is None:
            return pages
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    pytest.fail("pagination did not terminate")


@pytest.mark.parametrize("ties", [False, True])
@pytest.mark.parametrize("size", [1, 12, 50])
def test_all_rows_once_with_timestamp_ties(tmp_path, ties, size):
    store, sid = seed(tmp_path, ties=ties)
    pages = collect(store, sid, limit=size)
    rows = [r for page in reversed(pages) for r in page["runs"]]
    assert [r["id"] for r in rows] == [str(UUID(int=i + 1)) for i in range(120)]
    assert all(len(p["runs"]) <= size for p in pages)
    assert pages[0]["remaining"] == 120 - size
    assert pages[-1]["remaining"] == 0
    assert all(len(r["events"]) == 2 for r in rows)


def test_empty_page(tmp_path):
    store, sid = seed(tmp_path, 0)
    for lane in ("history", "attention"):
        assert store.session_page(sid, 1, lane=lane) == {"id": sid, "title": "新对话", "lane": lane,
            "runs": [], "next_cursor": None, "remaining": 0, "has_active": False}


@pytest.mark.parametrize("limit", [0, -1, 51, 1000000, True, 1.5, "12", None])
def test_invalid_limits_are_not_unbounded(tmp_path, limit):
    store, sid = seed(tmp_path, 1)
    with pytest.raises(StoreError) as e: store.session_page(sid, 1, limit=limit)
    assert e.value.code == 400


@pytest.mark.parametrize("token", ["", "=", "no%%", "A"*513, "AAAA", 123,
    base64.urlsafe_b64encode(b'null').decode().rstrip("="),
    base64.urlsafe_b64encode(b'{"secret":"never-echo-me"}').decode().rstrip("=")])
def test_invalid_cursor_does_not_echo_inputs(tmp_path, token):
    store, sid = seed(tmp_path, 1)
    with pytest.raises(StoreError) as e: store.session_page(sid, 1, cursor=token)
    assert e.value.code == 400 and "never-echo-me" not in str(e.value)


def test_cursor_is_bound_to_session_lane_and_existing_anchor(tmp_path):
    store, sid = seed(tmp_path)
    token = store.session_page(sid, 1)["next_cursor"]
    other = store.new_session(1)["id"]
    for target, lane, cursor in [(other, "history", token), (sid, "attention", token),
        (other, "history", _cursor(other, "history", str(UUID(int=1))))]:
        with pytest.raises(StoreError) as e: store.session_page(target, 1, lane=lane, cursor=cursor)
        assert e.value.code == 400
    with pytest.raises(StoreError) as e: store.session_page(sid, 2, cursor="invalid-sensitive")
    assert e.value.code == 404
    with pytest.raises(StoreError) as e: store.session_page("absent", 1)
    assert e.value.code == 404


def test_attention_is_paged_and_keeps_unknown_and_interrupted(tmp_path):
    statuses = {i: ["WAITING_CONFIRMATION", "UNCERTAIN", "INTERRUPTED", "FUTURE_STATE", "RUNNING"][i % 5] for i in range(31)}
    store, sid = seed(tmp_path, statuses=statuses)
    pages = collect(store, sid, "attention")
    runs = [r for p in pages for r in p["runs"]]
    assert len(runs) == 31 and pages[0]["remaining"] == 19
    assert set(r["status"] for r in runs) == set(statuses.values())
    assert all(p["has_active"] for p in pages)
    assert all(r["input"] not in {f"问题 {i}" for i in range(31, 120)} for r in runs)


def test_new_newest_row_does_not_shift_existing_page_boundary(tmp_path):
    store, sid = seed(tmp_path, count=24)
    first = store.session_page(sid, 1)
    rid, _ = store.create_run(sid, 1, "new", "newest", "fixture")
    store.state(rid, "COMPLETED")
    second = store.session_page(sid, 1, cursor=first["next_cursor"])
    assert len(second["runs"]) == 12 and second["next_cursor"] is None
    assert not set(r["id"] for r in first["runs"]) & set(r["id"] for r in second["runs"])
    assert rid not in {r["id"] for r in second["runs"]}
    assert store.session_page(sid, 1)["runs"][-1]["id"] == rid


def test_attention_cursor_remains_usable_after_anchor_completed(tmp_path):
    store, sid = seed(tmp_path, 25, {i: "WAITING_CONFIRMATION" for i in range(25)})
    first = store.session_page(sid, 1, lane="attention")
    store.state(first["runs"][0]["id"], "COMPLETED")
    second = store.session_page(sid, 1, lane="attention", cursor=first["next_cursor"])
    assert len(second["runs"]) == 12
    for r in second["runs"]: store.state(r["id"], "STOPPED")
    last = store.session_page(sid, 1, lane="attention", cursor=second["next_cursor"])
    assert len(last["runs"]) == 1


def test_one_snapshot_covers_status_and_event_payloads(tmp_path, monkeypatch):
    store, sid = seed(tmp_path, 1, {0: "RUNNING"})
    original = store.db
    fired = False
    @contextmanager
    def database():
        with original() as db:
            def callback(sql):
                nonlocal fired
                if "SELECT id,run_id,kind,payload" in sql and not fired:
                    fired = True
                    with sqlite3.connect(store.path) as writer:
                        writer.execute("UPDATE runs SET status='COMPLETED'")
                        writer.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)", (str(UUID(int=1)), "state", '{"status":"COMPLETED"}'))
            db.set_trace_callback(callback)
            yield db
    monkeypatch.setattr(store, "db", database)
    page = store.session_page(sid, 1)
    assert fired and page["has_active"]
    assert page["runs"][0]["status"] == "RUNNING" and len(page["runs"][0]["events"]) == 2
    assert store.session_page(sid, 1)["runs"][0]["status"] == "COMPLETED"


def test_page_avoids_decoding_unselected_events_and_n_plus_one(tmp_path, monkeypatch):
    store, sid = seed(tmp_path)
    with store.db() as db:
        db.execute("UPDATE events SET payload='not-json' WHERE run_id=?", (str(UUID(int=1)),))
    monkeypatch.setattr(store, "events", lambda *_: pytest.fail("must use batched transaction"))
    page = store.session_page(sid, 1)
    assert len(page["runs"]) == 12
    assert all(r["id"] != str(UUID(int=1)) for r in page["runs"])


def test_legacy_api_model_context_and_confirm_state_are_unchanged(tmp_path):
    store, sid = seed(tmp_path, 25)
    rid = str(UUID(int=1)); store.state(rid, "WAITING_CONFIRMATION")
    store.emit(rid, "preview", {"id": "synthetic-operation", "confirmationToken": "synthetic-only"})
    before = store.history(sid, 1, "not-this-run")
    page = store.session_page(sid, 1, lane="attention")
    assert page["runs"][0]["events"][-1]["data"]["confirmationToken"] == "synthetic-only"
    assert len(store.session(sid, 1)["runs"]) == 25
    assert store.history(sid, 1, "not-this-run") == before
    store.begin_confirm(rid)
    assert store.session_page(sid, 1, lane="attention")["runs"][0]["status"] == "CONFIRMING"
    with pytest.raises(StoreError): store.begin_confirm(rid)


def test_reopen_additive_indexes_and_query_plan(tmp_path):
    store, sid = seed(tmp_path, 100)
    store = Store(store.path)
    with store.db() as db:
        plan = str([tuple(x) for x in db.execute("EXPLAIN QUERY PLAN SELECT * FROM runs WHERE session_id=? AND (created,id)<(?,?) ORDER BY created DESC,id DESC LIMIT 13", (sid, 90, 'x'))])
    assert "idx_runs_history" in plan and "OFFSET" not in plan
    assert len(store.session_page(sid, 1)["runs"]) == 12
