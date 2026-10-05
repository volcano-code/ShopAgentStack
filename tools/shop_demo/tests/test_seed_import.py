"""Local contract tests. Docker/Java business acceptance belongs to the hosted smoke."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock

import pytest
from tools.shop_demo import seed, seed_data, state as st

ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def catalog(tmp_path):
    root=tmp_path/'repo'; state=root/'.local/demo'
    for name in ('catalog/products.tsv','knowledge/catalog-v2.json','knowledge/authoring/policies.tsv','knowledge/authoring/staff-policies.tsv'):
        dst=root/name; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,dst)
    images=state/'runtime/apps/web/dist/products/catalog-v1';images.mkdir(parents=True)
    for src in (ROOT/'apps/web/public/products/catalog-v1').glob('*.webp'):
        (images/src.name).write_bytes(b'unit-image-not-rendered')
    return root,state


def test_actual_synthetic_sources_and_snapshot_images(catalog):
    b=seed_data.bundle(*catalog)
    assert len(b['products'])==100 and len(b['policies'])==96
    assert sum(x['visibility']=='STAFF' for x in b['policies'])==16
    assert b['products'][0]['price']=='59.00'
    assert len(b['sourceSha256'])==64

@pytest.mark.parametrize('field,value', [('price','NaN'),('stock','-1'),('weight_g','0'),('slug','../escape')])
def test_invalid_product_refused(catalog,field,value):
    root,state=catalog; path=root/'catalog/products.tsv'; lines=path.read_text().splitlines(); cols=lines[0].split('|'); row=lines[1].split('|');row[cols.index(field)]=value;lines[1]='|'.join(row);path.write_text('\n'.join(lines)+'\n')
    with pytest.raises(ValueError):seed_data.bundle(root,state)

@pytest.mark.parametrize('mutation',['visibility','hash','title','status','count','source','extra-row'])
def test_policy_manifest_conflict_refused(catalog,mutation):
    root,state=catalog; p=root/'knowledge/catalog-v2.json'; data=json.loads(p.read_text())
    if mutation=='visibility':data['policies'][0]['visibility']='STAFF'
    elif mutation=='hash':data['policies'][0]['clauses'][0]['sha256']='0'*64
    elif mutation=='title':data['policies'][0]['title']='modified'
    elif mutation=='status':data['policies'][0]['publication_status']='PUBLISHED'
    elif mutation=='count':data['documents']=95
    elif mutation=='source':data['source']='unreviewed'
    else:data['policies'].pop()
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):seed_data.bundle(root,state)

@pytest.mark.parametrize('kind',['missing','symlink'])
def test_image_inventory_refuses_missing_or_symlink(catalog,kind):
    root,state=catalog;p=next((state/'runtime/apps/web/dist/products/catalog-v1').glob('*.webp'));p.unlink()
    if kind=='symlink':p.symlink_to(root/'catalog/products.tsv')
    with pytest.raises(ValueError):seed_data.bundle(root,state)


def test_image_digest_bound_to_source(catalog):
    first=seed_data.bundle(*catalog)['sourceSha256'];next((catalog[1]/'runtime/apps/web/dist/products/catalog-v1').glob('*.webp')).write_bytes(b'changed')
    assert seed_data.bundle(*catalog)['sourceSha256']!=first


def test_import_flag_disabled_in_old_and_default_states(checkout,options):
    p=checkout/'.local/demo';st.init(checkout,p,options);owner,spec=st.load(checkout,p)
    assert 'seed_import' not in owner['options']
    assert all('SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED' not in s.get('environment',{}) for s in spec['services'].values())


def test_flag_only_enables_java_admin(checkout,options):
    p=checkout/'.local/demo';st.init(checkout,p,options|{'seed_import':True});_,spec=st.load(checkout,p)
    assert {n for n,s in spec['services'].items() if s.get('environment',{}).get('SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED')=='true'}=={'admin'}

@pytest.mark.parametrize('bad',[1,'true',None,{}])
def test_invalid_import_flag_refused(checkout,options,bad):
    with pytest.raises(ValueError):st.init(checkout,checkout/'.local/demo',options|{'seed_import':bad})


def test_import_disabled_refuses_before_network(checkout,options,monkeypatch):
    p=checkout/'.local/demo';st.init(checkout,p,options);monkeypatch.setattr(seed,'ROOT',checkout)
    net=Mock();monkeypatch.setattr(seed.runtime,'local_engine',net)
    with pytest.raises(ValueError,match='NEW'):seed.preview(p)
    net.assert_not_called()

@pytest.fixture
def saved_plan(tmp_path,monkeypatch):
    state=tmp_path/'.local/demo';(state/'imports').mkdir(parents=True);(state/'manifest.json').write_text('{}')
    owner={'project':'shop-demo-'+'a'*32}
    plan={'id':'1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12,'action':'SEED','bundle_hash':'b'*64,
          'confirmation_hash':'c'*64,'precondition':{'mode':'CREATE_PRODUCTS_AND_DRAFTS'},'review':{},'expires_at':'tomorrow','status':'PREVIEW'}
    (state/'imports'/f"{plan['id']}.json").write_text(json.dumps({'project':owner['project'],'snapshot':st.sha(state/'manifest.json'),'plan':plan}))
    api=Mock(); api.call.side_effect=[deepcopy(plan),{'status':'APPLIED','previewId':plan['id'],'bundleHash':plan['bundle_hash']}]
    monkeypatch.setattr(seed,'ROOT',tmp_path);monkeypatch.setattr(seed,'_api',lambda _: (owner,api))
    return state,plan,api


def test_apply_sends_only_reviewed_confirmation(saved_plan):
    state,p,api=saved_plan;assert seed.apply(state,p['id'],p['confirmation_hash'])['status']=='APPLIED'
    assert api.call.call_args.args==(seed.ENDPOINT+'/'+p['id']+'/apply',{'confirmation':'c'*64})

@pytest.mark.parametrize('field', ['id','action','bundle_hash','confirmation_hash','precondition','review','expires_at'])
def test_tampered_plan_refused_without_mutation(saved_plan,field):
    state,p,api=saved_plan;changed=deepcopy(p);changed[field]='changed';api.call.side_effect=[changed]
    with pytest.raises(ValueError):seed.apply(state,p['id'],p['confirmation_hash'])
    assert api.call.call_count==1


def test_wrong_confirmation_never_posts(saved_plan):
    state,p,api=saved_plan
    with pytest.raises(ValueError):seed.apply(state,p['id'],'d'*64)
    assert api.call.call_count==1


def test_lost_response_can_replay_same_receipt(saved_plan):
    state,p,api=saved_plan;plan=deepcopy(p);plan['status']='APPLIED';plan['result']={}
    api.call.side_effect=[plan,{'status':'APPLIED','previewId':p['id'],'bundleHash':p['bundle_hash']}]
    assert seed.apply(state,p['id'],p['confirmation_hash'])['status']=='APPLIED'

@pytest.mark.parametrize('mutation',['project','snapshot'])
def test_plan_for_another_state_refused(saved_plan,mutation):
    state,p,api=saved_plan;file=state/'imports'/f"{p['id']}.json";data=json.loads(file.read_text());data[mutation]='changed';file.write_text(json.dumps(data))
    with pytest.raises(ValueError):seed.apply(state,p['id'],p['confirmation_hash'])
    api.call.assert_not_called()


def test_redirect_handler_never_forwards_authorization():
    req=urllib_request=__import__('urllib.request',fromlist=['Request']).Request('http://127.0.0.1:18030/api/admin/admin/login',headers={'Authorization':'unit-secret'})
    assert seed.NoRedirect().redirect_request(req,None,302,'redirect',{},'https://example.invalid/steal') is None


def test_network_failure_is_not_retried_and_secret_not_in_error():
    api=object.__new__(seed.AdminAPI);api.url='http://127.0.0.1:18030';api.token='unit-secret';api.opener=Mock()
    api.opener.open.side_effect=OSError('unit-secret')
    with pytest.raises(seed.runtime.DemoError) as error:api.call(seed.ENDPOINT+'/x/apply',{'confirmation':'c'*64})
    assert 'unit-secret' not in str(error.value);assert api.opener.open.call_count==1

@pytest.mark.parametrize('path',['https://example.invalid','//example.invalid/a','/api/portal/orders','/api/admin/x?redirect=evil'])
def test_nonadmin_import_path_refused(path):
    api=object.__new__(seed.AdminAPI)
    with pytest.raises(ValueError):api.call(path)


def valid_evidence():
    return {'retrieval':'bm25','checks':['preview-has-no-business-writes','staff-import-denied','wrong-confirmation-denied',
         'draft-import-atomic','receipt-replay-safe','new-seed-preview-noop','separate-publication','stock-not-reset',
         'stop-restart-retains-import'], 'database':{'products':100,'skus':100,'drafts':0,'published':96,
         'staffClauses':48,'customerClauses':241,'stock':179,'epoch':97},'initial_customer_clauses':1,'initial_epoch':1}


def test_positive_readback_gate():
    from tools.shop_demo.seed_smoke import verify
    verify(valid_evidence())

@pytest.mark.parametrize('field', ['products','skus','drafts','published','staffClauses','customerClauses','stock','epoch'])
def test_bad_readback_cannot_pass(field):
    from tools.shop_demo.seed_smoke import verify
    evidence=valid_evidence();evidence['database'][field]+=1
    with pytest.raises(seed.runtime.DemoError):verify(evidence)

@pytest.mark.parametrize('mutation',['missing','duplicate','index'])
def test_partial_verification_cannot_pass(mutation):
    from tools.shop_demo.seed_smoke import verify
    evidence=valid_evidence()
    if mutation=='missing':evidence['checks'].pop()
    elif mutation=='duplicate':evidence['checks'][-1]=evidence['checks'][0]
    else:evidence.update(retrieval='hybrid',hybrid={'epoch':1,'ready':True})
    with pytest.raises(seed.runtime.DemoError):verify(evidence)


def test_seed_ci_pins_and_report_gates():
    import re
    import subprocess
    import yaml
    source=(ROOT/'.github/workflows/demo-seed.yml').read_text()
    workflow=yaml.load(source,Loader=yaml.BaseLoader)
    assert workflow['permissions']=={'contents':'read'}
    assert set(workflow['on'])=={'pull_request','workflow_dispatch'}
    job=workflow['jobs']['seed-import']
    assert job['strategy']['matrix']['retrieval']==['bm25','hybrid']
    for step in job['steps']:
        assert 'continue-on-error' not in step
        if 'uses' in step:assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}',step['uses'])
        if 'run' in step:
            command=re.sub(r'\$\{\{.*?\}\}','bm25',step['run'])
            assert subprocess.run(['bash','-n'],input=command,text=True,capture_output=True).returncode==0
        if step.get('uses','').startswith('actions/upload-artifact'):
            for forbidden in ('accounts.json','imports/','logs/','**/*'):
                assert forbidden not in step['with']['path']
    assert '--pattern "**/TEST-*DemoImportTest.xml"' in source
    assert '--pattern "**/TEST-*DemoImportAccessTest.xml"' in source
    assert '|| true' not in source and 'secrets.' not in source
