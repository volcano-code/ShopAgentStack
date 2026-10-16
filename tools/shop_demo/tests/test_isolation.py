"""Synthetic snapshot tests: real init/load, no running application or Docker."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from tools.shop_demo import isolation, state


def pair(root, options):
    paths = [root / '.local/demo-one', root / '.local/demo-two']
    for i, path in enumerate(paths):
        state.init(root, path, {**options, 'port': 18030 + i})
    return paths


def codes(report):
    return {row['code'] for row in report['issues']}


def contents(paths):
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode, p.stat().st_mtime_ns)
            for directory in paths for p in directory.rglob('*') if p.is_file()}


def test_valid_snapshots_are_read_only_and_never_runtime_proof(checkout, options, monkeypatch):
    paths = pair(checkout, options)
    before = contents(paths)
    def forbidden(*args, **kwargs):
        raise AssertionError('No network, subprocess or lifecycle operation is allowed')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(state, 'init', forbidden)
    report = isolation.audit(checkout, paths)
    assert report['configuration_disjoint'] is True
    assert report['runtime_verified'] is False
    assert report['states_validated'] == 2
    assert contents(paths) == before
    output = json.dumps(report)
    assert str(checkout) not in output
    for path in paths:
        owner = json.loads((path / 'owner.json').read_text())
        assert owner['project'] not in output
        assert (path / 'agent-key').read_text() not in output
        for account in json.loads((path / 'accounts.json').read_text()):
            assert account['password'] not in output


def test_same_state_is_not_two_environments(checkout, options):
    paths = pair(checkout, options)
    report = isolation.audit(checkout, [paths[0], paths[0]])
    assert {'STATE_PATH_OVERLAP', 'PROJECT_REUSED', 'VOLUME_REUSED', 'NETWORK_REUSED'} <= codes(report)
    assert report['configuration_disjoint'] is False


def test_same_port_in_independent_snapshots_is_rejected(checkout, options):
    paths = [checkout / '.local/one', checkout / '.local/two']
    for path in paths:
        state.init(checkout, path, options)
    assert codes(isolation.audit(checkout, paths)) == {'PUBLISHED_PORT_OVERLAP'}


@pytest.mark.parametrize('filename', ['manifest.json', 'compose.json', 'owner.json', '.env', 'agent-key'])
def test_tampered_snapshot_is_unknown_not_success(checkout, options, filename):
    paths = pair(checkout, options)
    (paths[1] / filename).write_text('private-secret-must-not-appear')
    report = isolation.audit(checkout, paths)
    assert report['issues'] == [{'code': 'STATE_INVALID', 'states': [2]}]
    assert not report['configuration_disjoint']
    assert 'private-secret' not in json.dumps(report)


def test_missing_state_does_not_create_it(checkout, options):
    paths = pair(checkout, options)
    missing = checkout / '.local/not-created'
    report = isolation.audit(checkout, [paths[0], missing])
    assert report['states_validated'] == 1 and codes(report) == {'STATE_INVALID'}
    assert not missing.exists()


def test_busy_state_is_not_read_or_unlocked(checkout, options):
    paths = pair(checkout, options)
    lock = paths[1] / 'command.lock'
    lock.write_text('synthetic-pid')
    before = contents(paths)
    report = isolation.audit(checkout, paths)
    assert report['issues'] == [{'code': 'STATE_BUSY', 'states': [2]}]
    assert contents(paths) == before


@pytest.mark.parametrize('count', [0, 1, 9])
def test_invalid_selection_does_not_pass(tmp_path, count):
    report = isolation.audit(tmp_path, [tmp_path / 'missing'] * count)
    assert codes(report) == {'STATE_COUNT_INVALID'}
    assert not report['configuration_disjoint']


def test_symlink_is_not_a_new_environment(checkout, options):
    paths = pair(checkout, options)
    alias = checkout / '.local/alias'
    alias.symlink_to(paths[0], target_is_directory=True)
    assert codes(isolation.audit(checkout, [alias, paths[1]])) == {'STATE_INVALID'}


def test_wrong_checkout_is_rejected(checkout, options, tmp_path):
    paths = pair(checkout, options)
    report = isolation.audit(tmp_path / 'other-checkout', paths)
    assert report['states_validated'] == 0 and codes(report) == {'STATE_INVALID'}


def test_cli_exit_code_and_safe_json(checkout, options, monkeypatch, capsys):
    paths = pair(checkout, options)
    monkeypatch.setattr(isolation, 'ROOT', checkout)
    assert isolation.main(['--state', str(paths[0]), '--state', str(paths[1])]) == 0
    assert json.loads(capsys.readouterr().out)['runtime_verified'] is False
    assert isolation.main(['--state', str(paths[0]), '--state', str(paths[0])]) == 2
    assert not json.loads(capsys.readouterr().out)['configuration_disjoint']


def inputs(tmp_path):
    def entry(name, port):
        private = tmp_path / name
        spec = {'name': name, 'volumes': {'db': {}}, 'networks': {'business': {}},
                'services': {'app': {'ports': [{'published': str(port), 'protocol': 'tcp'}],
                                     'volumes': ['db:/data']}}}
        return private, spec
    return entry('left', 18030), entry('right', 18031)


def comparison(data):
    return {'issues': isolation.compare([isolation.inventory(p, s) for p, s in data])}


@pytest.mark.parametrize('kind,code', [('volumes', 'VOLUME_REUSED'), ('networks', 'NETWORK_REUSED')])
@pytest.mark.parametrize('external', [False, True])
def test_explicit_names_bypass_project_namespace(tmp_path, kind, code, external):
    data = inputs(tmp_path)
    for _, spec in data:
        spec[kind] = {'shared': {'name': 'same-resource', 'external': external}}
        if kind == 'volumes':
            spec['services']['app']['volumes'] = ['shared:/data']
    assert code in codes(comparison(data))


@pytest.mark.parametrize('left_ro,right_ro,expected', [(True, True, False), (True, False, True), (False, True, True), (False, False, True)])
def test_shared_cache_requires_both_read_only(tmp_path, left_ro, right_ro, expected):
    data = inputs(tmp_path)
    for (_, spec), ro in zip(data, [left_ro, right_ro]):
        spec['services']['app']['volumes'].append({'type': 'bind', 'source': str(tmp_path / 'cache'), 'target': '/cache', 'read_only': ro})
    assert ('WRITABLE_BIND_OVERLAP' in codes(comparison(data))) is expected


def test_parent_child_writable_mount_is_overlap(tmp_path):
    data = inputs(tmp_path)
    for (_, spec), source in zip(data, [tmp_path / 'shared', tmp_path / 'shared/subdir']):
        spec['services']['app']['volumes'].append({'type': 'bind', 'source': str(source), 'target': '/cache'})
    assert 'WRITABLE_BIND_OVERLAP' in codes(comparison(data))


def test_other_private_state_is_forbidden_even_read_only(tmp_path):
    data = inputs(tmp_path)
    data[0][1]['services']['app']['volumes'].append({'type': 'bind', 'source': str(data[1][0] / 'agent-key'), 'target': '/key', 'read_only': True})
    assert 'CROSS_STATE_BIND' in codes(comparison(data))


def test_distinct_protocols_and_dynamic_ports(tmp_path):
    data = inputs(tmp_path)
    data[1][1]['services']['app']['ports'] = [{'published': '18030', 'protocol': 'udp'}]
    assert not comparison(data)['issues']
    for _, spec in data:
        spec['services']['app']['ports'] = [{'published': '0'}]
    assert not comparison(data)['issues']


@pytest.mark.parametrize('port', ['${PORT}', '8000-9000', '-1', '65536', True])
def test_unresolved_or_invalid_ports_refused(tmp_path, port):
    (path, spec), _ = inputs(tmp_path)
    spec['services']['app']['ports'] = [{'published': port}]
    with pytest.raises(ValueError):
        isolation.inventory(path, spec)


@pytest.mark.parametrize('config', [{'driver_opts': {'device': '/shared'}}, {'driver': 'nfs'}, {'external': 'true'}, {'name': '${DATA}'}])
def test_opaque_volume_resolution_refused(tmp_path, config):
    (path, spec), _ = inputs(tmp_path)
    spec['volumes']['db'] = config
    with pytest.raises(ValueError):
        isolation.inventory(path, spec)


@pytest.mark.parametrize('mount', ['/host:/data', 'db:/data:cached', {'type': 'volume', 'source': 'unknown'}, {'type': 'bind', 'source': '/tmp', 'read_only': 'false'}, {'type': 'bind', 'source': '/var/run/docker.sock'}])
def test_unsupported_mounts_are_not_ignored(tmp_path, mount):
    (path, spec), _ = inputs(tmp_path)
    spec['services']['app']['volumes'] = [mount]
    with pytest.raises(ValueError):
        isolation.inventory(path, spec)


def test_relative_state_paths_from_repository(checkout, options, monkeypatch):
    paths = pair(checkout, options)
    monkeypatch.chdir(checkout)
    report = isolation.audit(checkout, [p.relative_to(checkout) for p in paths])
    assert report['configuration_disjoint']


def test_disposable_e2e_is_not_mistaken_for_a_demo(checkout, options):
    from tools.shop_e2e.stack import prepare
    paths = pair(checkout, options)
    ephemeral = checkout / '.local/disposable'
    prepare(checkout, ephemeral)
    report = isolation.audit(checkout, [paths[0], ephemeral])
    assert report['issues'] == [{'code': 'STATE_INVALID', 'states': [2]}]


def test_three_states_keep_argument_positions(checkout, options):
    paths = pair(checkout, options)
    third = checkout / '.local/third'
    state.init(checkout, third, {**options, 'port': 18031})
    report = isolation.audit(checkout, [*paths, third])
    assert report['issues'] == [{'code': 'PUBLISHED_PORT_OVERLAP', 'states': [2, 3]}]


def test_unexpected_programming_error_is_not_hidden(checkout, monkeypatch):
    def broken(*args):
        raise AssertionError('programming error')
    monkeypatch.setattr(isolation, 'load', broken)
    with pytest.raises(AssertionError):
        isolation.audit(checkout, [checkout / '.local/a', checkout / '.local/b'])
