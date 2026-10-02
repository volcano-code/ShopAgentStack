"""Fixture/real-Store contract tests only; never a model benchmark."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import pytest
from tools.shop_quality.agent_adapter import adapt
from tools.shop_quality.contracts import ContractError, sha256
from tools.shop_quality.evaluator import evaluate, apply_gates
from tools.shop_quality.export_agent import main

HASH='a'*64
PROMPT_HASH=hashlib.sha256(b'synthetic prompt').hexdigest()


def inputs():
    cases=[{'case_id':'case-1','split':'heldout','qrels':{'P1V1C1':3},'expected_citation_ids':['P1V1C1'],
            'allowed_tools':['search_policies'],'expected_tools':['search_policies'],
            'should_abstain':False,'check_write_safety':False}]
    raw=[{'id':'synthetic-run','session_id':'private-session','input':'synthetic-private-prompt','provider':'fixture',
          'status':'COMPLETED','created':1,'events':[
              {'id':1,'kind':'tool','data':{'name':'search_policies','status':'started'}},
              {'id':2,'kind':'retrieval','data':{'count':2,'elapsed_ms':1}},
              {'id':3,'kind':'preview','data':{'confirmation_token':'synthetic-secret-token'}},
              {'id':4,'kind':'usage','data':{'reported_tokens':987}},
              {'id':5,'kind':'assistant','data':{'text':'synthetic-private-answer [P1V1C1] [P1V1C1]'}}]}]
    obs=[{'case_id':'case-1','run_id':'synthetic-run','latency_ms':12,'abstained':False,
          'retrieval_unit':'initial_query','retrieved_ids':['P2V1C1','P1V1C1'],
          'task_judgment':{'passed':True,'method':'human','reference':'synthetic-review-1'}}]
    exp={'schema_version':1,'experiment_id':'synthetic-contract','source_kind':'fixture','git_revision':'d'*40,
         'code_patch_sha256':None,'dataset_sha256':HASH,'prompt_version':'synthetic-v1','prompt_sha256':PROMPT_HASH,
         'provider':'fixture','model':'deterministic-fixture','retriever':{'mode':'bm25','embedding_model':None,
         'reranker_model':None,'top_k':5},'started_at':'2026-09-28T00:00:00Z','finished_at':'2026-09-28T00:01:00Z',
         'task_unit':'agent_turn'}
    return cases,raw,obs,exp


def convert(data):
    return adapt(*data,cases_hash=HASH,prompt_hash=PROMPT_HASH)


def test_adapter_does_not_infer_ranking_from_final_citations():
    data=inputs()
    rows,manifest,proof=convert(data)
    assert rows[0]['retrieved_ids']==['P2V1C1','P1V1C1']
    assert rows[0]['citation_ids']==['P1V1C1']
    assert rows[0]['selected_tools']==['search_policies']
    assert 'input_tokens' not in rows[0] and 'output_tokens' not in rows[0] and 'cost_usd' not in rows[0]
    report=evaluate(data[0],rows,manifest,HASH,ks=(1,5))
    assert report['metrics']['recall@1']['value']==0
    assert report['metrics']['recall@5']['value']==1
    assert report['claim_scope']=='fixture_only'
    serialized=json.dumps([rows,manifest,proof])
    for secret in ('synthetic-private','synthetic-secret','private-session'):
        assert secret not in serialized


def test_missing_cases_and_unjudged_tasks_stay_in_denominator():
    data=inputs()
    data[0].append({**data[0][0],'case_id':'case-missing'})
    del data[2][0]['task_judgment']
    rows,manifest,proof=convert(data)
    report=evaluate(data[0],rows,manifest,HASH)
    assert report['dataset_cases']==2
    assert report['metrics']['task_success_rate']['value']==0
    assert proof['missing_cases']==['case-missing']
    assert proof['availability']['task_judgment']==0


def test_missing_audit_cannot_pass_safety_gate():
    data=inputs(); data[0][0]['check_write_safety']=True
    rows,manifest,_=convert(data)
    report=evaluate(data[0],rows,manifest,HASH)
    gate=apply_gates(report,{'schema_version':1,'min_cases':1,'thresholds':{'task_success_rate':{'min_samples':1,'min':0}}})
    assert not gate['passed'] and report['counts']['unknown_write_cases']==1


@pytest.mark.parametrize('state,expected', [('COMPLETED','ok'),('WAITING_CONFIRMATION','ok'),('FAILED','error'),
                                         ('STOPPED','error'),('INTERRUPTED','error'),('UNCERTAIN','error')])
def test_status_is_agent_turn_not_refund_success(state,expected):
    data=inputs();data[1][0]['status']=state
    rows,_,proof=convert(data)
    assert rows[0]['status']==expected
    assert 'NOT a committed refund' in ' '.join(proof['limitations'])


def test_independent_timeout_evidence_is_required():
    data=inputs();data[1][0]['status']='FAILED';data[2][0]['termination']='timeout'
    assert convert(data)[0][0]['status']=='timeout'
    data[1][0]['status']='COMPLETED'
    with pytest.raises(ContractError): convert(data)


@pytest.mark.parametrize('mutation', [
    lambda c,r,o,e:o[0].pop('retrieved_ids'),
    lambda c,r,o,e:o[0].update(retrieval_unit='final_citations'),
    lambda c,r,o,e:r[0].update(provider='deepseek'),
    lambda c,r,o,e:e.update(source_kind='recorded'),
    lambda c,r,o,e:e.update(prompt_sha256='b'*64),
    lambda c,r,o,e:e.update(dataset_sha256='b'*64),
    lambda c,r,o,e:e.update(task_unit='refund'),
    lambda c,r,o,e:e.update(git_revision='main'),
    lambda c,r,o,e:e.update(finished_at='2026-09-27T00:00:00Z'),
    lambda c,r,o,e:e.update(started_at='not-a-dateZ'),
    lambda c,r,o,e:r[0].update(status='RUNNING'),
    lambda c,r,o,e:r.append(deepcopy(r[0])),
    lambda c,r,o,e:r.append({**r[0],'id':'unmapped-failure','status':'FAILED'}),
    lambda c,r,o,e:o.append(deepcopy(o[0])),
    lambda c,r,o,e:o[0].update(latency_ms=-1),
    lambda c,r,o,e:o[0].update(latency_ms=float('nan')),
    lambda c,r,o,e:o[0].update(input_tokens=True),
    lambda c,r,o,e:o[0].update(trace_id='0'*32),
    lambda c,r,o,e:o[0].update(task_judgment={'passed':True,'method':'model','reference':'self'}),
    lambda c,r,o,e:r[0]['events'].append(deepcopy(r[0]['events'][0])),
])
def test_bad_or_mislabeled_evidence_is_rejected(mutation):
    data=inputs();mutation(*data)
    with pytest.raises(ContractError): convert(data)


def test_recorded_mode_is_not_rebranded_as_live_benchmark():
    data=inputs();data[1][0]['provider']='custom';data[3].update(provider='custom',source_kind='recorded')
    rows,manifest,_=convert(data)
    assert manifest['source_kind']=='recorded'
    data[2][0]['write_audit']={'source':'fixture','complete':True,'events':[]}
    with pytest.raises(ContractError):convert(data)


def test_actual_sqlite_store_snapshot_can_be_adapted(tmp_path):
    # Use the unmodified real Store class; only the run event producer is synthetic.
    path=Path(__file__).resolve().parents[3]/'services/agent/shop_agent_stack/store.py'
    spec=importlib.util.spec_from_file_location('real_store_for_adapter',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    store=module.Store(str(tmp_path/'actual.sqlite'))
    sid=store.new_session(1)['id']
    begin=time.monotonic()
    rid,_=store.create_run(sid,1,'request-1','synthetic-private-prompt','fixture')
    store.emit(rid,'tool',{'name':'search_policies','status':'started'})
    store.emit(rid,'assistant',{'text':'synthetic-answer [P1V1C1]'})
    store.state(rid,'COMPLETED')
    data=inputs();data[1][:]=[store.run(rid,1)]
    data[2][0].update(run_id=rid,latency_ms=(time.monotonic()-begin)*1000)
    rows,_,_=convert(data)
    assert rows[0]['citation_ids']==['P1V1C1'] and rows[0]['latency_ms']>0


def cli_inputs(tmp_path):
    cases,raw,obs,exp=inputs()
    def jsonl(name,data):
        p=tmp_path/name;p.write_text(''.join(json.dumps(r)+'\n' for r in data));return p
    cp=jsonl('cases.jsonl',cases);rp=jsonl('raw.jsonl',raw);op=jsonl('observations.jsonl',obs)
    prompt=tmp_path/'prompt.txt';prompt.write_bytes(b'synthetic prompt')
    exp['dataset_sha256']=sha256(cp)
    ep=tmp_path/'experiment.json';ep.write_text(json.dumps(exp))
    return ['--cases',str(cp),'--raw-runs',str(rp),'--observations',str(op),'--experiment',str(ep),
            '--prompt-file',str(prompt),'--out',str(tmp_path/'export')]


def test_cli_requires_explicit_fixture_and_preserves_existing_evidence(tmp_path):
    args=cli_inputs(tmp_path)
    assert main(args)==2 and not (tmp_path/'export').exists()
    assert main(args+['--allow-fixture'])==0
    original=(tmp_path/'export/runs.jsonl').read_bytes()
    assert main(args+['--allow-fixture'])==2
    assert (tmp_path/'export/runs.jsonl').read_bytes()==original
    combined=''.join(p.read_text() for p in (tmp_path/'export').iterdir())
    assert 'synthetic-secret' not in combined and 'synthetic-private' not in combined
    assert 'input_hashes' in combined
    if __import__('os').name=='posix':
        assert (tmp_path/'export').stat().st_mode&0o077==0
        assert (tmp_path/'export/runs.jsonl').stat().st_mode&0o077==0


@pytest.mark.parametrize('target',['source_kind','provider','mode','status'])
def test_malformed_enum_values_fail_as_contract_errors(target):
    data=inputs()
    if target=='mode':data[3]['retriever']['mode']=[]
    elif target=='status':data[1][0]['status']=[]
    else:data[3][target]=[]
    with pytest.raises(ContractError):convert(data)
