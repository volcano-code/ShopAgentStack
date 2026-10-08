"""Default-off business adapter and MCP wrapper need no optional tracing packages."""
import os
from pathlib import Path
import subprocess
import sys


def test_disabled_commerce_and_server_import_without_sdk():
    root=Path(__file__).resolve().parents[1]
    program=r'''
import asyncio, importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name.startswith('opentelemetry'):
            raise ImportError('intentionally unavailable')
sys.meta_path.insert(0,Deny())
import httpx
from shop_agent_stack import business
from shop_agent_stack.observability_server import ObservedMCPApp
Original=httpx.AsyncClient
seen=[]
def receive(request):
    seen.append(request)
    assert 'traceparent' not in request.headers
    assert request.headers['X-ShopAgentStack-Execution']=='synthetic-grant'
    return httpx.Response(200,json={'code':200,'data':{'unchanged':True}})
business.httpx.AsyncClient=lambda **kw: Original(transport=httpx.MockTransport(receive),**kw)
assert asyncio.run(business.java('/synthetic',execution='synthetic-grant'))=={'unchanged':True}
assert len(seen)==1
assert ObservedMCPApp(None).runtime is None
assert not any(x.startswith('opentelemetry') for x in sys.modules)
'''
    env={k:v for k,v in os.environ.items() if not k.startswith(('SHOP_AGENT_STACK_','OTEL_'))}
    env['PYTHONPATH']=str(root)
    result=subprocess.run([sys.executable,'-c',program],cwd=root,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
