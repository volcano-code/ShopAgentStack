"""Opt-in Agent tracing. No business decisions, persisted raw traces, or global SDK.

The HTTP request activates a context-local Runtime. asyncio.create_task inherits it,
so the run remains observable after the POST response ends. Lifespan owns shutdown.
No OpenTelemetry import occurs while disabled. Public incoming context is untrusted.
"""
from __future__ import annotations

import logging
import math
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

_LOG = logging.getLogger(__name__)
_ACTIVE: ContextVar[Runtime | None] = ContextVar("shop_observability_runtime", default=None)
_PROVIDERS = frozenset({"deepseek", "openai", "kimi", "custom", "fixture"})
# Labels only: these NEVER grant tool permission. MODEL_TOOLS stays authoritative.
_TOOL_LABELS = frozenset({"list_my_orders", "get_my_order", "list_my_after_sales",
    "preview_after_sale", "get_operation_status", "search_policies", "search_products",
    "get_product", "submit_after_sale"})
_RUN_STATES = frozenset({"QUEUED", "RUNNING", "WAITING_CONFIRMATION", "CONFIRMING",
    "COMPLETED", "FAILED", "STOPPED", "STOPPING", "INTERRUPTED", "UNCERTAIN"})


@dataclass
class Runtime:
    """One application lifespan's SDK ownership; injectable exporter in tests."""
    telemetry: Any = None
    provider: Any = None
    _closed: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *,
                 service_name: str = "shop-agent-stack-agent") -> Runtime:
        env = os.environ if env is None else env
        if service_name not in {"shop-agent-stack-agent", "shop-agent-stack-mcp"}:
            raise ValueError("unregistered telemetry service name")
        enabled = env.get("SHOP_AGENT_STACK_OTEL_ENABLED", "false")
        if enabled == "false":
            return cls()
        if enabled != "true":
            raise ValueError("SHOP_AGENT_STACK_OTEL_ENABLED must be true or false")
        exporter_name = env.get("SHOP_AGENT_STACK_OTEL_EXPORTER", "console")
        if exporter_name not in {"console", "otlp"}:
            raise ValueError("SHOP_AGENT_STACK_OTEL_EXPORTER must be console or otlp")
        try:
            ratio = float(env.get("SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO", "0.1"))
        except (ValueError, TypeError):
            raise ValueError("invalid tracing sample ratio") from None
        if not math.isfinite(ratio) or not 0 <= ratio <= 1:
            raise ValueError("tracing sample ratio must be finite and within 0..1")
        endpoint = env.get("SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT", "")
        if exporter_name == "otlp":
            _validate_endpoint(endpoint)
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
            from .observability_core import Telemetry
        except ImportError:
            raise RuntimeError("Tracing was enabled but optional tracing dependencies are missing") from None
        if exporter_name == "console":
            exporter = ConsoleSpanExporter()
        else:
            try:
                import requests
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            except ImportError:
                raise RuntimeError("OTLP export dependencies are missing") from None
            session = requests.Session()
            session.trust_env = False
            exporter = OTLPSpanExporter(endpoint=endpoint, timeout=2, session=session)
        # Do not merge arbitrary OTEL_RESOURCE_ATTRIBUTES or process/host detectors.
        provider = TracerProvider(resource=Resource({"service.name": service_name}),
            sampler=ParentBased(TraceIdRatioBased(ratio)), shutdown_on_exit=False)
        try:
            provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=512,
                max_export_batch_size=64, schedule_delay_millis=1000))
            telemetry = Telemetry(provider.get_tracer("shop-agent-stack", "m1.2"),
                tools=_TOOL_LABELS, providers=_PROVIDERS)
            return cls(telemetry=telemetry, provider=provider)
        except BaseException:
            provider.shutdown()
            raise

    @contextmanager
    def activate(self) -> Iterator[None]:
        token = _ACTIVE.set(self if self.telemetry is not None and not self._closed else None)
        try:
            yield
        finally:
            _ACTIVE.reset(token)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.provider is not None:
            try:
                self.provider.shutdown()
            except Exception:
                # No exporter URL, raw error, customer data, or credentials in this log.
                _LOG.warning("shop_observability_shutdown_failed")


def _validate_endpoint(endpoint: str) -> None:
    # This is deployment configuration, NEVER a browser/model-supplied endpoint.
    try:
        parts = urlsplit(endpoint)
        valid = (parts.scheme in {"http", "https"} and bool(parts.hostname)
                 and not parts.username and not parts.password
                 and not parts.query and not parts.fragment
                 and parts.path == "/v1/traces" and "\\" not in endpoint
                 and not any(ord(c) < 33 for c in endpoint))
        port = parts.port
        valid = valid and (port is None or 1 <= port <= 65535)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("OTLP endpoint must be an operator-configured HTTP(S) /v1/traces URL without credentials or query")


class AgentTraceMiddleware:
    """Keeps the HTTP span alive through SSE; state is supplied by real app lifespan."""
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = getattr(scope.get("app"), "state", None)
        runtime = getattr(state, "shop_observability", None)
        if runtime is None or runtime.telemetry is None or runtime._closed:
            await self.app(scope, receive, send)
            return
        from .observability_core import TraceMiddleware
        with runtime.activate():
            await TraceMiddleware(self.app, runtime.telemetry, trust_inbound=False)(scope, receive, send)


def observe_provider(function: Callable) -> Callable:
    """Wrap the actual gateway, including SSE consumption. Preserve return and errors."""
    @wraps(function)
    async def observed(*args: Any, **kwargs: Any) -> Any:
        runtime = _ACTIVE.get()
        if runtime is None or runtime._closed:
            return await function(*args, **kwargs)
        name = kwargs.get("provider", args[0] if args else None)
        attributes = {"gen_ai.provider.name": name} if isinstance(name, str) and name in _PROVIDERS else {}
        with runtime.telemetry.operation("llm.call", attributes=attributes) as span:
            span.set_attribute("shop.provider.fixture", name == "fixture")
            result = await function(*args, **kwargs)
            # Usage absence is unknown, not zero. Never infer billing from fixture calls.
            usage = result[1] if isinstance(result, tuple) and len(result) == 2 else None
            if isinstance(usage, dict):
                for wire_key, attr in (("prompt_tokens", "gen_ai.usage.input_tokens"),
                                       ("completion_tokens", "gen_ai.usage.output_tokens")):
                    value = usage.get(wire_key)
                    if type(value) is int and 0 <= value <= 2**63 - 1:
                        span.set_attribute(attr, value)
            return result
    return observed


def observe_tool(function: Callable) -> Callable:
    """Record the tool name only; arguments, responses and execution grants stay out."""
    @wraps(function)
    async def observed(*args: Any, **kwargs: Any) -> Any:
        runtime = _ACTIVE.get()
        if runtime is None or runtime._closed:
            return await function(*args, **kwargs)
        name = kwargs.get("name", args[1] if len(args) > 1 else None)
        attributes = {"shop.tool.name": name} if isinstance(name, str) and name in _TOOL_LABELS else {}
        with runtime.telemetry.operation("tool.call", attributes=attributes):
            return await function(*args, **kwargs)
    return observed


async def inject_internal_trace(request: Any) -> None:
    """HTTPX request hook on the dedicated internal MCP client, not the LLM client.

    Per-request injection records the current tool span, not a stale connect-time span.
    Authorization stays unchanged; baggage and inherited raw tracestate are not sent.
    """
    runtime = _ACTIVE.get()
    if runtime is None or runtime._closed:
        return
    for key in ("traceparent", "tracestate", "baggage"):
        request.headers.pop(key, None)
    for key, value in runtime.telemetry.outbound_headers().items():
        if key == "traceparent":
            request.headers[key] = value


async def execute_run(function: Callable, store: Any, run: dict, member: Any,
                      execution: str, **kwargs: Any) -> Any:
    """Trace the background coroutine. Read final stored status without mutating it."""
    runtime = _ACTIVE.get()
    if runtime is None or runtime._closed:
        return await function(store, run, member, execution, **kwargs)
    with runtime.telemetry.operation("agent.run") as span:
        result = await function(store, run, member, execution, **kwargs)
        # runtime.execute catches some failures itself. A successful coroutine return
        # therefore must NOT be labelled a successful business/Agent outcome.
        try:
            status = store.run(run["id"], member)["status"]
            status = status if isinstance(status, str) and status in _RUN_STATES else "UNKNOWN"
        except Exception:
            status = "UNKNOWN"
        span.set_attribute("shop.run.status", status)
        if status in {"FAILED", "INTERRUPTED", "UNCERTAIN", "UNKNOWN"}:
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.ERROR))
        return result


def observe_commerce(function: Callable) -> Callable:
    """Trace the real Python -> Java adapter, without URL, arguments or business data.

    This is a client span, NOT proof of instrumentation inside Java. A downstream
    Java server must independently validate business authorization and extract context.
    """
    @wraps(function)
    async def observed(*args: Any, **kwargs: Any) -> Any:
        runtime = _ACTIVE.get()
        if runtime is None or runtime._closed:
            return await function(*args, **kwargs)
        from opentelemetry.trace import SpanKind
        with runtime.telemetry.operation("commerce.call", kind=SpanKind.CLIENT):
            return await function(*args, **kwargs)
    return observed
