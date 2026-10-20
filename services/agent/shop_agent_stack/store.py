"""Single-instance P2 event store. SQLite is isolated from Java's MySQL business tables."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

# Bound persisted events and read-back responses; legacy oversized rows stay in SQLite.
MAX_EVENT_BYTES = 128 * 1024
MAX_PAGE_BYTES = 192 * 1024
MAX_EVENTS_PER_PAGE = 100
# A normal run cannot produce anywhere near this many business previews.
# A corrupt legacy run must fail closed rather than issue unlimited cancellations.
MAX_CANCELLATION_PREVIEWS = 16


class StoreError(Exception):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


class Store:
    def __init__(self, path=None):
        self.path = path or os.getenv("SHOP_AGENT_STACK_AGENT_DB", "/data/agent.sqlite")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, member_id INTEGER NOT NULL, title TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT NOT NULL, input TEXT NOT NULL, provider TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, UNIQUE(session_id,request_id));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id,id);
                CREATE INDEX IF NOT EXISTS idx_events_kind ON events(run_id,kind,id DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_history ON runs(session_id,created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_attention ON runs(session_id,created DESC,id DESC) WHERE status NOT IN ('COMPLETED','FAILED','STOPPED');
                CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(member_id,created);
                CREATE TABLE IF NOT EXISTS task_context(session_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
            for row in db.execute("SELECT id,status FROM runs WHERE status IN ('QUEUED','RUNNING','CONFIRMING','STOPPING')").fetchall():
                status = "UNCERTAIN" if row["status"] == "CONFIRMING" else "INTERRUPTED"
                db.execute("UPDATE runs SET status=? WHERE id=?", (status, row["id"]))
                self._event(db, row["id"], "state", {"status": status, "message": "服务已重启，请核实业务结果或重新提问"})

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db, run_id, kind, payload):
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > MAX_EVENT_BYTES or len(kind.encode("utf-8")) > 128:
            raise StoreError("事件内容超过单条大小限制", 400)
        db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)", (run_id, kind, serialized))

    def sessions(self, member):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id,title,created FROM sessions WHERE member_id=? ORDER BY created DESC LIMIT 100",(member,))]

    def new_session(self, member):
        sid = str(uuid4())
        with self.db() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)",(sid,member,"新对话",time.time()))
        return {"id":sid,"title":"新对话"}

    def delete_session(self, sid, member):
        # Serialize deletion with run creation and confirmation transitions.
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND member_id=?", (sid, member)).fetchone():
                raise StoreError("会话不存在或无权访问", 404)
            if db.execute("SELECT 1 FROM runs WHERE session_id=? AND status NOT IN ('COMPLETED','FAILED','STOPPED','INTERRUPTED')", (sid,)).fetchone():
                raise StoreError("请先停止执行、取消待确认操作，或核实业务提交结果后再删除对话", 409)
            db.execute("DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE session_id=?)", (sid,))
            db.execute("DELETE FROM task_context WHERE session_id=?", (sid,))
            db.execute("DELETE FROM runs WHERE session_id=?", (sid,))
            db.execute("DELETE FROM sessions WHERE id=? AND member_id=?", (sid, member))
        return {"deleted": True}

    def session(self, sid, member):
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND member_id=?",(sid,member)).fetchone()
            if not row:
                raise StoreError("会话不存在或无权访问",404)
            runs = [dict(r) for r in db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY created",(sid,))]
        for run in runs:
            run["events"] = self.events(run["id"])
        return {"id":sid,"title":row["title"],"runs":runs}

    def session_page(self, sid, member, *, limit=12, cursor=None, lane="history", summary_only=False):
        from .history_pages import session_page
        return session_page(self, sid, member, limit=limit, cursor=cursor, lane=lane,
                            summary_only=summary_only, error=StoreError)

    def create_run(self, sid, member, request_id, message, provider):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND member_id=?",(sid,member)).fetchone():
                raise StoreError("会话不存在或无权访问",404)
            previous = db.execute("SELECT id FROM runs WHERE session_id=? AND request_id=?",(sid,request_id)).fetchone()
            if previous:
                return previous["id"], False
            if db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('QUEUED','RUNNING','CONFIRMING','STOPPING')",(sid,)).fetchone():
                raise StoreError("当前会话仍在执行，请等待或停止后再发送")
            rid = str(uuid4())
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",(rid,sid,request_id,message,provider,"QUEUED",time.time()))
            db.execute("UPDATE sessions SET title=? WHERE id=? AND title='新对话'",(message[:28],sid))
            self._event(db,rid,"state",{"status":"QUEUED","message":"已接收请求"})
            return rid, True

    def run(self, rid, member):
        with self.db() as db:
            row = db.execute("SELECT r.* FROM runs r JOIN sessions s ON s.id=r.session_id WHERE r.id=? AND s.member_id=?",(rid,member)).fetchone()
        if not row:
            raise StoreError("执行不存在或无权访问",404)
        result = dict(row)
        result["events"] = self.events(rid)
        return result

    def run_header(self, rid, member):
        """Owner-scoped metadata for read-only recovery; no event materialization."""
        with self.db() as db:
            row = db.execute("SELECT r.* FROM runs r JOIN sessions s ON s.id=r.session_id "
                             "WHERE r.id=? AND s.member_id=?", (rid, member)).fetchone()
        if row is None:
            raise StoreError("执行不存在或无权访问", 404)
        return dict(row, events=[], events_complete=False)


    @staticmethod
    def _require_run_owner(db, rid, member):
        row = db.execute(
            "SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
            "WHERE r.id=? AND s.member_id=?", (rid, member)).fetchone()
        if row is None:
            raise StoreError("执行不存在或无权访问", 404)

    @staticmethod
    def _parse_preview(payload):
        """Never let legacy/corrupted preview data authorize a business call."""
        try:
            preview = json.loads(payload)
        except (ValueError, TypeError):
            raise StoreError("售后预览记录损坏，请核实原始操作状态", 409) from None
        if (not isinstance(preview, dict) or
                not isinstance(preview.get("id"), str) or not preview["id"]):
            raise StoreError("售后预览记录损坏，请核实原始操作状态", 409)
        return preview

    def latest_preview(self, rid, member):
        """Fetch only the latest authorized preview; never decode all run events."""
        with self.db() as db:
            db.execute("BEGIN")
            self._require_run_owner(db, rid, member)
            row = db.execute(
                "SELECT id,LENGTH(CAST(payload AS BLOB)) AS bytes "
                "FROM events WHERE run_id=? AND kind='preview' "
                "ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
            if row is None:
                raise StoreError("当前执行没有可确认操作", 409)
            if row["bytes"] > MAX_EVENT_BYTES:
                raise StoreError("历史售后预览超出安全读取上限，请核实原始操作状态", 409)
            payload = db.execute(
                "SELECT payload FROM events WHERE run_id=? AND id=?",
                (rid, row["id"])).fetchone()["payload"]
        return self._parse_preview(payload)

    def cancellation_preview_ids(self, rid, member):
        """Return all preview IDs only when the whole set fits a strict budget.

        No business cancellation must begin if previews are corrupt, oversized
        or too numerous. This prevents a partially inspected run from granting
        implicit permission to cancel just a subset of operations.
        """
        with self.db() as db:
            db.execute("BEGIN")
            self._require_run_owner(db, rid, member)
            rows = db.execute(
                "SELECT id,LENGTH(CAST(payload AS BLOB)) AS bytes "
                "FROM events WHERE run_id=? AND kind='preview' ORDER BY id "
                "LIMIT ?", (rid, MAX_CANCELLATION_PREVIEWS + 1)).fetchall()
            if len(rows) > MAX_CANCELLATION_PREVIEWS:
                raise StoreError("历史预览数量超过安全核实范围，请核实原始操作状态", 409)
            if any(row["bytes"] > MAX_EVENT_BYTES for row in rows):
                raise StoreError("历史售后预览超出安全读取上限，请核实原始操作状态", 409)
            if not rows:
                return []
            marks = ",".join("?" for _ in rows)
            payloads = {row["id"]: row["payload"] for row in db.execute(
                f"SELECT id,payload FROM events WHERE run_id=? AND id IN ({marks})",
                (rid, *(row["id"] for row in rows)))}
            return [self._parse_preview(payloads[row["id"]])["id"] for row in rows]

    def operation_recorded(self, rid, member, operation_id):
        """Owner-scoped idempotency evidence without loading any other events."""
        with self.db() as db:
            self._require_run_owner(db, rid, member)
            return db.execute(
                "SELECT 1 FROM events WHERE run_id=? AND kind='operation' AND "
                "CASE WHEN json_valid(payload) THEN "
                "json_extract(payload,'$.id')=? ELSE 0 END LIMIT 1",
                (rid, operation_id)).fetchone() is not None

    def run_status(self, rid, member):
        """Owner-scoped status lookup; never materializes the event stream."""
        with self.db() as db:
            row = db.execute("SELECT r.status FROM runs r JOIN sessions s ON s.id=r.session_id "
                             "WHERE r.id=? AND s.member_id=?", (rid, member)).fetchone()
        if row is None:
            raise StoreError("执行不存在或无权访问", 404)
        return row["status"]

    def events_page(self, rid, member, *, after=0, limit=100):
        """Owner-scoped, byte- and row-bounded event page.

        Oversized legacy events become explicit display-only error markers. No
        original events are changed, and cursors still move past their IDs.
        """
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= MAX_EVENTS_PER_PAGE:
            raise StoreError("事件分页参数无效", 400)
        with self.db() as db:
            db.execute("BEGIN")
            owner = db.execute("SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
                               "WHERE r.id=? AND s.member_id=?", (rid, member)).fetchone()
            if owner is None:
                raise StoreError("执行不存在或无权访问", 404)
            metadata = db.execute(
                "SELECT id,kind,LENGTH(CAST(payload AS BLOB)) AS size_bytes "
                "FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                (rid, after, limit + 1)).fetchall()
            selected = []
            budget = 48  # envelope overhead; include a generous per-row allowance
            for row in metadata[:limit]:
                kind_bytes = len(row["kind"].encode("utf-8"))
                oversize = row["size_bytes"] > MAX_EVENT_BYTES or kind_bytes > 128
                estimate = 256 if oversize else row["size_bytes"] + kind_bytes + 256
                if selected and budget + estimate > MAX_PAGE_BYTES:
                    break
                selected.append((row, oversize))
                budget += estimate
            ids = [r["id"] for r, omitted in selected if not omitted]
            payloads = {}
            if ids:
                marks = ",".join("?" for _ in ids)
                payloads = {r["id"]: r["payload"] for r in db.execute(
                    f"SELECT id,payload FROM events WHERE run_id=? AND id IN ({marks})",
                    (rid, *ids))}
        events = []
        for row, omitted in selected:
            if omitted:
                events.append({"id": row["id"], "kind": "error", "data": {
                    "message": "有一条旧进度记录超过安全读取上限，详情未自动加载；如涉及待确认操作请核实原始业务状态。",
                    "display_only": True, "oversized": True}})
            else:
                try:
                    data = json.loads(payloads[row["id"]])
                    if not isinstance(data, dict):
                        raise ValueError("event data is not an object")
                except (ValueError, TypeError):
                    events.append({"id": row["id"], "kind": "error", "data": {
                        "message": "一条旧进度记录格式损坏，详情未自动加载；涉及业务操作请核实原始状态。",
                        "display_only": True, "corrupt": True}})
                else:
                    events.append({"id": row["id"], "kind": row["kind"], "data": data})
        more = len(metadata) > len(selected)
        result = {"events": events, "next_after": events[-1]["id"] if events else after, "has_more": more}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_PAGE_BYTES:
            raise StoreError("事件页超出字节预算，请缩小读取范围", 400)
        return result

    def events(self, rid, after=0):
        with self.db() as db:
            return [{"id":r["id"],"kind":r["kind"],"data":json.loads(r["payload"])} for r in db.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id",(rid,after))]

    def emit(self,rid,kind,payload):
        with self.db() as db:
            self._event(db,rid,kind,payload)

    def state(self,rid,status,message=""):
        with self.db() as db:
            db.execute("UPDATE runs SET status=? WHERE id=?",(status,rid))
            self._event(db,rid,"state",{"status":status,"message":message})

    def begin_confirm(self,rid):
        with self.db() as db:
            changed=db.execute("UPDATE runs SET status='CONFIRMING' WHERE id=? AND status='WAITING_CONFIRMATION'",(rid,)).rowcount
            if not changed:
                raise StoreError("确认已处理或当前状态不可确认，请刷新核实")
            self._event(db,rid,"state",{"status":"CONFIRMING","message":"正在提交已确认操作"})

    def begin_stop(self,rid):
        with self.db() as db:
            if not db.execute("UPDATE runs SET status='STOPPING' WHERE id=? AND status IN ('QUEUED','RUNNING','WAITING_CONFIRMATION')",(rid,)).rowcount:
                raise StoreError("执行状态已变化，请刷新核实")

    def history(self,sid,member,current_rid):
        """Preserve model context exactly, reading only assistant final events.

        The previous implementation decoded every preview, tool trace and
        intermediate delta in the last ten runs, even though none can become a
        model message. Keep the same run ordering and 3,000-character slice.
        """
        messages=[]
        with self.db() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND member_id=?",(sid,member)).fetchone():
                raise StoreError("会话不存在或无权访问",404)
            runs = db.execute("SELECT * FROM runs WHERE session_id=? AND id!=? AND status IN ('COMPLETED','WAITING_CONFIRMATION') ORDER BY created DESC,rowid DESC LIMIT 10",(sid,current_rid)).fetchall()
            for run in reversed(runs):
                messages.append({"role":"user","content":run["input"]})
                for event in db.execute(
                    "SELECT payload FROM events WHERE run_id=? AND kind='assistant' ORDER BY id",
                    (run["id"],)):
                    messages.append({"role":"assistant","content":json.loads(event["payload"])["text"][:3000]})
        return messages

    def task_context(self, sid, member):
        from .context import empty
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND member_id=?",(sid,member)).fetchone():
                raise StoreError("会话不存在或无权访问",404)
            row = db.execute("SELECT payload FROM task_context WHERE session_id=?", (sid,)).fetchone()
        return json.loads(row["payload"]) if row else empty()

    def finish_task(self, rid, member, task, status, message):
        # Context and terminal state commit together. Stopped/failed runs never promote their claims.
        with self.db() as db:
            row = db.execute("SELECT r.session_id FROM runs r JOIN sessions s ON s.id=r.session_id WHERE r.id=? AND s.member_id=? AND r.status='RUNNING'", (rid,member)).fetchone()
            if not row:
                raise StoreError("任务已停止或无权更新")
            db.execute("INSERT INTO task_context VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload", (row["session_id"],json.dumps(task,ensure_ascii=False)))
            db.execute("UPDATE runs SET status=? WHERE id=?", (status,rid))
            self._event(db,rid,"state",{"status":status,"message":message})
