"""Only the hosted workflow can validate actual application-to-Tempo transport."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from tools.shop_e2e import traces as t, stack as s

TID = '1'*32
ROOT = Path(__file__).resolve().parents[3]


def payload(kind='approval'):
    if kind == 'approval':
        descriptions = [('commerce.request', None, 'shop-commerce-admin'), ('refund.publish', 1, 'shop-commerce-admin'),
                        ('refund.consume', 2, 'shop-commerce-admin'), ('db.refund.transaction', 3, 'shop-commerce-admin')]
    else:
        descriptions = [('http.request', None, 'shop-agent-stack-agent'), ('agent.run', 1, 'shop-agent-stack-agent'),
                        ('llm.call', 2, 'shop-agent-stack-agent'), ('tool.call', 2, 'shop-agent-stack-agent'),
                        ('mcp.request', 4, 'shop-agent-stack-mcp'), ('commerce.call', 5, 'shop-agent-stack-mcp'),
                        ('commerce.request', 6, 'shop-commerce-portal')]
    result = {'resourceSpans': []}
    for i, (name, parent, service) in enumerate(descriptions, 1):
        span = {'name': name, 'traceId': TID, 'spanId': f'{i:016x}', 'attributes': []}
        if parent: span['parentSpanId'] = f'{parent:016x}'
        result['resourceSpans'].append({'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': service}}]},
                                       'scopeSpans': [{'spans': [span]}]})
    return result


@pytest.mark.parametrize('kind', ['agent', 'approval'])
def test_full_chain_with_separate_human_request(kind):
    result = t.verify(payload(kind), TID, kind)
    assert result['verified'] and result['span_count'] == (7 if kind == 'agent' else 4)
    assert 'attributes' not in json.dumps(result)


@pytest.mark.parametrize('error', ['missing','orphan','cycle','wrong-service','wrong-trace','duplicate',
                                 'event','prompt','status-message','error','links','baggage','scope','zero','unknown-name'])
def test_backend_evidence_fails_closed(error):
    data = payload()
    group = data['resourceSpans'][-1]
    span = group['scopeSpans'][0]['spans'][0]
    if error == 'missing': data['resourceSpans'].pop()
    if error == 'orphan': span['parentSpanId'] = 'a'*16
    if error == 'cycle': span['parentSpanId'] = span['spanId']
    if error == 'wrong-service': group['resource']['attributes'][0]['value']['stringValue'] = 'shop-commerce-portal'
    if error == 'wrong-trace': span['traceId'] = 'a'*32
    if error == 'duplicate': data['resourceSpans'].append(deepcopy(group))
    if error == 'event': span['events'] = [{'name':'private-message'}]
    if error == 'prompt': span['attributes'].append({'key':'prompt','value':{'stringValue':'secret'}})
    if error == 'status-message': span['status'] = {'message':'private-error'}
    if error == 'error': span['status'] = {'code':2}
    if error == 'links': span['links'] = [{'attributes':[]}]
    if error == 'baggage': group['resource']['attributes'].append({'key':'baggage'})
    if error == 'scope': group['scopeSpans'][0]['scope'] = {'attributes':[{'key':'private'}]}
    if error == 'zero': span['spanId'] = '0'*16
    if error == 'unknown-name': span['name'] = '/url/with/customer/id'
    with pytest.raises(ValueError): t.verify(data, TID, 'approval')


@pytest.mark.parametrize('data',[None,[],{}, {'trace':[]}, {'resourceSpans':{}}, {'resourceSpans':[1]}, {'resourceSpans':[{'resource':None}]}])
def test_malformed_trace_is_not_acceptance(data):
    with pytest.raises(ValueError): t.verify(data, TID, 'approval')


@pytest.mark.parametrize('value',['0'*32,True,'A'*32,'1'*31,'1'*32+'\n',None])
def test_trace_receipt_validation(value):
    with pytest.raises(ValueError): t.trace_id(value)


def test_tracing_stack_is_explicit_internal_and_test_only():
    p='shop-e2e-'+'a'*32;state=ROOT/'.local/test'
    off=s.compose(ROOT,state,p);on=s.compose(ROOT,state,p,tracing=True)
    assert 'tempo' not in off['services']
    for name in ('tempo','otel-collector'):
        assert on['services'][name]['networks']==['business']
        assert 'ports' not in on['services'][name]
        assert on['services'][name]['labels']=={s.LABEL:p}
    for name in ('portal','admin','agent','commerce-mcp'):
        assert 'SHOP_AGENT_STACK_OTEL_ENABLED' not in off['services'][name]['environment']
        assert on['services'][name]['environment']['SHOP_AGENT_STACK_OTEL_ENABLED']=='true'
    assert on['services']['commerce-mcp']['command'][1]=='shop_agent_stack.mcp_observed:app'
    assert on['services']['portal']['environment']['SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL']=='true'
    assert 'SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL' not in on['services']['admin']['environment']
    proxy=(ROOT/'deploy/nginx/shop_agent_stack-p2.conf').read_text()
    for key in ('traceparent','tracestate','baggage'):
        assert proxy.count(f'proxy_set_header {key} "";')==3


def test_traced_workflow_runs_new_java_tests_and_actual_readback():
    w=yaml.load((ROOT/'.github/workflows/business-tracing-e2e.yml').read_text(),Loader=yaml.BaseLoader)
    assert w['permissions']=={'contents':'read'} and 'pull_request_target' not in w['on']
    text=[]
    for step in w['jobs']['business']['steps']:
        assert 'continue-on-error' not in step
        if 'uses' in step: assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}',step['uses'])
        if 'run' in step:
            code=step['run'];text.append(code)
            assert subprocess.run(['bash','-n'],input=code,text=True,capture_output=True).returncode==0
    text='\n'.join(text)
    assert 'CommerceTracingTest' in text and 'tools.shop_e2e run --tracing' in text
    assert 'secrets.' not in text and '|| true' not in text
