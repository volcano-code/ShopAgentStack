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
    events = store.events_page(rid, 7, after=cursor, limit=100)["events"]
    assert events[-1]["kind"] == "state"
    assert events[-1]["data"]["status"] == "COMPLETED"


def test_event_page_does_not_mutate_db(tmp_path):
    store, rid = seed(tmp_path)
    before = store.events(rid)
    store.events_page(rid, 7, limit=3)
    store.run_status(rid, 7)
    assert store.events(rid) == before
