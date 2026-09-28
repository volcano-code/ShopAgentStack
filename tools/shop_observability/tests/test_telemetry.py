from __future__ import annotations
import asyncio
import json
import pytest
from opentelemetry import baggage, context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from tools.shop_observability.telemetry import Telemetry, TraceMiddleware

@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name":"test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observer = Telemetry(provider.get_tracer("test"), tools=frozenset({"search_policy"}),
                         models=frozenset({"test-model"}), providers=frozenset({"test-provider"}))
    yield observer,exporter
    provider.shutdown()


def test_real_sdk_parent_child(telemetry):
    observer, exporter = telemetry
    with observer.operation("agent.run"):
        parent_id = observer.trace_id()
        with observer.operation("llm.call"):
            observer.record_usage(input_tokens=10,output_tokens=20)
            assert observer.trace_id() == parent_id
    child,parent=exporter.get_finished_spans()
    assert child.parent.span_id == parent.context.span_id
    assert child.context.trace_id == parent.context.trace_id
    assert child.attributes["gen_ai.usage.output_tokens"] == 20
    assert observer.trace_id() is None


@pytest.mark.parametrize("key", ["prompt", "api_key", "Authorization", "user.id", "http.url", "exception.message"])
def test_reject_sensitive_attributes(telemetry,key):
    observer,exporter=telemetry
    with pytest.raises(ValueError):
        with observer.operation("llm.call",attributes={key:"SECRET"}): pass
    assert not exporter.get_finished_spans()


def test_unknown_tool_or_model_not_logged(telemetry):
    observer,exporter=telemetry
    with pytest.raises(ValueError):
        with observer.operation("tool.call",attributes={"shop.tool.name":"arbitrary-user-input"}): pass
    with pytest.raises(ValueError):
        with observer.operation("llm.call",attributes={"gen_ai.request.model":"API_KEY"}): pass
    assert not exporter.get_finished_spans()


def test_allowlisted_enum(telemetry):
    observer,exporter=telemetry
    with observer.operation("tool.call",attributes={"shop.tool.name":"search_policy"}): pass
    assert exporter.get_finished_spans()[0].attributes["shop.tool.name"] == "search_policy"


@pytest.mark.parametrize("value", [-1,True,float("nan"),1.5,"2"])
def test_invalid_usage(telemetry,value):
    observer,_=telemetry
    with pytest.raises(ValueError): observer.record_usage(input_tokens=value,output_tokens=1)


def test_boolean_attribute_strict(telemetry):
    observer,_=telemetry
    with pytest.raises(ValueError): observer.safe_attributes({"shop.source.valid":1})


def test_unknown_span_name(telemetry):
    observer,_=telemetry
    with pytest.raises(ValueError):
        with observer.operation("customer entered secret here"): pass


def test_exception_messages_and_tracebacks_never_recorded(telemetry):
    observer,exporter=telemetry
    with pytest.raises(RuntimeError):
        with observer.operation("llm.call"): raise RuntimeError("SECRET_API_KEY sk-very-private")
    span=exporter.get_finished_spans()[0]
    dumped=span.to_json()
    assert "SECRET_API_KEY" not in dumped and "sk-very-private" not in dumped
    assert "exception.stacktrace" not in dumped
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["shop.failure.category"] == "error"


@pytest.mark.parametrize("exception,category", [(TimeoutError("private"),"timeout"),(asyncio.CancelledError("private"),"cancelled")])
def test_cancellation_timeout_propagate(telemetry,exception,category):
    observer,exporter=telemetry
    with pytest.raises(type(exception)):
        with observer.operation("agent.run"): raise exception
    assert exporter.get_finished_spans()[0].attributes["shop.failure.category"] == category


def test_no_baggage_in_outbound_headers(telemetry):
    observer,_=telemetry
    token=context.attach(baggage.set_baggage("secret","PRIVATE"))
    try:
        with observer.operation("tool.call"):
            headers=observer.outbound_headers()
            assert "traceparent" in headers
            assert "baggage" not in headers
    finally: context.detach(token)


def test_concurrent_context_isolation(telemetry):
    observer,exporter=telemetry
    async def one():
        with observer.operation("agent.run"):
            own=observer.trace_id()
            await asyncio.sleep(0)
            with observer.operation("llm.call"):
                assert own == observer.trace_id()
            return own
    async def run(): return await asyncio.gather(one(),one(),one())
    trace_ids=asyncio.run(run())
    assert len(set(trace_ids)) == 3
    assert len(exporter.get_finished_spans()) == 6


def test_asgi_stream_span_lives_through_final_body(telemetry):
    observer,exporter=telemetry
    sent=[]
    async def app(scope,receive,send):
        await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"text/event-stream"),(b"X-Trace-ID",b"untrusted")]})
        await send({"type":"http.response.body","body":b"data: first\n\n","more_body":True})
        assert not exporter.get_finished_spans()
        await asyncio.sleep(0)
        await send({"type":"http.response.body","body":b"data: final\n\n","more_body":False})
    async def send(message):
        assert not exporter.get_finished_spans()
        sent.append(message)
    async def receive(): return {"type":"http.request","body":b""}
    scope={"type":"http","method":"GET","path":"/private-account","query_string":b"key=SECRET","headers":[(b"authorization",b"Bearer SECRET")]}
    asyncio.run(TraceMiddleware(app,observer)(scope,receive,send))
    spans=exporter.get_finished_spans()
    assert len(spans) == 1
    headers=dict(sent[0]["headers"])
    assert headers[b"x-trace-id"].decode() == f"{spans[0].context.trace_id:032x}"
    assert len([k for k,v in sent[0]["headers"] if k.lower()==b"x-trace-id"]) == 1
    assert "SECRET" not in spans[0].to_json() and "private-account" not in spans[0].to_json()


@pytest.mark.parametrize("trusted",[True,False])
def test_asgi_boundary_traceparent(telemetry,trusted):
    observer,exporter=telemetry
    incoming_trace="1"*32
    async def app(scope,receive,send): await send({"type":"http.response.start","status":503,"headers":[]})
    async def send(message): pass
    async def receive(): return {"type":"http.request"}
    scope={"type":"http","method":"POST","headers":[(b"traceparent",f"00-{incoming_trace}-{'2'*16}-01".encode()),(b"baggage",b"PRIVATE=SECRET")]}
    asyncio.run(TraceMiddleware(app,observer,trust_inbound=trusted)(scope,receive,send))
    span=exporter.get_finished_spans()[0]
    assert (f"{span.context.trace_id:032x}" == incoming_trace) is trusted
    assert span.status.status_code is StatusCode.ERROR
    assert "PRIVATE" not in span.to_json()


def test_non_http_passes_without_span(telemetry):
    observer,exporter=telemetry
    seen=[]
    async def app(scope,receive,send): seen.append(scope["type"])
    asyncio.run(TraceMiddleware(app,observer)({"type":"lifespan"},None,None))
    assert seen == ["lifespan"] and not exporter.get_finished_spans()
