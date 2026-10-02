"""Real SDK + real business adapter + ASGI boundary doubles. NOT a commerce E2E."""
import asyncio
from contextlib import asynccontextmanager
import json
import httpx
import pytest

pytest.importorskip('opentelemetry.sdk')
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from shop_agent_stack import observability as obs, business
from shop_agent_stack.observability_core import Telemetry, TraceMiddleware
from shop_agent_stack.observability_server import ObservedMCPApp


@pytest.fixture
def chain():
    exporter = InMemorySpanExporter()
    runtimes = []
    for service in ('shop-agent-stack-agent', 'shop-agent-stack-mcp'):
        p = TracerProvider(resource=Resource({'service.name': service}), shutdown_on_exit=False)
        p.add_span_processor(SimpleSpanProcessor(exporter))
        runtimes.append(obs.Runtime(Telemetry(p.get_tracer('m12-test'), tools=obs._TOOL_LABELS), p))
    yield *runtimes, exporter
    for r in runtimes:
        r.shutdown()


@asynccontextmanager
async def started(wrapper):
    queue = asyncio.Queue()
    ready, done = asyncio.Event(), asyncio.Event()
    messages = []
    async def send(msg):
        messages.append(msg['type'])
        if msg['type'] == 'lifespan.startup.complete': ready.set()
        if msg['type'] == 'lifespan.shutdown.complete': done.set()
    task = asyncio.create_task(wrapper({'type': 'lifespan'}, queue.get, send))
    await queue.put({'type': 'lifespan.startup'})
    await asyncio.wait_for(ready.wait(), 2)
    try:
        yield messages
    finally:
        await queue.put({'type': 'lifespan.shutdown'})
        await asyncio.wait_for(task, 2)
        assert done.is_set()


async def lifespan(scope, receive, send):
    if scope['type'] == 'lifespan':
        assert (await receive())['type'] == 'lifespan.startup'
        await send({'type': 'lifespan.startup.complete'})
        assert (await receive())['type'] == 'lifespan.shutdown'
        await send({'type': 'lifespan.shutdown.complete'})
        return True
    return False


@pytest.mark.asyncio
async def test_parallel_agent_tool_mcp_commerce_context_and_privacy(chain, monkeypatch):
    agent, mcp, exporter = chain
    original = httpx.AsyncClient
    outgoing = []
    def java_response(request):
        assert request.headers['X-ShopAgentStack-Execution'] == 'synthetic-secret-grant'
        assert 'baggage' not in request.headers and 'tracestate' not in request.headers
        outgoing.append(request.headers['traceparent'])
        return httpx.Response(200, json={'code': 200, 'data': {'orders': []}})
    monkeypatch.setattr(business.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(java_response), **kw))
    async def downstream(scope, receive, send):
        if await lifespan(scope, receive, send): return
        await asyncio.sleep(0)
        result = await business.java('/private-path?secret=synthetic', execution='synthetic-secret-grant')
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': json.dumps(result).encode()})
    wrapper = ObservedMCPApp(downstream, runtime_factory=lambda: mcp, trust_inbound=True)
    async with started(wrapper):
        async with original(transport=httpx.ASGITransport(wrapper), base_url='http://localhost:8011', trust_env=False) as client:
            @obs.observe_tool
            async def call(session, name, arguments):
                return await client.post('/mcp', headers={**agent.telemetry.outbound_headers(), 'baggage': 'secret=hidden'}, json=arguments)
            async def one():
                with agent.activate(), agent.telemetry.operation('agent.run'):
                    tid = agent.telemetry.trace_id()
                    response = await call(None, 'search_products', {'private': 'synthetic-secret'})
                    assert response.headers['x-trace-id'] == tid
                    return tid
            tids = await asyncio.gather(*(one() for _ in range(12)))
    assert len(set(tids)) == 12 and len(outgoing) == 12
    assert obs._ACTIVE.get() is None
    spans = exporter.get_finished_spans()
    assert len(spans) == 48
    for tid in tids:
        group = {s.name: s for s in spans if f'{s.context.trace_id:032x}' == tid}
        assert set(group) == {'agent.run', 'tool.call', 'mcp.request', 'commerce.call'}
        for child, parent in [('tool.call','agent.run'), ('mcp.request','tool.call'), ('commerce.call','mcp.request')]:
            assert group[child].parent.span_id == group[parent].context.span_id
        assert group['mcp.request'].resource.attributes['service.name'] == 'shop-agent-stack-mcp'
        assert group['commerce.call'].kind.name == 'CLIENT'
        assert any(f'{group["commerce.call"].context.span_id:016x}' in h for h in outgoing)
    text = '\n'.join(s.to_json() for s in spans)
    for forbidden in ('synthetic-secret', 'private-path', 'secret=hidden'):
        assert forbidden not in text


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,accepted', [
    ([(b'traceparent', b'00-' + b'1'*32 + b'-' + b'2'*16 + b'-01')], True),
    ([(b'traceparent', b'00-' + b'1'*32 + b'-' + b'2'*16 + b'-00')], True),
    ([(b'traceparent', b'00-' + b'1'*32 + b'-' + b'2'*16 + b'-03')], True),
    ([(b'traceparent', b'bad')], False),
    ([(b'traceparent', b'x'*10000)], False),
    ([(b'traceparent', b'00-' + b'0'*32 + b'-' + b'2'*16 + b'-01')], False),
    ([(b'traceparent', b'00-' + b'1'*32 + b'-' + b'0'*16 + b'-01')], False),
    ([(b'traceparent', b'00-' + b'1'*32 + b'-' + b'2'*16 + b'-01')]*2, False),
    ([(b'traceparent', b'01-' + b'1'*32 + b'-' + b'2'*16 + b'-01')], False),
])
async def test_strict_inbound_context(chain, headers, accepted):
    agent, _, exporter = chain
    observed = []
    async def target(scope, receive, send):
        observed.append((agent.telemetry.trace_id(), agent.telemetry.outbound_headers()))
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b''})
    async def receive(): return {'type':'http.request','body':b''}
    async def send(message): pass
    app = TraceMiddleware(target, agent.telemetry, trust_inbound=True)
    await app({'type':'http','method':'POST','headers':headers+[(b'tracestate',b'secret=hidden'),(b'baggage',b'secret=hidden')]}, receive, send)
    assert (observed[0][0] == '1'*32) is accepted
    assert set(observed[0][1]) == {'traceparent'}


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['cancel', 'business', 'http'])
async def test_commerce_errors_and_cancellation_propagate(chain, monkeypatch, failure):
    agent, _, exporter = chain
    original = httpx.AsyncClient
    def respond(request):
        if failure == 'cancel': raise asyncio.CancelledError('synthetic-secret')
        if failure == 'http': raise httpx.ConnectError('synthetic-secret')
        return httpx.Response(200, json={'code':403,'message':'synthetic-secret'})
    monkeypatch.setattr(business.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with agent.activate(), pytest.raises(asyncio.CancelledError if failure=='cancel' else business.BusinessError):
        await business.java('/internal',execution='synthetic-secret')
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes['shop.failure.category'] == ('cancelled' if failure=='cancel' else 'error')
    assert 'synthetic-secret' not in span.to_json()


@pytest.mark.asyncio
async def test_mcp_disabled_passthrough_and_lifespan():
    runtime = obs.Runtime()
    calls = []
    async def inner(scope, receive, send):
        calls.append(scope['type'])
        if await lifespan(scope,receive,send): return
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'unchanged'})
    app = ObservedMCPApp(inner, runtime_factory=lambda: runtime, trust_inbound=False)
    async with started(app) as messages:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://localhost') as client:
            response = await client.get('/')
            assert response.content == b'unchanged' and 'x-trace-id' not in response.headers
    assert messages == ['lifespan.startup.complete','lifespan.shutdown.complete']
    assert calls == ['lifespan','http'] and runtime._closed and app.runtime is None


@pytest.mark.asyncio
async def test_failed_lifespan_closes_owned_sdk():
    r = obs.Runtime()
    async def broken(scope, receive, send): raise RuntimeError('underlying start failure')
    app = ObservedMCPApp(broken,runtime_factory=lambda:r)
    with pytest.raises(RuntimeError): await app({'type':'lifespan'},None,None)
    assert r._closed and app.runtime is None


def test_service_names_and_internal_trust_are_operator_controlled(monkeypatch):
    with pytest.raises(ValueError): obs.Runtime.from_env({}, service_name='untrusted-user-text')
    monkeypatch.setenv('SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL','maybe')
    with pytest.raises(ValueError): ObservedMCPApp(None)
    with pytest.raises(ValueError): TraceMiddleware(None,None,operation_name='secret')
