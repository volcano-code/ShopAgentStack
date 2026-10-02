"""Actual provider/app/Store/adapter source + real SDK, with explicit boundary doubles.

No live model, Java service, LangGraph execution, real MCP session, or collector is
used here. Tests must never be represented as an end-to-end commerce/Agent benchmark.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import sys
import types
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("opentelemetry.sdk", reason="optional Agent tracing image only")
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from shop_agent_stack import observability as obs, providers, store as real_store
from shop_agent_stack.observability_core import Telemetry


@pytest.fixture
def observer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"service.name":"synthetic-contract"}), shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = obs.Runtime(Telemetry(provider.get_tracer("contract"), providers=obs._PROVIDERS,
                                   tools=obs._TOOL_LABELS), provider)
    yield runtime, exporter
    runtime.shutdown()


def spans_text(exporter):
    return "\n".join(s.to_json() for s in exporter.get_finished_spans())


@pytest.mark.parametrize("config", [
    {"SHOP_AGENT_STACK_OTEL_ENABLED":"yes"},
    {"SHOP_AGENT_STACK_OTEL_EXPORTER":"unknown"},
    *[{"SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO":n} for n in ("NaN", "inf", "-1", "1.01", "secret")],
    {"SHOP_AGENT_STACK_OTEL_EXPORTER":"otlp", "SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT":"https://key:secret@host/v1/traces"},
    {"SHOP_AGENT_STACK_OTEL_EXPORTER":"otlp", "SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT":"https://host/v1/traces?token=secret"},
    {"SHOP_AGENT_STACK_OTEL_EXPORTER":"otlp", "SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT":"https://host:bad/v1/traces"},
    {"SHOP_AGENT_STACK_OTEL_EXPORTER":"otlp", "SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT":"https://host/v1/traces\n"},
])
def test_invalid_deployment_config_is_redacted(config):
    with pytest.raises(ValueError) as exc:
        obs.Runtime.from_env({"SHOP_AGENT_STACK_OTEL_ENABLED":"true", **config})
    assert "secret" not in str(exc.value)


def test_console_provider_ignores_resource_environment(monkeypatch, capsys):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "secret=synthetic-api-key")
    runtime = obs.Runtime.from_env({"SHOP_AGENT_STACK_OTEL_ENABLED":"true", "SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO":"1"})
    try:
        with runtime.activate(), runtime.telemetry.operation("agent.run"):
            pass
        assert runtime.provider.force_flush(2000)
        assert dict(runtime.provider.resource.attributes) == {"service.name":"shop-agent-stack-agent"}
        assert "synthetic-api-key" not in capsys.readouterr().out
    finally:
        runtime.shutdown()


def test_shutdown_is_idempotent_and_redacted(caplog):
    class Failing:
        count = 0
        def shutdown(self):
            self.count += 1
            raise ValueError("synthetic-secret")
    fake = Failing()
    runtime = obs.Runtime(provider=fake)
    runtime.shutdown(); runtime.shutdown()
    assert fake.count == 1
    assert "shutdown_failed" in caplog.text and "synthetic-secret" not in caplog.text


@pytest.mark.parametrize("name", ["deepseek", "openai", "kimi", "custom"])
def test_actual_provider_wire_usage_and_privacy(observer, monkeypatch, name):
    runtime, exporter = observer
    original = httpx.AsyncClient
    def respond(request):
        # Traces are NOT propagated to public/personal model providers.
        assert "traceparent" not in request.headers and "baggage" not in request.headers
        assert request.headers["Authorization"] == "Bearer synthetic-secret-key"
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == "synthetic-private-prompt"
        return httpx.Response(200, json={"choices":[{"message":{"role":"assistant", "content":"synthetic-private-answer",
            "reasoning_content":"synthetic-private-reasoning"}, "finish_reason":"stop"}],
            "usage":{"prompt_tokens":12,"completion_tokens":7,"total_tokens":19}})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    async def run():
        with runtime.activate(), runtime.telemetry.operation("agent.run"):
            message, usage = await providers.complete(name, [{"role":"user","content":"synthetic-private-prompt"}], [],
                config={"model":"synthetic-private-model", "key":"synthetic-secret-key", "url":"https://model.example"})
        assert usage["total_tokens"] == 19 and message["reasoning_content"] == "synthetic-private-reasoning"
    asyncio.run(run())
    spans = exporter.get_finished_spans()
    llm, root = spans
    assert llm.parent.span_id == root.context.span_id
    assert llm.attributes["gen_ai.usage.input_tokens"] == 12 and llm.attributes["gen_ai.usage.output_tokens"] == 7
    text = spans_text(exporter)
    for private in ("synthetic-private", "synthetic-secret", "model.example"):
        assert private not in text


def test_actual_provider_stream_is_incremental_and_trace_lasts_until_done(observer, monkeypatch):
    runtime, exporter = observer
    received = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            payload = 'data: ' + json.dumps({"choices":[{"delta":{"content":"回答", "reasoning_content":"synthetic-private-reasoning"}}]}, ensure_ascii=False) + '\n\n'
            for byte in payload.encode():
                yield bytes([byte])
            assert received == ["回答"] and not exporter.get_finished_spans()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3}}\n\ndata: [DONE]\n\n'
    original = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream())), **kw))
    async def run():
        async def delta(value): received.append(value)
        with runtime.activate():
            result = await providers.complete("deepseek", [], [], config={"model":"synthetic", "key":"synthetic-key", "url":"https://model.example"}, on_delta=delta)
        assert result[0]["content"] == "回答"
    asyncio.run(run())
    assert len(exporter.get_finished_spans()) == 1
    assert exporter.get_finished_spans()[0].attributes["gen_ai.usage.output_tokens"] == 3
    assert "synthetic-private-reasoning" not in spans_text(exporter)


@pytest.mark.parametrize("ending", ["", 'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'])
def test_actual_provider_incomplete_stream_stays_failure(observer, monkeypatch, ending):
    runtime, exporter = observer
    original = httpx.AsyncClient
    text = 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n' + ending
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=text)), **kw))
    async def run():
        async def delta(value): pass
        with runtime.activate(), pytest.raises(ValueError, match="未完整结束"):
            await providers.complete("deepseek", [], [], config={"model":"x", "key":"synthetic-key", "url":"https://model.example"}, on_delta=delta)
    asyncio.run(run())
    assert exporter.get_finished_spans()[0].status.status_code == StatusCode.ERROR
    assert "partial" not in spans_text(exporter)


@pytest.mark.parametrize("value", [True, -1, 1.5, "secret", None, 2**64])
def test_invalid_usage_is_omitted_without_changing_response(observer, value):
    runtime, exporter = observer
    expected = ({"content":"private"}, {"prompt_tokens":value})
    @obs.observe_provider
    async def gateway(provider): return expected
    async def run():
        with runtime.activate(): assert await gateway("custom") is expected
    asyncio.run(run())
    assert "gen_ai.usage.input_tokens" not in exporter.get_finished_spans()[0].attributes


@pytest.mark.parametrize("exception", [ValueError("synthetic-secret"), TimeoutError("synthetic-secret"), asyncio.CancelledError("synthetic-secret")])
def test_exception_identity_and_cancel_propagate(observer, exception):
    runtime, exporter = observer
    @obs.observe_tool
    async def call(session, name, arguments): raise exception
    async def run():
        with runtime.activate():
            with pytest.raises(type(exception)) as caught: await call(None,"get_my_order",{"secret":"synthetic-secret"})
            assert caught.value is exception
    asyncio.run(run())
    assert exporter.get_finished_spans()[0].status.status_code == StatusCode.ERROR
    assert "synthetic-secret" not in spans_text(exporter)


def test_per_request_internal_header_inherits_current_span_not_connect_span(observer):
    runtime, exporter = observer
    async def run():
        with runtime.activate(), runtime.telemetry.operation("agent.run") as root:
            ids = []
            for _ in range(2):
                with runtime.telemetry.operation("tool.call") as child:
                    request = httpx.Request("POST", "http://commerce-mcp/mcp", headers={"X-ShopAgentStack-Execution":"synthetic-grant", "baggage":"private", "tracestate":"private"})
                    await obs.inject_internal_trace(request)
                    pieces = request.headers["traceparent"].split("-")
                    assert int(pieces[2],16) == child.get_span_context().span_id
                    ids.append(pieces[2])
                    assert "baggage" not in request.headers and "tracestate" not in request.headers
                    assert request.headers["X-ShopAgentStack-Execution"] == "synthetic-grant"
            assert ids[0] != ids[1]
    asyncio.run(run())
    assert "synthetic-grant" not in spans_text(exporter)


def test_concurrent_runs_do_not_share_trace_context(observer):
    runtime, exporter = observer
    @obs.observe_provider
    async def gateway(provider):
        await asyncio.sleep(0)
        return {}, {}
    async def one():
        with runtime.activate(), runtime.telemetry.operation("agent.run"):
            await gateway("deepseek")
    async def run(): await asyncio.gather(*(one() for _ in range(12)))
    asyncio.run(run())
    spans = exporter.get_finished_spans()
    assert len(spans) == 24 and len({s.context.trace_id for s in spans}) == 12
    for root in (s for s in spans if s.name == "agent.run"):
        children = [s for s in spans if s.parent and s.parent.span_id == root.context.span_id]
        assert len(children) == 1 and children[0].context.trace_id == root.context.trace_id


@pytest.mark.parametrize("state", ["COMPLETED", "WAITING_CONFIRMATION", "FAILED", "UNCERTAIN", "synthetic-secret-state"])
def test_background_return_not_assumed_success(observer, tmp_path, state):
    runtime, exporter = observer
    store = real_store.Store(str(tmp_path/"state.sqlite"))
    sid=store.new_session(1)["id"];rid,_=store.create_run(sid,1,"synthetic-request","synthetic-message","fixture")
    async def worker(store, run, member, grant, **kw):
        store.state(run["id"], state)
        return "unchanged"
    async def run():
        with runtime.activate():
            assert await obs.execute_run(worker,store,store.run(rid,1),1,"synthetic-grant") == "unchanged"
    asyncio.run(run())
    span=exporter.get_finished_spans()[0]
    assert span.attributes["shop.run.status"] == (state if state in obs._RUN_STATES else "UNKNOWN")
    assert (span.status.status_code == StatusCode.ERROR) == (state in {"FAILED","UNCERTAIN","synthetic-secret-state"})
    assert "synthetic-secret-state" not in spans_text(exporter)


def load_app_with_boundary_doubles(monkeypatch, tmp_path, runtime):
    """Load exact app/tools files in a private namespace, not a simulated replacement.

    Only Java identity/Preferences/LangGraph runner and MCP SDK transport are doubled;
    FastAPI, Store, provider gateway, SDK and the modified app/tools source are real.
    """
    root=Path(__file__).resolve().parents[1]/"shop_agent_stack"
    name="_shop_contract_"+uuid4().hex
    package=types.ModuleType(name);package.__path__=[str(root)]
    monkeypatch.setitem(sys.modules,name,package)
    for suffix, module in {"observability":obs, "providers":providers, "store":real_store}.items():
        monkeypatch.setitem(sys.modules,name+"."+suffix,module);setattr(package,suffix,module)
    business=types.ModuleType(name+".business")
    business.BusinessError=real_store.StoreError
    async def identity(bearer):
        if bearer not in {"Bearer user-1","Bearer user-2"}: raise real_store.StoreError("synthetic denied",401)
        return {"memberId":int(bearer[-1]),"executionToken":"synthetic-execution-secret"}
    async def java(*a,**kw): raise AssertionError("No real Java call permitted")
    business.identity=identity;business.java=java
    monkeypatch.setitem(sys.modules,business.__name__,business)
    preferences=types.ModuleType(name+".preferences")
    class Preferences:
        def __init__(self, store): pass
        def catalog(self, member): return [{"id":"fixture","configured":True}]
    preferences.Preferences=Preferences
    monkeypatch.setitem(sys.modules,preferences.__name__,preferences)
    runner=types.ModuleType(name+".runtime")
    async def execute(store, run, member, execution, **kw):
        store.state(run["id"],"RUNNING")
        await providers.complete("fixture", [{"role":"user","content":"订单"}], [])
        store.state(run["id"],"COMPLETED")
    runner.execute=execute
    monkeypatch.setitem(sys.modules,runner.__name__,runner);setattr(package,"runtime",runner)
    # Explicit protocol doubles, only for these adapter/component contracts.
    mcp=types.ModuleType("mcp");mcp.ClientSession=object
    client=types.ModuleType("mcp.client");stream=types.ModuleType("mcp.client.streamable_http")
    @asynccontextmanager
    async def no_transport(*a,**kw):
        raise AssertionError("No real MCP transport permitted")
        yield
    stream.streamable_http_client=no_transport
    for k,v in {"mcp":mcp,"mcp.client":client,"mcp.client.streamable_http":stream}.items(): monkeypatch.setitem(sys.modules,k,v)
    for suffix in ("tools","app"):
        spec=importlib.util.spec_from_file_location(name+"."+suffix,root/(suffix+".py"))
        module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,module)
        spec.loader.exec_module(module);setattr(package,suffix,module)
    monkeypatch.setenv("SHOP_AGENT_STACK_AGENT_DB",str(tmp_path/"app.sqlite"))
    monkeypatch.setenv("SHOP_AGENT_STACK_ENABLE_TEST_PROVIDER","true")
    monkeypatch.setattr(obs.Runtime,"from_env",classmethod(lambda cls, env=None:runtime))
    return package.app


def test_actual_app_post_background_span_and_sse(observer, monkeypatch, tmp_path):
    runtime, exporter=observer
    module=load_app_with_boundary_doubles(monkeypatch,tmp_path,runtime)
    async def run():
        async with module.app.router.lifespan_context(module.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url="http://test") as client:
                auth={"Authorization":"Bearer user-1"}
                sid=(await client.post("/sessions",headers=auth)).json()["id"]
                exporter.clear()
                malicious="00-"+"1"*32+"-"+"2"*16+"-01"
                r=await client.post(f"/sessions/{sid}/runs",headers={**auth,"traceparent":malicious},json={"message":"synthetic-private-message","provider":"fixture","request_id":str(uuid4())})
                assert r.status_code==200
                await asyncio.gather(*list(module.tasks.values()))
                spans=exporter.get_finished_spans()
                root=next(s for s in spans if s.name=="http.request")
                background=next(s for s in spans if s.name=="agent.run")
                llm=next(s for s in spans if s.name=="llm.call")
                assert background.parent.span_id==root.context.span_id and llm.parent.span_id==background.context.span_id
                assert r.headers["x-trace-id"]==f"{root.context.trace_id:032x}" != "1"*32
                assert background.attributes["shop.run.status"]=="COMPLETED"
                assert llm.attributes["shop.provider.fixture"] is True
                assert "gen_ai.usage.input_tokens" not in llm.attributes
                events=await client.get(f"/runs/{r.json()['id']}/events",headers=auth)
                assert events.status_code==200 and "text/event-stream" in events.headers["content-type"]
                assert "COMPLETED" in events.text and "X-ShopAgentStack-Execution" not in events.text
                forbidden=await client.get(f"/runs/{r.json()['id']}",headers={"Authorization":"Bearer user-2"})
                assert forbidden.status_code==404
    asyncio.run(run())
    text=spans_text(exporter)
    for private in ("synthetic-private-message","synthetic-execution-secret","Bearer user-1"):
        assert private not in text


def test_actual_tools_call_and_allowlist_unchanged(observer, monkeypatch, tmp_path):
    runtime, exporter=observer
    module=load_app_with_boundary_doubles(monkeypatch,tmp_path,runtime)
    assert "submit_after_sale" not in module.tools.MODEL_TOOLS
    class Session:
        async def call_tool(self,name,arguments):
            assert name=="get_my_order" and arguments=={"order_id":123}
            return types.SimpleNamespace(isError=False,structuredContent={"private":"synthetic-secret-order"})
    async def run():
        with runtime.activate():
            assert await module.tools.call(Session(),"get_my_order",{"order_id":123})=={"private":"synthetic-secret-order"}
    asyncio.run(run())
    assert exporter.get_finished_spans()[0].attributes["shop.tool.name"]=="get_my_order"
    assert "synthetic-secret-order" not in spans_text(exporter)


def test_actual_app_default_disabled_is_unchanged(monkeypatch, tmp_path):
    module=load_app_with_boundary_doubles(monkeypatch,tmp_path,obs.Runtime())
    async def run():
        async with module.app.router.lifespan_context(module.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url="http://test") as client:
                response=await client.get("/health")
                assert response.json()=={"status":"UP"} and "x-trace-id" not in response.headers
    asyncio.run(run())


def test_actual_app_shutdown_drains_background_before_sdk(observer, monkeypatch, tmp_path):
    runtime, exporter=observer
    module=load_app_with_boundary_doubles(monkeypatch,tmp_path,runtime)
    created=[]; stopped=[]; shutdown_checks=[]
    original_shutdown=runtime.shutdown
    def checked_shutdown():
        shutdown_checks.append(all(t.done() for t in created))
        original_shutdown()
    monkeypatch.setattr(runtime,"shutdown",checked_shutdown)
    async def work(store, run, member, execution, **kw):
        created.append(asyncio.current_task())
        store.state(run["id"],"RUNNING")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append(True)
            store.state(run["id"],"STOPPED")
            raise
    module.runtime.execute=work
    async def run():
        async with module.app.router.lifespan_context(module.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url="http://test") as client:
                auth={"Authorization":"Bearer user-1"}
                sid=(await client.post("/sessions",headers=auth)).json()["id"]
                response=await client.post(f"/sessions/{sid}/runs",headers=auth,json={"message":"synthetic","provider":"fixture","request_id":str(uuid4())})
                assert response.status_code==200
                await asyncio.sleep(0)
                assert created and not created[0].done()
        assert stopped==[True] and shutdown_checks==[True]
    asyncio.run(run())
    run_span=next(s for s in exporter.get_finished_spans() if s.name=="agent.run")
    assert run_span.attributes["shop.failure.category"]=="cancelled"


def test_actual_tools_connect_installs_hook_and_preserves_grant(observer, monkeypatch, tmp_path):
    runtime, exporter=observer
    module=load_app_with_boundary_doubles(monkeypatch,tmp_path,runtime)
    clients=[];sent=[]
    @asynccontextmanager
    async def transport(url,http_client):
        clients.append(http_client)
        assert http_client._trust_env is False
        assert http_client.headers["X-ShopAgentStack-Execution"]=="synthetic-secret-grant"
        hook=http_client.event_hooks["request"][0]
        assert hook.__self__.__class__.__name__=="MCPTraceBridge"
        class WriteStream:
            async def send(self,item): pass
        yield (None,WriteStream(),None)
    class Session:
        def __init__(self,read,write): self.write=write
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def initialize(self): pass
        async def call_tool(self,name,arguments):
            # Explicit SDK-shaped double; real background queue has a separate SDK test.
            await self.write.send(types.SimpleNamespace(message=types.SimpleNamespace(root=types.SimpleNamespace(id=1,method="tools/call"))))
            await clients[-1].post("http://commerce-mcp:8011/mcp",json={"id":1,"method":"tools/call","params":{"arguments":{}}})
            return types.SimpleNamespace(isError=False,structuredContent={"ok":True})
    module.tools.streamable_http_client=transport;module.tools.ClientSession=Session
    original=httpx.AsyncClient
    def respond(request):
        sent.append(request)
        return httpx.Response(200,json={})
    monkeypatch.setattr(module.tools.httpx,"AsyncClient",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    async def run():
        with runtime.activate(), runtime.telemetry.operation("agent.run"):
            async with module.tools.connect("synthetic-secret-grant") as session:
                await module.tools.call(session,"get_my_order",{})
    asyncio.run(run())
    assert len(sent)==1
    child=next(s for s in exporter.get_finished_spans() if s.name=="tool.call")
    assert int(sent[0].headers["traceparent"].split("-")[2],16)==child.context.span_id
    assert sent[0].headers["X-ShopAgentStack-Execution"]=="synthetic-secret-grant"
    assert "synthetic-secret-grant" not in spans_text(exporter)


def test_real_otlp_encoding_with_explicit_http_session_double(monkeypatch):
    # Real SDK + OTLP protobuf encoder/exporter. HTTP peer is a double, not a collector.
    import requests
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    sessions=[];payloads=[]
    class Session(requests.Session):
        def __init__(self):
            super().__init__();sessions.append(self)
        def post(self,url,*args,**kwargs):
            assert self.trust_env is False
            assert url=="http://collector.example:4318/v1/traces"
            decoded=ExportTraceServiceRequest();decoded.ParseFromString(kwargs["data"])
            payloads.append(decoded)
            response=requests.Response();response.status_code=200;response._content=b"";response.url=url
            return response
    monkeypatch.setattr(requests,"Session",Session)
    runtime=obs.Runtime.from_env({"SHOP_AGENT_STACK_OTEL_ENABLED":"true", "SHOP_AGENT_STACK_OTEL_EXPORTER":"otlp", "SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO":"1", "SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT":"http://collector.example:4318/v1/traces"})
    try:
        with runtime.activate(), runtime.telemetry.operation("agent.run"):
            pass
        assert runtime.provider.force_flush(2000)
        assert payloads and sessions
        spans=payloads[0].resource_spans[0].scope_spans[0].spans
        assert len(spans)==1 and spans[0].name=="agent.run"
    finally:
        runtime.shutdown()
