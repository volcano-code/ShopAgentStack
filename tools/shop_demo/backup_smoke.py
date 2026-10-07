"""Real recovery rehearsal in fresh synthetic BM25 demos; never accepts a user's source."""
from __future__ import annotations
import json
from pathlib import Path
import secrets
import socket
import subprocess
from uuid import uuid4

from . import backup, backup_crypto as crypto, runtime, seed
from .seed_smoke import readback
from .smoke import agent, business, expect, failure_locations
from .state import ROOT, init, load, sha, write_private

CHECKS = {'running-source-refused', 'authenticated-backup', 'wrong-key-refused',
          'wrong-confirmation-refused', 'existing-target-refused', 'all-volumes-byte-verified',
          'source-stopped-and-unchanged', 'exact-images-and-broker-identity',
          'logins-restored', 'catalog-policies-stock-restored', 'cart-session-preferences-restored',
          'encrypted-provider-decrypts', 'restored-restart', 'source-restart-unchanged'}


def verify(report):
    expect(set(report.get('checks', [])) == CHECKS and len(report['checks']) == len(CHECKS), 'missing/duplicate recovery checks')
    expect(report.get('volume_count') == 5, 'incomplete physical backup')
    data = report['database']
    expect(data['products'] == data['skus'] == 100 and data['published'] == 96 and data['drafts'] == 0,
           'restored catalog/policy state incomplete')
    expect(data['stock'] == 179 and data['customerClauses'] == 241 and data['staffClauses'] == 48,
           'restored data values incorrect')
    expect(report.get('live_llm_called') is False and report.get('public_deployment') is False, 'scope mismatch')


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


def run():
    base = ROOT / '.local/recovery-smoke'
    base.mkdir(parents=True, mode=0o700, exist_ok=False)
    source, target, vault = base/'source', base/'target', base/'vault'
    vault.mkdir(mode=0o700); (base/'artifacts').mkdir(mode=0o700)
    key, archive = vault/'recovery.key', vault/'backup.enc'
    owners = {}
    report = {'scope': 'M1.4c1-cold-bm25-same-engine-recovery', 'status': 'failed', 'checks': [],
              'backup_restore_verified': False, 'cleanup_verified': False, 'live_llm_called': False,
              'public_deployment': False, 'hybrid_recovery_verified': False}
    code = 1
    try:
        report['checkout_sha'], report['tree_sha'] = subprocess.check_output(['git','rev-parse','HEAD','HEAD^{tree}'],cwd=ROOT,text=True).split()
        expect(not subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=ROOT,text=True).strip(), 'dirty recovery source')
        owner = init(ROOT, source, {'retrieval':'bm25','port':port(),'fixture':False,'model_network':False,'seed_import':True})
        owners[source] = owner['project']
        url = runtime.up(source)['url']
        # Explicit synthetic CI confirmation; ordinary CLI still requires user review.
        plan = seed.preview(source); seed.apply(source,plan['preview_id'],plan['confirmation'])
        plan = seed.preview(source,publish=True); seed.apply(source,plan['preview_id'],plan['confirmation'])
        _, api = seed._api(source)
        business(url,'/api/admin/shop_agent_stack/catalog/10001/changes',bearer=api.token,allow_empty=True,
                 data={'requestId':str(uuid4()),'action':'STOCK','skuId':10001,'expected':180,'value':-1,'reason':'synthetic restore check'})
        user = {'username':'recover_'+uuid4().hex[:12], 'password':uuid4().hex+'Aa9!'}
        telephone = '000' + str(secrets.randbelow(10**8)).zfill(8)
        otp = business(url,'/api/portal/sso/getAuthCode?telephone='+telephone)
        business(url,'/api/portal/sso/register',data={**user,'telephone':telephone,'authCode':otp},form=True,allow_empty=True)
        login = business(url,'/api/portal/sso/login',data=user,form=True); token = login['tokenHead']+login['token']
        business(url,'/api/portal/cart/add',bearer=token,data={'productId':10001,'productSkuId':10001,'quantity':1,'price':0.01,'productCategoryId':1011})
        cart = business(url,'/api/portal/cart/list',bearer=token)
        session = agent(url,'/sessions',token,data={})
        agent(url,'/settings',token,data={'nickname':'Synthetic recovery demo','compact':True})
        agent(url,'/settings/providers/custom',token,data={'model':'not-live','base_url':'https://model.example.invalid','api_key':'synthetic-'+uuid4().hex})
        before_db = readback(source,owner['project'])
        crypto.keygen(key)
        try: backup.backup(source,archive,key)
        except ValueError: pass
        else: raise runtime.DemoError('backup accepted running source')
        expect(not archive.exists(),'refused backup still created output'); report['checks'].append('running-source-refused')
        runtime.stop(source)
        before = {n:sha(source/n) for n in ('owner.json','.env','accounts.json','agent-key','manifest.json','lifecycle.json')}
        created = backup.backup(source,archive,key)
        inspected = backup.inspect(archive,key)
        expect(created['archive_sha256']==inspected['archive_sha256'], 'backup digest disagreement')
        report['checks'].append('authenticated-backup')
        wrong = vault/'wrong.key'; crypto.keygen(wrong)
        try: backup.inspect(archive,wrong)
        except ValueError: pass
        else: raise runtime.DemoError('wrong key accepted')
        report['checks'].append('wrong-key-refused')
        try: backup.restore(archive,key,target,port(),'0'*64)
        except ValueError: pass
        else: raise runtime.DemoError('wrong confirmation accepted')
        expect(not target.exists(),'unconfirmed restore modified target');report['checks'].append('wrong-confirmation-refused')
        try: backup.restore(archive,key,source,port(),created['archive_sha256'])
        except FileExistsError: pass
        else: raise runtime.DemoError('existing source was accepted as restore target')
        report['checks'].append('existing-target-refused')
        target_port = port()
        while target_port == owner['options']['port']: target_port = port()
        restored = backup.restore(archive,key,target,target_port,created['archive_sha256'])
        owners[target] = restored['project']; report['volume_count'] = restored['volume_count']
        expect(restored['target_still_stopped'] and not restored['application_verified'], 'restore unexpectedly started services')
        report['checks'].append('all-volumes-byte-verified')
        expect(before == {n:sha(source/n) for n in before} and not runtime.status(source)['ready'],'source changed during restore')
        report['checks'].append('source-stopped-and-unchanged')
        new, spec = load(ROOT,target)
        resumed = runtime.up(target); url2 = resumed['url']
        current = runtime.resources(target,new['project'])
        expect(all(item['Image']==new['recovery']['images'][item['Config']['Labels']['com.docker.compose.service']] for item in current['container']), 'restored image identity mismatch')
        rabbit = next(item for item in current['container'] if item['Config']['Labels']['com.docker.compose.service']=='rabbitmq')
        expect(rabbit['Config']['Hostname']==new['recovery']['rabbit_hostname'], 'broker hostname changed')
        report['checks'].append('exact-images-and-broker-identity')
        for account in json.loads((source/'accounts.json').read_text()):
            business(url2,'/api/admin/admin/login',data={k:account[k] for k in ('username','password')})
        login2 = business(url2,'/api/portal/sso/login',data=user,form=True); token2=login2['tokenHead']+login2['token']
        report['checks'].append('logins-restored')
        expect(readback(target,new['project'])==before_db,'restored catalog state differs');report['database']=before_db
        report['checks'].append('catalog-policies-stock-restored')
        expect(business(url2,'/api/portal/cart/list',bearer=token2)==cart,'cart mismatch')
        expect(agent(url2,'/sessions/'+session['id'],token2)['id']==session['id'],'session mismatch')
        expect(agent(url2,'/settings',token2)['nickname']=='Synthetic recovery demo','preferences mismatch')
        report['checks'].append('cart-session-preferences-restored')
        config=agent(url2,'/settings/providers/custom',token2)
        expect(config['has_key'] and 'api_key' not in config and 'key' not in config,'key recovery failed or secret exposed')
        report['checks'].append('encrypted-provider-decrypts')
        runtime.stop(target);runtime.up(target)
        expect(readback(target,new['project'])==before_db,'restored second start changed data')
        report['checks'].append('restored-restart')
        runtime.stop(target);runtime.up(source)
        expect(readback(source,owner['project'])==before_db,'original source data changed')
        report['checks'].append('source-restart-unchanged')
        verify(report);report['status']='passed';report['backup_restore_verified']=True;code=0
    except Exception as exc:
        report['failure_type']=type(exc).__name__;report['failure_locations']=failure_locations(exc)
        if isinstance(exc,runtime.DemoError):report['failure_stage']=str(exc)
        # Private logs and backup bytes must NEVER be uploaded to the public repository.
    finally:
        clean=True
        for state in (target,source):
            if (state/'manifest.json').is_file():
                try:
                    selected,_=load(ROOT,state);runtime.destroy(state,selected['project'])
                except Exception as exc:
                    clean=False;report.setdefault('cleanup_errors',[]).append(type(exc).__name__)
        report['cleanup_verified']=clean
        if not clean:report['status']='failed';report['backup_restore_verified']=False;code=1
        write_private(base/'artifacts/evidence.json',json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
    return code


if __name__=='__main__':raise SystemExit(run())
