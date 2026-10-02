"""Queue-boundary tests with the real OTel SDK. No MCP or network in this unit suite."""
import asyncio
from types import SimpleNamespace as NS
import json
import httpx
import pytest

pytest.importorskip("opentelemetry.sdk")
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shop_agent_stack.observability import Runtime, inject_internal_trace
from shop_agent_stack.observability_core import Telemetry
from shop_agent_stack.observability_transport import MCPTraceBridge


@pytest.fixture
def runtime():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = Runtime(Telemetry(provider.get_tracer("queue-boundary-test")), provider)
    yield runtime, exporter
    runtime.shutdown()


def message(i=1, method="tools/call"):
    return NS(message=NS(root=NS(id=i, method=method)))


def request(i=1, method="tools/call"):
    return httpx.Request("POST", "http://commerce-mcp:8011/mcp", json={
        "jsonrpc":"2.0", "id":i, "method":method,
        "params":{"arguments":{"traceparent":"synthetic-untrusted-argument"}}},
        headers={"X-ShopAgentStack-Execution":"synthetic-grant", "baggage":"private", "tracestate":"private"})


@pytest.mark.asyncio
async def test_background_writer_uses_caller_context_not_connect_time(runtime):
    runtime, _ = runtime
    queue = asyncio.Queue()
    class Stream:
        async def send(self, item): await queue.put(item)
    with runtime.activate(), runtime.telemetry.operation("agent.run") as root:
        bridge = MCPTraceBridge(); stream = bridge.wrap(Stream())
        # Task context is captured here, BEFORE the tool spans are created.
        async def writer():
            received = []
            for _ in range(2):
                item = await queue.get()
                req = request(item.message.root.id)
                await inject_internal_trace(req)
                assert req.headers["traceparent"].split("-")[2] == f"{root.get_span_context().span_id:016x}"
                await bridge.inject(req)
                received.append(req.headers["traceparent"].split("-")[2])
                assert req.headers["X-ShopAgentStack-Execution"] == "synthetic-grant"
                assert "baggage" not in req.headers and "tracestate" not in req.headers
            return received
        writer_task = asyncio.create_task(writer())
        expected = []
        for i in (1,2):
            with runtime.telemetry.operation("tool.call") as span:
                expected.append(f"{span.get_span_context().span_id:016x}")
                await stream.send(message(i))
        assert await writer_task == expected
        bridge.clear(); assert not bridge.parents


@pytest.mark.asyncio
async def test_same_request_id_in_two_connections_does_not_mix(runtime):
    runtime, _ = runtime
    with runtime.activate():
        a, b = MCPTraceBridge(), MCPTraceBridge()
        with runtime.telemetry.operation("tool.call") as one: a.capture(message())
        with runtime.telemetry.operation("tool.call") as two: b.capture(message())
        x, y = request(), request()
        await asyncio.gather(a.inject(x), b.inject(y))
        assert x.headers["traceparent"].split("-")[2] == f"{one.get_span_context().span_id:016x}"
        assert y.headers["traceparent"].split("-")[2] == f"{two.get_span_context().span_id:016x}"
        assert x.headers["traceparent"] != y.headers["traceparent"]


@pytest.mark.asyncio
async def test_missing_mapping_never_uses_argument_or_writer_parent(runtime):
    runtime, _ = runtime
    with runtime.activate(), runtime.telemetry.operation("agent.run"):
        bridge=MCPTraceBridge();req=request()
        req.headers['traceparent']='00-'+'1'*32+'-'+'2'*16+'-01'
        await bridge.inject(req)
        assert 'traceparent' not in req.headers
        assert req.headers['X-ShopAgentStack-Execution']=='synthetic-grant'


@pytest.mark.asyncio
async def test_same_request_retry_keeps_capture(runtime):
    runtime, _ = runtime
    with runtime.activate(), runtime.telemetry.operation("tool.call"):
        bridge=MCPTraceBridge();bridge.capture(message());a,b=request(),request()
        await bridge.inject(a);await bridge.inject(b)
        assert a.headers['traceparent']==b.headers['traceparent']


@pytest.mark.asyncio
async def test_cancelled_enqueue_cleans_mapping_and_propagates(runtime):
    runtime,_=runtime
    class Stream:
        async def send(self, item): raise asyncio.CancelledError()
    with runtime.activate(),runtime.telemetry.operation("tool.call"):
        bridge=MCPTraceBridge()
        with pytest.raises(asyncio.CancelledError):await bridge.wrap(Stream()).send(message())
        assert not bridge.parents


def test_disabled_wrap_preserves_original_stream_and_message():
    bridge=MCPTraceBridge();stream=object()
    assert bridge.wrap(stream) is stream
    assert bridge.capture(None) is None
    assert not bridge.parents


def test_bounded_connection_mapping(runtime):
    runtime,_=runtime
    with runtime.activate(),runtime.telemetry.operation("tool.call"):
        bridge=MCPTraceBridge()
        for i in range(bridge.LIMIT+5):bridge.capture(message(i))
        assert len(bridge.parents)==bridge.LIMIT and ('tools/call',0) not in bridge.parents
        assert 'synthetic-untrusted-argument' not in str(bridge.parents)


@pytest.mark.parametrize('request_id',[None,True,-1,2**63,'','x'*65,[],{}])
def test_invalid_request_ids_are_not_captured(request_id):
    assert MCPTraceBridge.key('tools/call',request_id) is None


@pytest.mark.asyncio
async def test_foreign_runtime_cannot_use_connection(runtime):
    runtime,_=runtime
    with runtime.activate():bridge=MCPTraceBridge()
    bridge.capture(message())
    await bridge.inject(request())
    assert not bridge.parents
