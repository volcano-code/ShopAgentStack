"""Lifecycle-preserving ASGI tracing wrapper for the *internal* MCP service.

No MCP protocol implementation is duplicated. The original app owns its lifespan,
FastMCP session manager and all authorization. Trace context never grants access.
"""
from __future__ import annotations

import os
from typing import Any, Callable
from .observability import Runtime


class ObservedMCPApp:
    def __init__(self, app: Any, *, runtime_factory: Callable[[], Runtime] | None = None,
                 trust_inbound: bool | None = None) -> None:
        self.app = app
        self._runtime_factory = runtime_factory or (
            lambda: Runtime.from_env(service_name="shop-agent-stack-mcp"))
        if trust_inbound is None:
            value = os.getenv("SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL", "false")
            if value not in {"true", "false"}:
                raise ValueError("SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL must be true or false")
            trust_inbound = value == "true"
        self.trust_inbound = trust_inbound
        self.runtime: Runtime | None = None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            if self.runtime is not None:
                raise RuntimeError("MCP tracing lifespan is already active")
            # Initialize only after the ASGI server starts the lifespan. Never on import.
            runtime = self._runtime_factory()
            self.runtime = runtime
            try:
                # Forward the SAME lifespan messages, exactly once. This is important:
                # mounting an MCP app and dropping its lifespan loses the session manager.
                await self.app(scope, receive, send)
            finally:
                runtime.shutdown()
                self.runtime = None
            return
        runtime = self.runtime
        if (scope["type"] != "http" or runtime is None or runtime.telemetry is None
                or runtime._closed):
            await self.app(scope, receive, send)
            return
        from .observability_core import TraceMiddleware
        with runtime.activate():
            await TraceMiddleware(self.app, runtime.telemetry,
                                  trust_inbound=self.trust_inbound,
                                  operation_name="mcp.request")(scope, receive, send)
