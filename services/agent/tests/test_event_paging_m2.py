"""SQLite event-page contract: bounded reads, identity, terminal tail, stable cursor."""
import pytest
from shop_agent_stack.store import Store, StoreError


def seed(tmp_path):
    store = Store(str(tmp_path / "agent.sqlite"))
    sid = store.new_session(7)["id"]
    rid, _ = store.create_run(sid, 7, "req", "hello", "fixture")
    for index in range(231):
        store.emit(rid, "tool", {"sequence": index})
    return store, rid


def test_event_pages_are_bounded_exact_and_ordered(tmp_path):
    store, rid = seed(tmp_path)
    cursor = 0
    ids = []
    for size in (100, 100, 100):
        page = store.events_page(rid, 7, after=cursor, limit=size)
        assert len(page["events"]) <= size
        ids.extend(e["id"] for e in page["events"])
        cursor = page["next_after"]
        if not page["has_more"]:
            break
    assert ids == sorted(set(ids))
    assert len(ids) == 232  # queued-state event + 231 tool events
    assert not store.events_page(rid, 7, after=cursor)["events"]


def test_owner_check_applies_even_to_empty_page(tmp_path):
    store, rid = seed(tmp_path)
    for user in (8, 0):
        with pytest.raises(StoreError) as exc:
            store.events_page(rid, user, after=10**12)
        assert exc.value.code == 404
        with pytest.raises(StoreError):
            store.run_status(rid, user)


@pytest.mark.parametrize("after,limit", [(-1, 1), (0, 0), (0, 101), (True, 1), (0, 1.5)])
def test_invalid_arguments_rejected(tmp_path, after, limit):
    store, rid = seed(tmp_path)
    with pytest.raises(StoreError) as exc:
        store.events_page(rid, 7, after=after, limit=limit)
    assert exc.value.code == 400


def test_terminal_event_visible_after_old_cursor(tmp_path):
    store, rid = seed(tmp_path)
    cursor = store.events_page(rid, 7, after=0, limit=100)["next_after"]
    store.state(rid, "COMPLETED", "done")
    assert store.run_status(rid, 7) == "COMPLETED"
    remaining = []
    while True:
        page = store.events_page(rid, 7, after=cursor, limit=100)
        remaining.extend(page["events"])
        cursor = page["next_after"]
        if not page["has_more"]:
            break
    assert remaining[-1]["kind"] == "state"
    assert remaining[-1]["data"]["status"] == "COMPLETED"


def test_event_page_does_not_mutate_db(tmp_path):
    store, rid = seed(tmp_path)
    before = store.events(rid)
    store.events_page(rid, 7, limit=3)
    store.run_status(rid, 7)
    assert store.events(rid) == before

def test_byte_budget_for_large_page_and_stable_tail(tmp_path):
    import json
    from shop_agent_stack.store import MAX_PAGE_BYTES
    store = Store(str(tmp_path / "bytes.sqlite"))
    sid = store.new_session(7)["id"]
    rid, _ = store.create_run(sid, 7, "byte-case", "test", "fixture")
    for i in range(90):
        store.emit(rid, "assistant_delta", {"text": "数据" * 1900, "sequence": i})
    seen = []
    after = 0
    for _ in range(20):
        page = store.events_page(rid, 7, after=after)
        assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= MAX_PAGE_BYTES
        assert len(page["events"]) <= 100
        assert page["next_after"] > after
        seen.extend(e["id"] for e in page["events"])
        after = page["next_after"]
        if not page["has_more"]:
            break
    assert seen == [e["id"] for e in store.events(rid)]


def test_large_new_event_rejected_without_mutation(tmp_path):
    from shop_agent_stack.store import MAX_EVENT_BYTES
    store, rid = seed(tmp_path)
    original_count = len(store.events(rid))
    with pytest.raises(StoreError) as exc:
        store.emit(rid, "assistant", {"text": "x" * (MAX_EVENT_BYTES + 1)})
    assert exc.value.code == 400
    assert len(store.events(rid)) == original_count


def test_legacy_oversized_event_has_explicit_marker_and_cursor_progress(tmp_path):
    import json
    from shop_agent_stack.store import MAX_EVENT_BYTES, MAX_PAGE_BYTES
    store, rid = seed(tmp_path)
    cursor = store.events(rid)[-1]["id"]
    with store.db() as db:
        db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)",
                   (rid, "assistant", json.dumps({"text": "x" * (MAX_EVENT_BYTES + 100)})))
    page = store.events_page(rid, 7, after=cursor)
    assert len(page["events"]) == 1 and not page["has_more"]
    row = page["events"][0]
    assert row["kind"] == "error" and row["data"]["oversized"] is True
    assert row["data"]["display_only"] is True
    assert row["id"] == page["next_after"] > cursor
    assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) < MAX_PAGE_BYTES


@pytest.mark.asyncio
async def test_sse_delivers_real_frames_with_terminal_tail(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module("shop_agent_stack.app")
    store = Store(str(tmp_path / "sse.sqlite"))
    sid = store.new_session(7)["id"]
    rid, _ = store.create_run(sid, 7, "sse", "test", "fixture")
    for i in range(205):
        store.emit(rid, "tool", {"sequence": i})
    store.state(rid, "COMPLETED", "done")
    async def identity(_header):
        return {"memberId": 7}
    class Request:
        async def is_disconnected(self):
            return False
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "identity", identity)
    response = await module.events(rid, Request(), 0, "synthetic-auth")
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == 207  # queued + 205 tools + terminal
    assert all(frame.startswith("id: ") and "\\n" not in frame for frame in frames)
    assert all(frame.endswith("\n\n") and "\ndata: " in frame for frame in frames)
    parsed = [json.loads(frame.split("\ndata: ", 1)[1].strip()) for frame in frames]
    assert parsed[-1]["kind"] == "state"
    assert parsed[-1]["data"]["status"] == "COMPLETED"
    assert [e["id"] for e in parsed] == sorted({e["id"] for e in parsed})
