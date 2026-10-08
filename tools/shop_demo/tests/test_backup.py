"""Real crypto/archive/CLI safety checks; Docker lifecycle is an explicit test double."""
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import tarfile
import pytest

from tools.shop_demo import backup as b, backup_crypto as c, backup_volume as v, runtime, state as ds


@pytest.fixture
def private(tmp_path):
    p = tmp_path / 'vault'; p.mkdir(mode=0o700)
    return p


def test_keygen_is_private_and_non_overwriting(private):
    key = private / 'key'
    c.keygen(key)
    assert len(c.read_key(key)) == 32
    assert key.stat().st_mode & 0o777 == 0o600
    before = key.read_bytes()
    with pytest.raises(FileExistsError): c.keygen(key)
    assert key.read_bytes() == before


def test_crypto_roundtrip_streams_and_does_not_expose_secrets(private, capsys):
    payload = private / 'data'; payload.write_bytes(b'secret\0' * 300000)
    key = os.urandom(32); encrypted = private / 'backup.enc'
    c.encrypt(payload, encrypted, key)
    assert b'secret' not in encrypted.read_bytes()
    with c.decrypt(encrypted, key, private) as decrypted:
        assert decrypted.read_bytes() == payload.read_bytes()
        assert decrypted.stat().st_mode & 0o777 == 0o600
    assert not decrypted.exists()
    assert not capsys.readouterr().out


@pytest.mark.parametrize('damage', ['wrong-key', 'header', 'middle', 'tag', 'truncated', 'extra'])
def test_corrupt_backup_never_yields_plaintext(private, damage):
    data = private / 'data'; data.write_bytes(b'important-data' * 20)
    archive = private / 'data.enc'; key = os.urandom(32); c.encrypt(data, archive, key)
    content = bytearray(archive.read_bytes())
    if damage == 'wrong-key': key = os.urandom(32)
    elif damage == 'truncated': content = content[:25]
    elif damage == 'extra': content.extend(b'bad')
    else:
        i = {'header': 0, 'middle': len(content)//2, 'tag': -1}[damage]; content[i] ^= 1
    archive.write_bytes(content)
    with pytest.raises(ValueError):
        with c.decrypt(archive, key, private):
            pytest.fail('unauthenticated bytes exposed')
    assert not list(private.glob('.decrypted-*'))


def test_decrypt_cleanup_when_consumer_fails(private):
    data = private / 'data'; data.write_bytes(b'synthetic'); key = os.urandom(32)
    archive = private / 'data.enc'; c.encrypt(data, archive, key)
    with pytest.raises(RuntimeError):
        with c.decrypt(archive, key, private): raise RuntimeError('consumer')
    assert not list(private.glob('.decrypted-*'))


@pytest.mark.parametrize('case', ['public-key', 'symlink-key', 'hardlink-key', 'bad-size', 'public-dir'])
def test_key_path_safety(private, case):
    key = private / 'key'; c.keygen(key)
    if case == 'public-key': key.chmod(0o644)
    if case == 'symlink-key': other = private / 'sym'; other.symlink_to(key); key = other
    if case == 'hardlink-key': os.link(key, private / 'other')
    if case == 'bad-size': key.write_bytes(b'guessable-password')
    if case == 'public-dir':
        private.chmod(0o755)
        with pytest.raises(ValueError): c.keygen(private / 'new')
    else:
        with pytest.raises(ValueError): c.read_key(key)


def test_encrypt_existing_target_unchanged(private):
    original = private / 'original'; original.write_bytes(b'abc')
    target = private / 'exists'; target.write_bytes(b'keep')
    with pytest.raises(FileExistsError): c.encrypt(original, target, os.urandom(32))
    assert target.read_bytes() == b'keep'


def test_volume_roundtrip_actual_bytes_modes_and_ids(private):
    source = private / 'volume'; source.mkdir(mode=0o750)
    (source / '.hidden').write_bytes(b'cookie-placeholder')
    (source / '.hidden').chmod(0o600)
    (source / 'dir').mkdir(); (source / 'dir/data').write_bytes(b'\0binary\xff')
    archive = private / 'volume.tgz'; digest = v.pack(source, archive, 'rabbit_data')
    target = private / 'new'; target.mkdir()
    assert v.unpack(archive, target) == digest
    assert (target / 'dir/data').read_bytes() == b'\0binary\xff'
    assert target.stat().st_mode & 0o777 == 0o750
    assert (target / '.hidden').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError): v.unpack(archive, target)


def test_mysql_socket_link_is_only_named_transient_exclusion(private):
    source = private / 'db'; source.mkdir(); (source / 'dbfile').write_text('data')
    (source / 'mysql.sock').symlink_to('/var/run/mysqld/mysqld.sock')
    archive = private / 'db.tgz'; v.pack(source, archive, 'mysql_data')
    assert {row['name'] for row in v.inventory(archive)[0]} == {'.', 'dbfile'}
    with pytest.raises(ValueError): v.pack(source, private / 'bad.tgz', 'agent_data')


def make_tar(path, names):
    with tarfile.open(path, 'w:gz') as tar:
        root = tarfile.TarInfo('.'); root.type = tarfile.DIRTYPE; tar.addfile(root)
        for name, kind in names:
            item = tarfile.TarInfo(name); item.type = kind
            item.uid = os.getuid(); item.gid = os.getgid(); item.size = 0
            tar.addfile(item, io.BytesIO())


@pytest.mark.parametrize('name,kind', [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),
    ('back\\slash',tarfile.REGTYPE),('a/../b',tarfile.REGTYPE),('a//b',tarfile.REGTYPE),
    ('a',tarfile.SYMTYPE),('a',tarfile.LNKTYPE),('a',tarfile.CHRTYPE),('a',tarfile.FIFOTYPE)])
def test_hostile_volume_archive_rejected_before_write(private, name, kind):
    archive=private/'bad.tgz';make_tar(archive,[(name,kind)])
    target=private/'new';target.mkdir()
    with pytest.raises(ValueError):v.unpack(archive,target)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('names', [ [('a',tarfile.REGTYPE),('a',tarfile.REGTYPE)],
  [('a',tarfile.REGTYPE),('a/b',tarfile.REGTYPE)], [('a/b',tarfile.REGTYPE),('a',tarfile.REGTYPE)] ])
def test_archive_duplicates_and_file_parent_rejected(private,names):
    archive=private/'bad.tgz';make_tar(archive,names)
    with pytest.raises(ValueError):v.inventory(archive)


def test_archive_size_bound(private,monkeypatch):
    source=private/'v';source.mkdir();(source/'large').write_bytes(b'x'*200)
    monkeypatch.setattr(v,'MAX_BYTES',100)
    with pytest.raises(ValueError):v.pack(source,private/'data.tgz','agent_data')


@pytest.fixture
def demo(checkout,options,monkeypatch):
    state=checkout/'.local/source';owner=ds.init(checkout,state,options)
    monkeypatch.setattr(b,'ROOT',checkout);monkeypatch.setattr(runtime,'ROOT',checkout)
    monkeypatch.setattr(b,'contract',lambda:'c'*64)
    spec=ds.layout(state,owner)
    image='sha256:'+'a'*64
    records={'volume':[{'Name':owner['project']+'_'+n,'CreatedAt':'test-created'} for n in sorted(b.VOLUMES)],
             'network':[], 'container':[{'Id':name,'Image':image,'Config':{'Hostname':'original-rabbit-node','Labels':{'com.docker.compose.service':name}},
                           'State':{'Status':'exited','Running':False,'ExitCode':0}} for name in spec['services']]}
    ds._atomic(state/'lifecycle.json',{'phase':'stopped','volumes':runtime.volume_identity(records['volume'])})
    monkeypatch.setattr(runtime,'resources',lambda *_:copy.deepcopy(records))
    monkeypatch.setattr(runtime,'command',lambda *_,**__: '')
    monkeypatch.setattr(b,'engine',lambda _: 'test-engine')
    return state,owner,spec,records


def test_stopped_volume_gate_accepts_only_stopped_complete_source(demo):
    state,owner,spec,records=demo
    assert b.stopped(state,owner,spec)==records


@pytest.mark.parametrize('fault',['running','oom','bad-exit','missing-service','duplicate-service','missing-volume','hybrid','model-egress','pending'])
def test_source_backup_gate_refuses_unsafe_state(demo,fault):
    state,owner,spec,records=demo
    if fault=='running':records['container'][0]['State']['Running']=True
    elif fault=='oom':records['container'][0]['State']['OOMKilled']=True
    elif fault=='bad-exit':next(x for x in records['container'] if x['Id']=='mysql')['State']['ExitCode']=137
    elif fault=='missing-service':records['container'].pop()
    elif fault=='duplicate-service':records['container'].append(records['container'][0])
    elif fault=='missing-volume':records['volume'].pop()
    elif fault=='hybrid':owner['options']['retrieval']='hybrid'
    elif fault=='model-egress':owner['options']['model_network']=True
    elif fault=='pending':ds._atomic(state/'lifecycle.json',{'phase':'stopped','volumes':None,'recovery_pending':True})
    with pytest.raises(ValueError):b.stopped(state,owner,spec)


def test_foreign_volume_user_rejected(demo,monkeypatch):
    state,owner,spec,_=demo
    monkeypatch.setattr(runtime,'command',lambda *_,**__: 'foreign-container')
    with pytest.raises(ValueError,match='foreign'):b.stopped(state,owner,spec)


def test_recovery_layout_pins_images_and_rabbit_name(demo):
    state,owner,spec,records=demo
    owner['recovery']={'images':{n:'sha256:'+'a'*64 for n in spec['services']},'rabbit_hostname':'original-rabbit-node'}
    result=ds.layout(state,owner)
    assert result['services']['rabbitmq']['hostname']=='original-rabbit-node'
    assert all('build' not in s and s['pull_policy']=='never' for s in result['services'].values())
    assert result['services']['web']['ports'][0]['host_ip']=='127.0.0.1'


@pytest.mark.parametrize('fault',['tag','service','hostname','extra'])
def test_invalid_recovery_configuration_refused(demo,fault):
    state,owner,spec,_=demo
    owner['recovery']={'images':{n:'sha256:'+'a'*64 for n in spec['services']},'rabbit_hostname':'source'}
    if fault=='tag':owner['recovery']['images']['agent']='python:latest'
    if fault=='service':owner['recovery']['images'].pop('agent')
    if fault=='hostname':owner['recovery']['rabbit_hostname']='bad/name'
    if fault=='extra':owner['recovery']['other']='network'
    with pytest.raises(ValueError):ds.layout(state,owner)


def test_partial_restore_cannot_be_started(demo):
    state,owner,spec,_=demo
    ds._atomic(state/'lifecycle.json',{'phase':'starting','volumes':None,'recovery_pending':True})
    with pytest.raises(ValueError,match='incomplete restore'):runtime.up(state)


def test_restore_never_overwrites_existing_state(demo,private):
    state,_,_,_=demo
    before=(state/'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):b.restore(private/'x',private/'key',state,18031,'bad')
    assert (state/'manifest.json').read_bytes()==before


def test_full_archive_roundtrip_with_fake_docker_only(demo,monkeypatch):
    state,owner,spec,records=demo
    vault=state.parent/'vault';vault.mkdir(mode=0o700)
    key=vault/'key';c.keygen(key)
    source=state.parent/'fake-volume';source.mkdir();(source/'db').write_bytes(b'unit-data-not-live-db')
    def helper(state,folder,image,volume,actual,action):
        if action=='pack':digest=v.pack(source,folder/(volume+'.tgz'),volume)
        else:digest=v.inventory(folder/(volume+'.tgz'))[1]
        return {'verified':True,'inventory_sha256':digest}
    monkeypatch.setattr(b,'helper',helper)
    archive=vault/'backup.enc'
    created=b.backup(state,archive,key)
    assert created['source_still_stopped'] and created['encrypted']
    checked=b.inspect(archive,key)
    assert checked['verified'] and checked['archive_sha256']==created['archive_sha256']
    with b.opened(archive,key) as (_,payload,_):
        assert (payload/'snapshot/runtime/apps/web/dist/index.html').stat().st_mode & 0o777 == 0o644
    assert not list(vault.glob('.cold-*'))
    assert runtime._life(state)['phase']=='stopped'
    assert ds.load(state.parents[1],state)[0] == owner


def test_restore_to_new_namespace_copies_volumes_and_keeps_source(demo,monkeypatch):
    state,owner,spec,records=demo
    vault=state.parent/'vault';vault.mkdir(mode=0o700);key=vault/'key';c.keygen(key)
    raw=state.parent/'data';raw.mkdir();(raw/'fixture').write_text('not-live-service-data')
    written={}; calls=[]
    def helper(state,folder,image,volume,actual,action):
        calls.append((action,actual))
        if action=='pack':digest=v.pack(raw,folder/(volume+'.tgz'),volume)
        elif action=='unpack':
            dest=state.parent/('volume-'+volume);dest.mkdir();digest=v.unpack(folder/(volume+'.tgz'),dest)
            written[volume]=(dest/'fixture').read_text()
        else:digest=v.inventory(folder/(volume+'.tgz'))[1]
        return {'verified':True,'inventory_sha256':digest}
    monkeypatch.setattr(b,'helper',helper)
    archive=vault/'archive.enc';created=b.backup(state,archive,key)
    before={n:ds.sha(state/n) for n in ('owner.json','manifest.json','.env','agent-key','lifecycle.json')}
    target=state.parent/'restored';created_target=[]
    def resources(st,project):
        if project==owner['project']:return copy.deepcopy(records)
        if not created_target:return {'volume':[],'network':[],'container':[]}
        return {'volume':[{'Name':project+'_'+n,'CreatedAt':'new-test'} for n in b.VOLUMES],
                'network':[], 'container':[{'State':{'Running':False}}]}
    def command(st,stage,args,**kw):
        if stage=='restore-create-without-start':created_target.append(True)
        return '[]'
    monkeypatch.setattr(runtime,'resources',resources);monkeypatch.setattr(runtime,'command',command)
    result=b.restore(archive,key,target,18031,created['archive_sha256'])
    assert result['project']!=owner['project'] and result['target_still_stopped']
    assert set(written)==b.VOLUMES and len({n for a,n in calls if a=='unpack'})==5
    assert all(not n.startswith(owner['project']) for a,n in calls if a=='unpack')
    new,spec2=ds.load(state.parents[1],target)
    assert spec2['services']['rabbitmq']['hostname']=='original-rabbit-node'
    assert (target/'agent-key').read_bytes()==(state/'agent-key').read_bytes()
    assert (target/'runtime/apps/web/dist/index.html').stat().st_mode & 0o777==0o644
    assert runtime._life(target)['phase']=='stopped' and not runtime._life(target)['recovery_pending']
    assert before=={n:ds.sha(state/n) for n in before}


def test_helper_network_and_readonly_scope(demo,monkeypatch):
    state,owner,spec,_=demo;captured=[]
    def command(st,stage,args,**kw):
        captured.append(args)
        return json.dumps({'verified':True,'inventory_sha256':'a'*64}) if stage.startswith('cold-') else ''
    monkeypatch.setattr(runtime,'command',command)
    b.helper(state,state,'sha256:'+'a'*64,'mysql_data',owner['project']+'_mysql_data','pack')
    cmd=captured[0]
    assert ['--network','none'] == cmd[cmd.index('--network'):cmd.index('--network')+2]
    assert '--pull=never' in cmd and '--read-only' in cmd
    assert any('dst=/volume,volume-nocopy,readonly' in x for x in cmd)
    assert not any('docker.sock' in x for x in cmd)


def test_running_source_refused_before_any_volume_copy(demo,monkeypatch):
    state,owner,spec,records=demo
    vault=state.parent/'vault';vault.mkdir(mode=0o700);key=vault/'key';c.keygen(key)
    records['container'][0]['State']['Running']=True
    monkeypatch.setattr(b,'helper',lambda *_:pytest.fail('volume should not be opened'))
    with pytest.raises(ValueError):b.backup(state,vault/'b.enc',key)
    assert not (vault/'b.enc').exists()


def test_wrong_confirmation_refused_before_decryption_or_target(demo,monkeypatch):
    state,_,_,_=demo;vault=state.parent/'vault';vault.mkdir(mode=0o700)
    archive=vault/'b.enc';archive.write_text('fake');archive.chmod(0o600)
    target=state.parent/'new'
    monkeypatch.setattr(b,'opened',lambda *_:pytest.fail('no decrypt on unconfirmed restore'))
    with pytest.raises(ValueError,match='confirmation'):b.restore(archive,vault/'key',target,18031,'0'*64)
    assert not target.exists()


def test_restore_cannot_be_nested_in_original_demo(demo,private):
    state,_,_,_=demo
    with pytest.raises(ValueError,match='nested'):
        b.restore(private/'fake',private/'key',state/'nested',18032,'bad')
    assert not (state/'nested').exists()


def test_failed_volume_copy_keeps_target_unstartable(demo,monkeypatch):
    state,owner,spec,records=demo
    vault=state.parent/'vault';vault.mkdir(mode=0o700);key=vault/'key';c.keygen(key)
    raw=state.parent/'data';raw.mkdir();(raw/'db').write_text('synthetic')
    def helper(st,folder,image,volume,actual,action):
        if action=='pack':digest=v.pack(raw,folder/(volume+'.tgz'),volume)
        elif action=='unpack':raise ValueError('synthetic copy failure')
        else:digest=v.inventory(folder/(volume+'.tgz'))[1]
        return {'verified':True,'inventory_sha256':digest}
    monkeypatch.setattr(b,'helper',helper)
    archive=vault/'archive.enc';created=b.backup(state,archive,key)
    provisioned=[]
    def resources(st,project):
        if project==owner['project']:return records
        return {'volume':[{'Name':project+'_'+n,'CreatedAt':'unit'} for n in b.VOLUMES] if provisioned else [],'network':[],'container':[]}
    monkeypatch.setattr(runtime,'resources',resources)
    monkeypatch.setattr(runtime,'command',lambda st,stage,*args,**kwargs:provisioned.append(True) if stage=='restore-create-without-start' else '')
    target=state.parent/'partial'
    with pytest.raises(ValueError,match='copy failure'):b.restore(archive,key,target,18031,created['archive_sha256'])
    assert runtime._life(target)['recovery_pending']
    with pytest.raises(ValueError,match='incomplete restore'):runtime.up(target)
    assert runtime._life(state)['phase']=='stopped'


def test_recovery_smoke_requires_every_named_check():
    from tools.shop_demo.backup_smoke import CHECKS,verify
    report={'checks':list(CHECKS),'volume_count':5,'database':{'products':100,'skus':100,'published':96,'drafts':0,'stock':179,'customerClauses':241,'staffClauses':48},'live_llm_called':False,'public_deployment':False}
    verify(report)
    for name in CHECKS:
        broken=copy.deepcopy(report);broken['checks'].remove(name)
        with pytest.raises(runtime.DemoError):verify(broken)
    broken=copy.deepcopy(report);broken['checks'].append(broken['checks'][0])
    with pytest.raises(runtime.DemoError):verify(broken)


def test_recovery_workflow_never_uploads_private_backup_or_credentials():
    import re,yaml
    root=Path(__file__).resolve().parents[3]
    workflow=yaml.load((root/'.github/workflows/demo-recovery.yml').read_text(),Loader=yaml.BaseLoader)
    assert workflow['permissions']=={'contents':'read'}
    assert set(workflow['on'])=={'pull_request','workflow_dispatch'}
    job=workflow['jobs']['cold-recovery']
    assert 'continue-on-error' not in job
    for step in job['steps']:
        assert 'continue-on-error' not in step
        if 'uses' in step:assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}',step['uses'])
        if step.get('uses','').startswith('actions/upload-artifact'):
            assert set(step['with']['path'].split())=={'.local/recovery-smoke/artifacts/evidence.json','.local/demo-unit/TEST-demo.xml'}
            assert step['with']['if-no-files-found']=='error'
    assert any('python -m tools.shop_demo.backup_smoke'==step.get('run') for step in job['steps'])
