"""Smoke logic/configuration tests. HTTP replies here are test doubles, not Tempo."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import pytest
import yaml
from tools.shop_observability import collector_smoke as s
from tools.shop_observability.local_env import create

ROOT=Path(__file__).resolve().parents[3]


def clean_response(payload):
    result=deepcopy(payload)
    for group in result['resourceSpans']:
        group['resource']['attributes']=group['resource']['attributes'][:1]
        for scope in group['scopeSpans']:
            for span in scope['spans']:
                span['attributes']=[];span['events']=[];span['status'].pop('message')
    return result


def test_smoke_parser_verifies_full_tree_including_base64_ids():
    tid,payload,expected=s.build_payload()
    reply=clean_response(payload)
    assert s.verify_trace(reply,tid,expected)==5
    import base64
    for group in reply['resourceSpans']:
        for span in group['scopeSpans'][0]['spans']:
            for key in ('traceId','spanId','parentSpanId'):
                if key in span:span[key]=base64.b64encode(bytes.fromhex(span[key])).decode()
    assert s.verify_trace({'trace':reply},tid,expected)==5


@pytest.mark.parametrize('case',['marker','missing','wrong-parent','wrong-trace','duplicate','bad-id'])
def test_bad_backend_evidence_fails(case):
    tid,payload,expected=s.build_payload();reply=clean_response(payload)
    spans=reply['resourceSpans'][0]['scopeSpans'][0]['spans']
    if case=='marker':reply['secret']=s.MARKER
    if case=='missing':spans.pop()
    if case=='wrong-parent':spans[1]['parentSpanId']='f'*16
    if case=='wrong-trace':spans[1]['traceId']='f'*32
    if case=='duplicate':spans.append(deepcopy(spans[0]))
    if case=='bad-id':spans[1]['spanId']='bad'
    with pytest.raises(s.SmokeError):s.verify_trace(reply,tid,expected)


def test_post_ack_alone_is_not_success(monkeypatch):
    monkeypatch.setattr(s,'request',lambda *a,**k:(200,b'{}'))
    times=iter([0,0.1,0.2,2])
    monkeypatch.setattr(s.time,'monotonic',lambda:next(times))
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    with pytest.raises(s.SmokeError,match='deadline'):s.smoke('http://localhost:4318','http://localhost:3200',wait_seconds=1)


def test_smoke_orchestration_with_explicit_http_double(monkeypatch):
    received={}
    def request(url,payload=None):
        if payload is not None:
            received['document']=clean_response(payload)
            return 200,b'{}'
        return 200,json.dumps(received['document']).encode()
    monkeypatch.setattr(s,'request',request)
    result=s.smoke('http://localhost:4318','http://localhost:3200',wait_seconds=1)
    assert result['verified_spans']==5 and result['claim_scope']=='synthetic_transport_smoke_only'
    assert not result['business_e2e_verified']


@pytest.mark.parametrize('ack',[{'partialSuccess':{'rejectedSpans':'1'}},{'partialSuccess':{'errorMessage':'failed'}}])
def test_partial_otlp_ack_fails(monkeypatch,ack):
    monkeypatch.setattr(s,'request',lambda *a,**kw:(200,json.dumps(ack).encode()))
    with pytest.raises(s.SmokeError,match='partial'):s.smoke('http://localhost:4318','http://localhost:3200')


@pytest.mark.parametrize('url',['https://public.example','http://127.0.0.1@public.example','http://localhost?token=secret',
                               'http://localhost:bad','http://localhost/private','http://localhost/#secret'])
def test_probe_destinations_are_local_only(url):
    with pytest.raises(s.SmokeError):s.endpoint(url,{'localhost','127.0.0.1'})


def test_cli_failure_and_no_overwrite(monkeypatch,tmp_path):
    def broken(*a,**k):raise s.SmokeError('synthetic-secret-error')
    monkeypatch.setattr(s,'smoke',broken)
    output=tmp_path/'evidence.json'
    assert s.main(['--out',str(output)])==1
    assert json.loads(output.read_text())['status']=='failed'
    assert 'synthetic-secret-error' not in output.read_text()
    assert s.main(['--out',str(output)])==2


def test_private_env_refuses_overwrite(tmp_path):
    target=tmp_path/'.env';create(target);original=target.read_bytes()
    assert original.startswith(b'SHOP_OBS_GRAFANA_PASSWORD=')
    with pytest.raises(FileExistsError):create(target)
    assert target.read_bytes()==original
    if __import__('os').name=='posix':assert target.stat().st_mode&0o077==0


def test_config_is_pinned_local_bounded_and_no_fake_health_shell():
    config=yaml.safe_load((ROOT/'deploy/observability/compose.yaml').read_text())
    assert set(config['services'])=={'otel-collector','tempo','grafana'}
    for service in config['services'].values():
        assert ':latest' not in service['image'] and re.search(r':\d+\.\d+\.\d+$',service['image'])
        assert service['cap_drop']==['ALL'] and service['read_only']
        assert service['mem_limit']
        for port in service.get('ports',[]):assert port.startswith('127.0.0.1:')
    assert config['networks']['default']['internal']
    assert config['services']['grafana']['environment']['GF_AUTH_ANONYMOUS_ENABLED']=='false'
    assert ':?' in config['services']['grafana']['environment']['GF_SECURITY_ADMIN_PASSWORD']
    collector=yaml.safe_load((ROOT/'deploy/observability/otel-collector.yaml').read_text())
    assert collector['processors']['transform/privacy']['error_mode']=='propagate'
    assert 'debug' not in collector['exporters']
    assert collector['service']['pipelines']['traces']['processors'][0]=='memory_limiter'
    assert collector['processors']['filter/drop_events']['traces']['spanevent']==['true']
    json.loads((ROOT/'deploy/observability/grafana/dashboards/shop-traces.json').read_text())


def test_manual_workflow_has_no_paid_keys_and_fails_on_missing_readback():
    w=yaml.load((ROOT/'.github/workflows/observability-smoke.yml').read_text(),Loader=yaml.BaseLoader)
    assert set(w['on'])=={'workflow_dispatch'} and w['permissions']=={'contents':'read'}
    runs=[]
    for job in w['jobs'].values():
        for step in job['steps']:
            assert 'continue-on-error' not in step
            if 'uses' in step:assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}',step['uses'])
            if 'run' in step:
                run=re.sub(r'\$\{\{.*?\}\}','VALUE',step['run']);runs.append(run)
                result=subprocess.run(['bash','-n'],input=run,text=True,capture_output=True)
                assert result.returncode==0,result.stderr
                assert '|| true' not in run and 'secrets.' not in run
    assert any('collector_smoke' in run for run in runs)
    assert any('config --quiet' in run for run in runs)


@pytest.mark.parametrize('reply',[[],{'trace':[]},{'resourceSpans':{}},{'resourceSpans':[1]},
                                 {'resourceSpans':[{'scopeSpans':{}}]},
                                 {'resourceSpans':[{'scopeSpans':[1]}]},
                                 {'resourceSpans':[{'scopeSpans':[{'spans':[1]}]}]}])
def test_malformed_backend_document_fails_closed(reply):
    tid,_,expected=s.build_payload()
    with pytest.raises(s.SmokeError):s.verify_trace(reply,tid,expected)


@pytest.mark.parametrize('ack',[[],{'partialSuccess':[]}])
def test_malformed_ack_is_not_success(monkeypatch,ack):
    monkeypatch.setattr(s,'request',lambda *a,**kw:(200,json.dumps(ack).encode()))
    with pytest.raises(s.SmokeError):s.smoke('http://localhost:4318','http://localhost:3200')
