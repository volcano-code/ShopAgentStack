"""Owner-scoped, read-only keyset pages. Cursors are positions, never authority."""
import base64
import binascii
import json

FOLDABLE_SQL = "('COMPLETED','FAILED','STOPPED')"
LANES = ("history", "attention")
MAX_PAGE_SIZE = 50


def _cursor(sid, lane, rid):
    raw = json.dumps([1, sid, lane, rid], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _anchor(token, sid, lane, error):
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 512:
            raise ValueError()
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        if (not isinstance(value, list) or len(value) != 4 or type(value[0]) is not int or
                value[:3] != [1, sid, lane] or not isinstance(value[3], str) or
                not 1 <= len(value[3]) <= 64 or _cursor(sid, lane, value[3]) != token):
            raise ValueError()
        return value[3]
    except (ValueError, UnicodeError, binascii.Error, TypeError):
        raise error("历史游标无效，请重新打开会话", 400) from None


def session_page(store, sid, member, *, limit=12, cursor=None, lane="history",
                 summary_only=False, error):
    """Each page is one SQLite read snapshot; separate pages see current statuses.

    History contains ALL statuses. Attention separately pages anything not safely
    finished, including unknown states. Bound run count, not per-run event bytes.
    Immutable (created, id) ordering avoids OFFSET drift and timestamp ties.
    """
    with store.db() as db:
        db.execute("BEGIN")
        owner = db.execute("SELECT title FROM sessions WHERE id=? AND member_id=?", (sid, member)).fetchone()
        if owner is None:
            raise error("会话不存在或无权访问", 404)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or lane not in LANES:
            raise error("历史分页参数无效", 400)
        where = "session_id=?"
        args = [sid]
        if lane == "attention":
            where += f" AND status NOT IN {FOLDABLE_SQL}"
        if cursor is not None:
            rid = _anchor(cursor, sid, lane, error)
            anchor = db.execute("SELECT created,id FROM runs WHERE id=? AND session_id=?", (rid, sid)).fetchone()
            if anchor is None:
                raise error("历史游标已失效，请重新打开会话", 400)
            where += " AND (created,id)<(?,?)"
            args.extend((anchor["created"], anchor["id"]))
        rows = db.execute(f"SELECT * FROM runs WHERE {where} ORDER BY created DESC,id DESC LIMIT ?", (*args, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = _cursor(sid, lane, rows[-1]["id"]) if more else None
        remaining = 0
        if more:
            last = rows[-1]
            remaining = db.execute(f"SELECT COUNT(*) FROM runs WHERE {where} AND (created,id)<(?,?)", (*args, last["created"], last["id"])).fetchone()[0]
        # Only selected payloads are decoded; one batched event query (not N+1).
        if type(summary_only) is not bool:
            raise error("历史请求参数无效", 400)
        runs = [dict(row, events=[], **({"events_complete": False} if summary_only else {}))
                for row in reversed(rows)]
        by_id = {run["id"]: run for run in runs}
        if runs and not summary_only:
            marks = ",".join("?" for _ in runs)
            for event in db.execute(f"SELECT id,run_id,kind,payload FROM events WHERE run_id IN ({marks}) ORDER BY id", tuple(by_id)):
                by_id[event["run_id"]]["events"].append({"id": event["id"], "kind": event["kind"], "data": json.loads(event["payload"])})
        has_active = db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('QUEUED','RUNNING','CONFIRMING','STOPPING') LIMIT 1", (sid,)).fetchone() is not None
        return {"id": sid, "title": owner["title"], "lane": lane, "runs": runs,
                "next_cursor": next_cursor, "remaining": remaining, "has_active": has_active}
