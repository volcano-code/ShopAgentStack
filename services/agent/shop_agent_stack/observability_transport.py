"""Host-owned per-message context bridge across the MCP SDK's background writer.

The SDK generates JSON-RPC IDs before it enqueues messages. Capture only those IDs
and the CURRENT span's traceparent there, then bind the matching HTTP request in
the writer task. Never mutate the message, arguments, grant or shared client headers.
"""
from __future__ import annotations
from collections import OrderedDict
import json
from typing import Any
from .observability import _ACTIVE


class MCPTraceBridge:
    LIMIT = 128  # Far above one run's bounded tools; never store payloads or credentials.

    def __init__(self) -> None:
        self.runtime = _ACTIVE.get()
        self.parents: OrderedDict[tuple[str, int | str], str] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.runtime is not None and not self.runtime._closed

    @staticmethod
    def key(method: Any, request_id: Any) -> tuple[str, int | str] | None:
        if not isinstance(method, str) or len(method) > 128:
            return None
        if type(request_id) is int and 0 <= request_id < 2**63:
            return method, request_id
        if type(request_id) is str and 0 < len(request_id) <= 64:
            return method, request_id
        return None

    def capture(self, message: Any) -> tuple[str, int | str] | None:
        if not self.enabled:
            return None
        root = message.message.root  # The pinned SDK's SessionMessage, not LLM data.
        key = self.key(getattr(root, "method", None), getattr(root, "id", None))
        if key is None:
            return None  # Notifications have no request ID.
        runtime = _ACTIVE.get()
        # A different lifespan/connection must not borrow this connection's context.
        value = runtime.telemetry.outbound_headers().get("traceparent") if runtime is self.runtime else None
        self.parents.pop(key, None)
        if value:
            self.parents[key] = value
            if len(self.parents) > self.LIMIT:
                self.parents.popitem(last=False)
        return key

    async def inject(self, request: Any) -> None:
        if not self.enabled:
            return
        for name in ("traceparent", "tracestate", "baggage"):
            request.headers.pop(name, None)
        # The official transport serializes a bounded JSON request before HTTPX.
        # Never consume a streaming request body to inspect it.
        try:
            content = request.content
            if len(content) > 1024 * 1024:
                return
            obj = json.loads(content)
            key = self.key(obj.get("method"), obj.get("id")) if isinstance(obj, dict) else None
        except (ValueError, UnicodeError, RuntimeError):
            return
        value = self.parents.get(key) if key is not None else None
        if value:
            request.headers["traceparent"] = value
        # Keep the mapping until connection close so a same-origin 307 retry can
        # retain its original context. Missing entries do NOT reuse writer context.

    def wrap(self, stream: Any) -> Any:
        return _CapturedSendStream(stream, self) if self.enabled else stream

    def clear(self) -> None:
        self.parents.clear()


class _CapturedSendStream:
    """Delegate AnyIO send-stream lifecycle; capture in the caller, before queueing."""
    def __init__(self, stream: Any, bridge: MCPTraceBridge) -> None:
        self.stream, self.bridge = stream, bridge

    async def send(self, item: Any) -> None:
        key = self.bridge.capture(item)
        try:
            await self.stream.send(item)
        except BaseException:
            self.bridge.parents.pop(key, None)
            raise

    def send_nowait(self, item: Any) -> None:
        key = self.bridge.capture(item)
        try:
            self.stream.send_nowait(item)
        except BaseException:
            self.bridge.parents.pop(key, None)
            raise

    def clone(self):
        return _CapturedSendStream(self.stream.clone(), self.bridge)

    def close(self) -> None:
        self.stream.close()

    async def aclose(self) -> None:
        await self.stream.aclose()

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)
