"""Privacy-first, context-local tracing using the actual OpenTelemetry SDK.

A host supplies its tracer provider and explicitly trusted tool/model names. No global
provider replacement, prompt capture, exception message capture, or baggage propagation.
"""
from __future__ import annotations
import asyncio
import re
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

OPERATIONS = frozenset({"agent.run", "llm.call", "tool.call", "retrieval.search", "source.validate", "http.request", "mcp.request", "commerce.call"})
NUMERIC = frozenset({"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "shop.retrieval.candidates", "shop.retrieval.returned"})
BOOLEANS = frozenset({"shop.source.valid", "shop.confirmation.required", "shop.confirmation.validated"})


class Telemetry:
    def __init__(self, tracer: trace.Tracer, *, tools: frozenset[str] = frozenset(),
                 models: frozenset[str] = frozenset(), providers: frozenset[str] = frozenset()) -> None:
        self.tracer = tracer
        # These lists must be supplied by trusted configuration, not LLM output.
        self.enums = {"shop.tool.name": tools, "gen_ai.request.model": models, "gen_ai.provider.name": providers}

    def safe_attributes(self, attributes: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in attributes.items():
            if key in NUMERIC:
                if type(value) is not int or value < 0:
                    raise ValueError(f"{key} requires a non-negative integer")
                result[key] = value
            elif key in BOOLEANS:
                if type(value) is not bool:
                    raise ValueError(f"{key} requires a boolean")
                result[key] = value
            elif key in self.enums:
                if not isinstance(value, str) or value not in self.enums[key]:
                    raise ValueError(f"{key} is not in trusted configuration")
                result[key] = value
            else:
                raise ValueError("attribute is not in the telemetry allowlist")
        return result

    @contextmanager
    def operation(self, name: str, *, attributes: Mapping[str, Any] | None = None,
                  context: Any = None, kind: SpanKind = SpanKind.INTERNAL) -> Iterator[trace.Span]:
        if name not in OPERATIONS:
            raise ValueError("unknown operation; do not use user input as a span name")
        safe = self.safe_attributes(attributes or {})
        # SDK default exception recording can leak prompts, keys and tool responses.
        with self.tracer.start_as_current_span(name, context=context, kind=kind, attributes=safe,
                                              record_exception=False, set_status_on_exception=False) as span:
            try:
                yield span
            except BaseException as exc:
                category = "cancelled" if isinstance(exc, asyncio.CancelledError) else ("timeout" if isinstance(exc, TimeoutError) else "error")
                span.set_attribute("shop.failure.category", category)
                span.set_status(Status(StatusCode.ERROR))
                span.add_event("operation.failed", {"shop.failure.category": category})
                raise

    def record_usage(self, *, input_tokens: int, output_tokens: int) -> None:
        attrs = self.safe_attributes({"gen_ai.usage.input_tokens": input_tokens,
                                      "gen_ai.usage.output_tokens": output_tokens})
        trace.get_current_span().set_attributes(attrs)

    @staticmethod
    def trace_id() -> str | None:
        context = trace.get_current_span().get_span_context()
        return f"{context.trace_id:032x}" if context.is_valid else None

    @staticmethod
    def outbound_headers() -> dict[str, str]:
        """Propagate W3C trace context only. Never attach credentials or baggage."""
        headers: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(headers)
        return headers


class TraceMiddleware:
    """Pure ASGI middleware: keeps the span open for the complete SSE response.

    trust_inbound=False for a public entrypoint; True only behind a trusted boundary.
    No request path, query, headers, body, or raw errors are recorded. Trace IDs are
    diagnostic correlation identifiers, not an authorization mechanism.
    """
    def __init__(self, app: Any, telemetry: Telemetry, *, trust_inbound: bool = False, operation_name: str = "http.request") -> None:
        self.app = app
        self.telemetry = telemetry
        self.trust_inbound = trust_inbound
        if operation_name not in {"http.request", "mcp.request"}:
            raise ValueError("invalid server span name")
        self.operation_name = operation_name

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from opentelemetry.context import Context
        context = Context()  # Ignore untrusted incoming traceparent at a public boundary.
        if self.trust_inbound:
            # One bounded W3C v00 traceparent only. Duplicates, future versions and
            # malformed values start a new trace. Never propagate tracestate/baggage.
            # Flags are a full byte; the SDK may set the W3C random-ID bit (03),
            # not only the sampled bit (01). Restricting to 00/01 breaks continuity.
            values = [value for key, value in scope.get("headers", [])
                      if key.lower() == b"traceparent"]
            if len(values) == 1 and re.fullmatch(
                    rb"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", values[0]):
                context = TraceContextTextMapPropagator().extract(
                    {"traceparent": values[0].decode("ascii")}, context=Context())
        with self.telemetry.operation(self.operation_name, context=context, kind=SpanKind.SERVER) as span:
            method = scope.get("method", "")
            span.set_attribute("http.request.method", method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "_OTHER")
            async def traced_send(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    code = int(message["status"])
                    span.set_attribute("http.response.status_code", code)
                    if code >= 500:
                        span.set_status(Status(StatusCode.ERROR))
                    headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"x-trace-id"]
                    trace_id = self.telemetry.trace_id()
                    if trace_id:
                        headers.append((b"x-trace-id", trace_id.encode("ascii")))
                    message = {**message, "headers": headers}
                await send(message)
            await self.app(scope, receive, traced_send)
