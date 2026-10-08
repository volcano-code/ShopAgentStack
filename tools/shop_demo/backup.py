"""M1.4c1: encrypted cold BM25 demo backup; restore only to NEW, stopped resources."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tarfile
import tempfile

from . import backup_crypto as crypto, runtime, state as ds
from .backup_volume import name_ok, MAX_BYTES, MAX_ENTRIES

ROOT = ds.ROOT
FORMAT = 'shop-demo-cold-bm25-v1'
VOLUMES = {'mysql_data', 'agent_data', 'rabbit_data', 'redis_data', 'mongo_data'}
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
HOST = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}\Z')
CONTRACT = ('tools/shop_demo/state.py', 'tools/shop_e2e/stack.py',
            'tools/shop_e2e/hybrid_stack.py', 'tools/shop_demo/backup.py',
            'tools/shop_demo/backup_volume.py')


def contract() -> str:
    return hashlib.sha256(json.dumps({n: ds.sha(ROOT / n) for n in CONTRACT}, sort_keys=True).encode()).hexdigest()


def path_in_private_area(path: Path) -> Path:
    path = ds.validate_state(ROOT, path.absolute())
    if path.resolve() == ROOT.resolve() / '.local':
        raise ValueError('select a dedicated recovery file or target directory')
    return path


def engine(state: Path) -> str:
    runtime.local_engine(state)
    value = runtime.command(state, 'backup-engine-id', [*runtime.DOCKER, 'info', '--format', '{{.ID}}'], timeout=30).strip()
    if not value:
        raise ValueError('local engine identity unavailable')
    return value


def stopped(state: Path, owner: dict, spec: dict) -> dict:
    life = runtime._life(state)
    if life['phase'] != 'stopped' or life.get('recovery_pending'):
        raise ValueError('explicitly stop the source demo before backup')
    if owner['options']['retrieval'] != 'bm25' or owner['options']['model_network']:
        raise ValueError('v1 cold backup requires BM25 and model egress disabled')
    if set(spec['volumes']) != VOLUMES:
        raise ValueError('unsupported backup volume inventory')
    current = runtime.resources(state, owner['project'])
    runtime.require_volumes(life, current)
    if set(life.get('volumes') or {}) != {owner['project'] + '_' + n for n in VOLUMES}:
        raise ValueError('source volumes incomplete')
    names = [v['Config']['Labels']['com.docker.compose.service'] for v in current['container']]
    if set(names) != set(spec['services']) or len(names) != len(set(names)):
        raise ValueError('source container inventory incomplete')
    for item in current['container']:
        status = item['State']
        if status['Status'] != 'exited' or status.get('Running') or status.get('OOMKilled'):
            raise ValueError('all source services must be stopped, without OOM')
        service = item['Config']['Labels']['com.docker.compose.service']
        if service in ('mysql', 'rabbitmq', 'mongo', 'redis') and status.get('ExitCode') != 0:
            raise ValueError('database did not shut down cleanly')
    allowed = {item['Id'] for item in current['container']}
    for volume in sorted(VOLUMES):
        holders = runtime.command(state, 'check-volume-users', [*runtime.DOCKER, 'ps', '-aq', '--no-trunc',
                    '--filter', 'volume=' + owner['project'] + '_' + volume], timeout=30).split()
        if not set(holders) <= allowed:
            raise ValueError('foreign container mounts source data')
    return current


def helper(state: Path, folder: Path, image: str, volume: str, actual: str | None, action: str) -> dict:
    if not IMAGE.fullmatch(image) or volume not in VOLUMES or action not in ('pack', 'unpack', 'verify'):
        raise ValueError('invalid cold helper request')
    for p in (ROOT / 'tools/shop_demo/backup_volume.py', folder):
        if ',' in str(p):
            raise ValueError('commas in Docker mount paths are unsupported')
    name = 'shop-recovery-helper-' + secrets.token_hex(12)
    command = [*runtime.DOCKER, 'run', '--name', name, '--rm', '--pull=never', '--network', 'none',
               '--read-only', '--label', 'io.shopagentstack.recovery-helper=' + name, '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'DAC_OVERRIDE',
               '--cap-add', 'CHOWN', '--cap-add', 'FOWNER', '--security-opt', 'no-new-privileges:true',
               '--mount', 'type=bind,src=' + str(ROOT / 'tools/shop_demo/backup_volume.py') + ',dst=/helper.py,readonly',
               '--mount', 'type=bind,src=' + str(folder) + ',dst=/transfer' + (',readonly' if action != 'pack' else '')]
    if actual is not None:
        if not re.fullmatch(r'shop-demo-[0-9a-f]{32}_[a-z_]+', actual):
            raise ValueError('invalid recovery volume name')
        command += ['--mount', 'type=volume,src=' + actual + ',dst=/volume,volume-nocopy' + (',readonly' if action == 'pack' else '')]
    command += ['--entrypoint', 'python', image, '/helper.py', action, volume]
    try:
        result = json.loads(runtime.command(state, 'cold-' + action + '-' + volume, command, timeout=600))
    finally:
        # A timeout of docker CLI does not imply helper termination. Remove only this nonce name.
        remaining = runtime.command(state, 'recovery-helper-inspect', [*runtime.DOCKER, 'ps', '-aq',
                    '--filter', 'label=io.shopagentstack.recovery-helper=' + name], timeout=30).split()
        if remaining:
            runtime.command(state, 'recovery-helper-cleanup', [*runtime.DOCKER, 'rm', '-f', *remaining], timeout=30)
    if result.get('verified') is not True or not re.fullmatch(r'[0-9a-f]{64}', result.get('inventory_sha256', '')):
        raise ValueError('cold helper verification missing')
    return result


def _copy(source: Path, destination: Path):
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with destination.open('xb') as stream, source.open('rb') as original:
        shutil.copyfileobj(original, stream, 1024 * 1024)
    destination.chmod(source.stat().st_mode & 0o777)


def backup(state: Path, destination: Path, key_file: Path) -> dict:
    destination, key_file = path_in_private_area(destination), path_in_private_area(key_file)
    key = crypto.read_key(key_file)
    crypto.private_parent(destination)
    state = ds.validate_state(ROOT, state)
    if destination.is_relative_to(state) or key_file == destination:
        raise ValueError('store backups separately from source demo state')
    with ds.locked(ROOT, state):
        owner, spec = ds.load(ROOT, state)
        host_id = engine(state)
        current = stopped(state, owner, spec)
        images = {v['Config']['Labels']['com.docker.compose.service']: v['Image'] for v in current['container']}
        rabbit = next(v for v in current['container'] if v['Config']['Labels']['com.docker.compose.service'] == 'rabbitmq')
        hostname = rabbit['Config']['Hostname']
        if not HOST.fullmatch(hostname) or not all(IMAGE.fullmatch(v) for v in images.values()):
            raise ValueError('image or RabbitMQ identity missing')
        with tempfile.TemporaryDirectory(prefix='.cold-backup-', dir=destination.parent) as directory:
            stage = Path(directory)
            payload = stage / 'payload'
            payload.mkdir(mode=0o700)
            volume_dir = payload / 'volumes'
            volume_dir.mkdir(mode=0o700)
            files = json.loads((state / 'manifest.json').read_text())['files']
            for name in [*files, 'manifest.json']:
                _copy(state / name, payload / 'snapshot' / name)
            receipts = {}
            for volume in sorted(VOLUMES):
                receipts[volume] = helper(state, volume_dir, images['agent'], volume, owner['project'] + '_' + volume, 'pack')
                stopped(state, owner, spec)
            paths = sorted(p for p in payload.rglob('*') if p.is_file())
            metadata = {'format': FORMAT, 'id': secrets.token_hex(16), 'created_at': datetime.now(timezone.utc).isoformat(),
                        'source_project': owner['project'], 'source_state': str(state), 'engine_id': host_id,
                        'contract': contract(), 'images': images, 'rabbit_hostname': hostname, 'volumes': receipts,
                        'files': {str(p.relative_to(payload)): {'sha256': ds.sha(p), 'size': p.stat().st_size, 'mode': p.stat().st_mode & 0o777} for p in paths}}
            ds.write_private(payload / 'backup.json', json.dumps(metadata, sort_keys=True))
            with tarfile.open(stage / 'payload.tar', 'x') as tar:
                for path in sorted(p for p in payload.rglob('*') if p.is_file()):
                    tar.add(path, arcname=str(path.relative_to(payload)), recursive=False)
            crypto.encrypt(stage / 'payload.tar', destination, key)
    return {'backup_created': True, 'archive': str(destination), 'archive_sha256': ds.sha(destination),
            'format': FORMAT, 'source_still_stopped': True, 'encrypted': True, 'volume_count': len(VOLUMES)}


@contextmanager
def opened(archive: Path, key_file: Path):
    archive, key_file = path_in_private_area(archive), path_in_private_area(key_file)
    with tempfile.TemporaryDirectory(prefix='.cold-inspect-', dir=archive.parent) as directory:
        stage = Path(directory)
        with crypto.decrypt(archive, crypto.read_key(key_file), stage) as plain:
            payload = stage / 'payload'
            payload.mkdir(mode=0o700)
            seen, total = set(), 0
            with tarfile.open(plain, 'r:') as tar:
                for item in tar:
                    if (not item.isfile() or not name_ok(item.name) or item.name == '.' or item.name in seen
                            or item.mode & 0o7000 or len(seen) >= MAX_ENTRIES):
                        raise ValueError('invalid outer backup archive')
                    seen.add(item.name)
                    total += item.size
                    if item.size < 0 or total > MAX_BYTES:
                        raise ValueError('backup expanded size limit')
                    target = payload / item.name
                    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    with target.open('xb') as out, tar.extractfile(item) as source:
                        shutil.copyfileobj(source, out, 1024 * 1024)
                    target.chmod(item.mode & 0o777)
            metadata = json.loads((payload / 'backup.json').read_text())
            if (not isinstance(metadata, dict) or not isinstance(metadata.get('files'), dict)
                    or not isinstance(metadata.get('images'), dict) or not isinstance(metadata.get('volumes'), dict)
                    or not isinstance(metadata.get('source_state'), str) or not isinstance(metadata.get('engine_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{32}', str(metadata.get('id', '')))):
                raise ValueError('malformed backup metadata')
            files = metadata['files']
            if (metadata.get('format') != FORMAT or metadata.get('contract') != contract() or
                    set(files) | {'backup.json'} != seen or set(metadata.get('volumes', {})) != VOLUMES):
                raise ValueError('incompatible or incomplete backup manifest')
            for name, record in files.items():
                if not name_ok(name) or (payload / name).stat().st_size != record['size'] or ((payload / name).stat().st_mode & 0o777) != record['mode'] or ds.sha(payload / name) != record['sha256']:
                    raise ValueError('backup file content mismatch')
            original = json.loads((payload / 'snapshot/owner.json').read_text())
            original_files = json.loads((payload / 'snapshot/manifest.json').read_text())['files']
            expected = {'snapshot/' + n for n in original_files} | {'snapshot/manifest.json'} | {'volumes/' + v + '.tgz' for v in VOLUMES}
            if (set(files) != expected or original['schema'] != ds.SCHEMA or original['project'] != metadata['source_project']
                    or original['options']['retrieval'] != 'bm25' or original['options']['model_network']):
                raise ValueError('unsupported original snapshot')
            for name, digest in original_files.items():
                if not name_ok(name) or ds.sha(payload / 'snapshot' / name) != digest:
                    raise ValueError('original runtime snapshot changed')
            spec = ds.layout(Path(metadata['source_state']), original)
            if json.loads((payload / 'snapshot/compose.json').read_text()) != spec:
                raise ValueError('original layout cannot be reproduced')
            if set(metadata['images']) != set(spec['services']) or not all(IMAGE.fullmatch(v) for v in metadata['images'].values()) or not HOST.fullmatch(metadata['rabbit_hostname']):
                raise ValueError('invalid recovery image/hostname inventory')
            yield metadata, payload, original


def inspect(archive: Path, key_file: Path) -> dict:
    with opened(archive, key_file) as (meta, _, original):
        return {'verified': True, 'archive_sha256': ds.sha(archive), 'format': FORMAT, 'backup_id': meta['id'],
                'source_project': meta['source_project'], 'retrieval': original['options']['retrieval'],
                'volume_count': len(meta['volumes']), 'restore_requires_new_state': True,
                'warning': 'Trusted local backup only: contains executable runtime and private credentials'}


def restore(archive: Path, key_file: Path, target: Path, port: int, confirmation: str) -> dict:
    target = path_in_private_area(target)
    if target.exists():
        raise FileExistsError('restore never overwrites an existing target')
    for parent in target.parents:
        if parent == ROOT.resolve() / '.local':
            break
        if (parent / 'owner.json').exists() or (parent / 'manifest.json').exists():
            raise ValueError('restore target must not be nested in an existing demo')
    archive = path_in_private_area(archive)
    crypto.private_file(archive)
    if confirmation != ds.sha(archive):
        raise ValueError('restore requires full archive SHA256 confirmation')
    if target.is_relative_to(archive.parent) or archive.is_relative_to(target):
        raise ValueError('restore target must be separate from backup storage')
    with opened(archive, key_file) as (meta, payload, original):
        options = {**original['options'], 'port': port}
        ds.validate_options(options)
        if port == original['options']['port']:
            raise ValueError('restore requires a different loopback port')
        # No target changes until authenticated archive, hashes and code compatibility pass.
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
        (target / 'logs').mkdir(mode=0o700)
        if engine(target) != meta['engine_id']:
            raise ValueError('v1 recovery requires the same local Docker engine and exact images')
        for image in sorted(set(meta['images'].values())):
            runtime.command(target, 'require-original-image', [*runtime.DOCKER, 'image', 'inspect', image], timeout=30)
        for volume in sorted(VOLUMES):
            result = helper(target, payload / 'volumes', meta['images']['agent'], volume, None, 'verify')
            if result != meta['volumes'][volume]:
                raise ValueError('volume verification mismatch')
        project = 'shop-demo-' + secrets.token_hex(16)
        if any(runtime.resources(target, project).values()):
            raise ValueError('new recovery namespace collision')
        for name in json.loads((payload / 'snapshot/manifest.json').read_text())['files']:
            if name not in ('owner.json', 'compose.json'):
                _copy(payload / 'snapshot' / name, target / name)
        (target / 'agent-key').chmod(0o644)
        owner = {**original, 'root': str(ROOT.resolve()), 'project': project, 'options': options,
                 'recovery': {'images': meta['images'], 'rabbit_hostname': meta['rabbit_hostname']}}
        ds.write_private(target / 'owner.json', json.dumps(owner, indent=2))
        spec = ds.layout(target, owner)
        ds.write_private(target / 'compose.json', json.dumps(spec, indent=2))
        bound = [p for p in target.rglob('*') if p.is_file() and not p.is_relative_to(target / 'logs')]
        ds.write_private(target / 'manifest.json', json.dumps({'schema': ds.SCHEMA, 'files': {str(p.relative_to(target)): ds.sha(p) for p in bound}}))
        ds._atomic(target / 'lifecycle.json', {'phase': 'starting', 'volumes': None, 'recovery_pending': True})
        with ds.locked(ROOT, target):
            cli = runtime.base(target, project)
            runtime.command(target, 'restore-create-without-start', [*cli, 'create', '--no-build', '--pull', 'never'], timeout=600)
            current = runtime.resources(target, project)
            ids = runtime.volume_identity(current['volume'])
            if set(ids) != {project + '_' + n for n in VOLUMES} or any(v['State'].get('Running') for v in current['container']):
                raise ValueError('target provisioning not empty/stopped and complete')
            ds._atomic(target / 'lifecycle.json', {'phase': 'starting', 'volumes': ids, 'recovery_pending': True})
            for volume in sorted(VOLUMES):
                result = helper(target, payload / 'volumes', meta['images']['agent'], volume, project + '_' + volume, 'unpack')
                if result != meta['volumes'][volume]:
                    raise ValueError('restored bytes do not match source')
            runtime.require_volumes({'volumes': ids}, runtime.resources(target, project))
            ds._atomic(target / 'lifecycle.json', {'phase': 'stopped', 'volumes': ids, 'recovery_pending': False})
        return {'restored': True, 'project': project, 'state': str(target), 'target_still_stopped': True,
                'source_not_modified': True, 'archive_sha256': confirmation, 'volume_count': len(VOLUMES),
                'application_verified': False, 'next': 'python -m tools.shop_demo up --state ' + str(target)}
