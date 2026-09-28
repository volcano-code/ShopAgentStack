"""Optional real pinned FastMCP protocol test. Java HTTP is the ONLY fake boundary.

Skipped explicitly when MCP is unavailable; must run in the enabled target image.
"""
import os
import httpx
import pytest
if os.getenv('SHOP_AGENT_STACK_REQUIRE_REAL_MCP_TEST') == 'true':
    import mcp  # Missing pinned SDK is a CI failure, never a silent skip.
else:
    pytest.importorskip('mcp', reason='requires actual pinned MCP SDK; no protocol substitute allowed')
pytest.importorskip('opentelemetry.sdk', reason='optional tracing-enabled image test')
from starlette.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shop_agent_stack.mcp_server import app as original_app
from shop_agent_stack.observability import Runtime, _TOOL_LABELS
from shop_agent_stack.observability_core import Telemetry
from shop_agent_stack.observability_server import ObservedMCPApp
from shop_agent_stack import business


def test_real_mcp_json_rpc_lifespan_and_tool_context(monkeypatch):
    exporter=InMemorySpanExporter()
    p=TracerProvider(resource=Resource({'service.name':'shop-agent-stack-mcp'}),shutdown_on_exit=False)
    p.add_span_processor(SimpleSpanProcessor(exporter))
    runtime=Runtime(Telemetry(p.get_tracer('real-mcp-test'),tools=_TOOL_LABELS),p)
    OriginalClient=httpx.AsyncClient
    seen=[]
    def java(request):
        seen.append(request.headers)
        assert request.headers['X-ShopAgentStack-Execution']=='synthetic-grant'
        return httpx.Response(200,json={'code':200,'data':[]})
    monkeypatch.setattr(business.httpx,'AsyncClient',lambda **kw:OriginalClient(transport=httpx.MockTransport(java),**kw))
    wrapped=ObservedMCPApp(original_app,runtime_factory=lambda:runtime,trust_inbound=True)
    headers={'Accept':'application/json, text/event-stream','X-ShopAgentStack-Execution':'synthetic-grant',
             'traceparent':'00-'+'1'*32+'-'+'2'*16+'-01'}
    with TestClient(wrapped,base_url='http://127.0.0.1:8011') as client:
        response=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'trace-test','version':'1.0'}}})
        assert response.status_code==200 and 'result' in response.json()
        headers['MCP-Protocol-Version']=response.json()['result']['protocolVersion']
        response=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call',
            'params':{'name':'list_my_orders','arguments':{}}})
        assert response.status_code==200 and not response.json()['result'].get('isError')
    assert len(seen)==1 and seen[0]['traceparent'].split('-')[1]=='1'*32
    spans=exporter.get_finished_spans()
    assert any(s.name=='commerce.call' and f'{s.context.trace_id:032x}'=='1'*32 for s in spans)
