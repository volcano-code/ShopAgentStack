"""Real pinned MCP ClientSession + Streamable HTTP writer + server; only Java is doubled.

The old direct HTTPX-hook test misses the SDK's background queue. This test uses
that queue and asserts exact parent span IDs, not merely a shared trace ID.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import sys
import os
import httpx
import pytest
if os.getenv('SHOP_AGENT_STACK_REQUIRE_REAL_MCP_TEST') == 'true':
    import mcp
else:
    pytest.importorskip('mcp',reason='requires actual pinned MCP SDK')
pytest.importorskip('opentelemetry.sdk')
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shop_agent_stack import tools
from shop_agent_stack.observability import Runtime, _TOOL_LABELS
from shop_agent_stack.observability_core import Telemetry
from shop_agent_stack.observability_server import ObservedMCPApp


@asynccontextmanager
async def started(app):
    # A real Starlette lifespan needs ASGI state; startup failures must not become
    # a silent wait on an Event. No protocol or application logic is replaced.
    requests, replies = asyncio.Queue(), asyncio.Queue()
    task = asyncio.create_task(app({"type":"lifespan", "asgi":{"version":"3.0"}, "state":{}}, requests.get, replies.put))
    ready = False
    try:
        await requests.put({"type":"lifespan.startup"})
        reply = await asyncio.wait_for(replies.get(), 3)
        assert reply["type"] == "lifespan.startup.complete", reply
        ready = True
        yield
    finally:
        if ready and not task.done():
            await requests.put({"type":"lifespan.shutdown"})
            reply = await asyncio.wait_for(replies.get(), 3)
            assert reply["type"] == "lifespan.shutdown.complete", reply
        if not ready and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            await asyncio.wait_for(task, 3)


def fresh_server(monkeypatch):
    # The pinned SDK session manager is single-lifespan. Execute the exact
    # production module in a private namespace instead of restarting the instance
    # already exercised by the preceding protocol test in the same pytest process.
    name = "shop_agent_stack._m13_transport_test_server"
    path = Path(__file__).resolve().parents[1]/"shop_agent_stack/mcp_server.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module.app


@pytest.mark.asyncio
async def test_real_client_background_writer_preserves_each_tool_parent(monkeypatch):
    exporter=InMemorySpanExporter();runtimes=[]
    for name in ('shop-agent-stack-agent','shop-agent-stack-mcp'):
        p=TracerProvider(resource=Resource({'service.name':name}),shutdown_on_exit=False)
        p.add_span_processor(SimpleSpanProcessor(exporter))
        runtimes.append(Runtime(Telemetry(p.get_tracer('real-queue-test'),tools=_TOOL_LABELS),p))
    caller,server=runtimes
    wrapped=ObservedMCPApp(fresh_server(monkeypatch),runtime_factory=lambda:server,trust_inbound=True)
    asgi=httpx.ASGITransport(wrapped);seen=[]
    class Routing(httpx.AsyncBaseTransport):
        async def handle_async_request(self,req):
            assert req.headers['X-ShopAgentStack-Execution']=='synthetic-grant'
            assert 'baggage' not in req.headers and 'tracestate' not in req.headers
            if req.url.host=='commerce-mcp':return await asgi.handle_async_request(req)
            assert req.url.host=='portal'
            seen.append(req.headers['traceparent'])
            return httpx.Response(200,json={'code':200,'data':[]})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=Routing(),**kw))
    monkeypatch.setenv('SHOP_AGENT_STACK_MCP_URL','http://commerce-mcp:8011/mcp')
    try:
        async with started(wrapped):
            with caller.activate(),caller.telemetry.operation('agent.run'):
                async with tools.connect('synthetic-grant') as session:
                    for _ in range(2):assert await tools.call(session,'list_my_orders',{})=={'orders':[]}
        spans=exporter.get_finished_spans();byid={s.context.span_id:s for s in spans}
        tool_spans=[s for s in spans if s.name=='tool.call']
        assert len(tool_spans)==2 and len(seen)==2
        parents=[]
        for out in (s for s in spans if s.name=='commerce.call'):
            assert out.parent is not None
            receive=byid[out.parent.span_id];assert receive.name=='mcp.request'
            assert receive.parent is not None
            tool=byid[receive.parent.span_id];assert tool.name=='tool.call'
            parents.append(tool.context.span_id)
        assert set(parents)=={s.context.span_id for s in tool_spans}
        assert len(parents)==2
    finally:
        for runtime in runtimes:runtime.shutdown()
