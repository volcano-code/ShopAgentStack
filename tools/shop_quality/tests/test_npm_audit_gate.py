"""Synthetic report/process fixtures: these tests are not a live npm security scan."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools.shop_quality import npm_audit_gate as gate

ROOT = Path(__file__).resolve().parents[3]
NODES = frozenset({'node_modules/source-map-js', 'node_modules/postcss'})


def report(severity=None):
    data = {'auditReportVersion': 2, 'vulnerabilities': {}, 'metadata': {
        'vulnerabilities': {key: 0 for key in (*gate.SEVERITIES, 'total')},
        'dependencies': {'prod': 0, 'dev': 2, 'optional': 0, 'peer': 0, 'peerOptional': 0, 'total': 2}}}
    if severity:
        data['vulnerabilities']['source-map-js'] = {
            'name': 'source-map-js', 'severity': severity, 'isDirect': False,
            'via': [{'source': 1, 'severity': severity, 'title': 'DO_NOT_COPY_REPORT_BODY'}],
            'nodes': ['node_modules/source-map-js'], 'effects': [], 'fixAvailable': True}
        data['metadata']['vulnerabilities'].update({severity: 1, 'total': 1})
    return data


def encode(value):
    return json.dumps(value).encode()


@pytest.mark.parametrize('severity,code', [(None, 0), ('info', 0), ('low', 0), ('moderate', 0), ('high', 1), ('critical', 1)])
def test_threshold_includes_development_vulnerabilities(severity, code):
    result = gate.evaluate(encode(report(severity)), code, NODES)
    assert result['exit_code'] == code
    assert result['passed'] is (code == 0)
    assert 'DO_NOT_COPY_REPORT_BODY' not in json.dumps(result)


@pytest.mark.parametrize('raw', [b'', b'[]', b'null', b'<html>PRIVATE</html>', b'\xff', b'{"x":1,"x":2}',
                               b'{"auditReportVersion":NaN}', b' ' * (gate.MAX_REPORT_BYTES + 1), b'{' ])
def test_bad_json_never_passes_or_echoes_input(raw):
    result = gate.evaluate(raw, 0, NODES)
    assert result['exit_code'] == 2 and result['counts'] is None
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('case', ['schema', 'missing_counts', 'missing_high', 'string_count', 'boolean_count',
    'negative_count', 'unknown_count', 'wrong_total', 'hidden_high', 'missing_inventory', 'short_inventory',
    'noninteger_inventory', 'missing_via', 'invalid_via', 'foreign_node', 'duplicate_node', 'missing_node',
    'wrong_name', 'severity', 'advisory_more_severe', 'exit_mismatch', 'registry_error'])
def test_report_inconsistencies_fail_closed(case):
    value = report('high'); counts = value['metadata']['vulnerabilities']; item = value['vulnerabilities']['source-map-js']; code = 1
    if case == 'schema': value['auditReportVersion'] = 3
    elif case == 'missing_counts': del value['metadata']['vulnerabilities']
    elif case == 'missing_high': del counts['high']
    elif case == 'string_count': counts['high'] = '1'
    elif case == 'boolean_count': counts['high'] = True
    elif case == 'negative_count': counts['low'] = -1
    elif case == 'unknown_count': counts['ignored'] = 0
    elif case == 'wrong_total': counts['total'] = 0
    elif case == 'hidden_high': counts.update(high=0, total=0); code = 0
    elif case == 'missing_inventory': del value['metadata']['dependencies']
    elif case == 'short_inventory': value['metadata']['dependencies']['total'] = 1
    elif case == 'noninteger_inventory': value['metadata']['dependencies']['dev'] = '2'
    elif case == 'missing_via': del item['via']
    elif case == 'invalid_via': item['via'] = ['not-in-report']
    elif case == 'foreign_node': item['nodes'] = ['node_modules/PRIVATE']
    elif case == 'duplicate_node': item['nodes'] *= 2
    elif case == 'missing_node': item['nodes'] = []
    elif case == 'wrong_name': item['name'] = 'PRIVATE'
    elif case == 'severity': item['severity'] = 'unknown'
    elif case == 'advisory_more_severe': item['via'][0]['severity'] = 'critical'
    elif case == 'exit_mismatch': code = 0
    elif case == 'registry_error': value['error'] = {'summary': 'PRIVATE'}
    result = gate.evaluate(encode(value), code, NODES)
    assert result['exit_code'] == 2 and not result['passed'] and result['reasons']
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('code', [-1, 2, 127, True, '0', None])
def test_execution_failures_are_not_zero_vulnerabilities(code):
    assert gate.evaluate(encode(report()), code, NODES)['exit_code'] == 2


def project(tmp_path):
    root = tmp_path / 'web'; root.mkdir()
    (root / 'package.json').write_text('{"name":"synthetic-fixture","private":true}')
    (root / 'package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': {
        '': {}, **{node: {'version': '1.2.2'} for node in NODES}}}))
    return root


def process(monkeypatch, data=None, code=0, error=None, mutate=None):
    monkeypatch.setattr(gate.shutil, 'which', lambda _: '/synthetic/npm')
    def fake(command, **kwargs):
        assert command[0] == '/synthetic/npm'
        assert command[1:] == ['audit', '--json', '--audit-level=high', '--package-lock-only',
            '--include=prod', '--include=dev', '--include=optional', '--include=peer', '--ignore-scripts',
            '--workspaces=false', '--registry=https://registry.npmjs.org', '--fetch-retries=0',
            '--fetch-timeout=30000', '--no-fund']
        assert kwargs['check'] is False and kwargs['timeout'] <= 300
        if error: raise error
        kwargs['stdout'].write(encode(data if data is not None else report()))
        kwargs['stderr'].write(b'PRIVATE_STDERR_TOKEN')
        if mutate: mutate(kwargs['cwd'])
        return subprocess.CompletedProcess(command, code)
    monkeypatch.setattr(gate.subprocess, 'run', fake)


@pytest.mark.parametrize('severity,code', [(None, 0), ('moderate', 0), ('high', 1), ('critical', 1)])
def test_real_runner_wiring_with_explicit_process_double(tmp_path, monkeypatch, severity, code):
    root = project(tmp_path); out = tmp_path / 'audit'; summary = tmp_path / 'step.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setenv('NODE_ENV', 'production'); monkeypatch.setenv('npm_config_omit', 'dev')
    process(monkeypatch, report(severity), code)
    before = (root / 'package-lock.json').read_bytes()
    assert gate.run(root, out) == code
    saved = json.loads((out / 'audit.json').read_text())
    assert saved['counts'] == report(severity)['metadata']['vulnerabilities']
    assert saved['lockfile_sha256'] == gate._sha(before)
    assert saved['gate_sha256'] == gate._sha(Path(gate.__file__).read_bytes())
    assert saved['npm_command_executed'] is True
    assert 'PRIVATE' not in ''.join(file.read_text() for file in out.iterdir())
    assert 'PRIVATE' not in summary.read_text()
    assert (root / 'package-lock.json').read_bytes() == before


@pytest.mark.parametrize('failure', ['timeout', 'io', 'changed_lock', 'changed_manifest', 'no_npm', 'registry', 'oversize'])
def test_runner_failures_preserve_unknown_counts_and_private_diagnostics(tmp_path, monkeypatch, failure):
    root = project(tmp_path); out = tmp_path / 'audit'
    process(monkeypatch)
    if failure == 'timeout': process(monkeypatch, error=subprocess.TimeoutExpired(['PRIVATE'], 1))
    elif failure == 'io': process(monkeypatch, error=OSError('PRIVATE_IO'))
    elif failure == 'changed_lock': process(monkeypatch, mutate=lambda p: (p/'package-lock.json').write_text('{}'))
    elif failure == 'changed_manifest': process(monkeypatch, mutate=lambda p: (p/'package.json').write_text('{}'))
    elif failure == 'no_npm': monkeypatch.setattr(gate.shutil, 'which', lambda _: None)
    elif failure == 'registry': process(monkeypatch, data={'error': {'summary': 'PRIVATE_NETWORK'}}, code=1)
    elif failure == 'oversize': process(monkeypatch, data={'PRIVATE': 'x' * (gate.MAX_REPORT_BYTES + 1)})
    assert gate.run(root, out) == 2
    data = json.loads((out / 'audit.json').read_text())
    assert data['status'] == 'unavailable' and data['counts'] is None
    assert 'PRIVATE' not in (out / 'audit.json').read_text()
    assert '| high | unknown |' in (out / 'audit.md').read_text()


def test_existing_evidence_is_never_overwritten(tmp_path, monkeypatch):
    root = project(tmp_path); out = tmp_path/'audit'; out.mkdir()
    (out/'audit.json').write_text('ORIGINAL')
    monkeypatch.setattr(gate.shutil, 'which', lambda _: pytest.fail('must not start npm'))
    assert gate.run(root, out) == 2
    assert (out/'audit.json').read_text() == 'ORIGINAL'


def test_reject_shrinkwrap_and_symlink_inputs(tmp_path, monkeypatch):
    root = project(tmp_path)
    (root/'npm-shrinkwrap.json').write_text('{}')
    monkeypatch.setattr(gate.shutil, 'which', lambda _: pytest.fail('must not start npm'))
    assert gate.run(root, tmp_path/'first') == 2
    (root/'npm-shrinkwrap.json').unlink()
    lock = root/'package-lock.json'; content = lock.read_bytes(); lock.unlink()
    target = tmp_path/'outside.json'; target.write_bytes(content); lock.symlink_to(target)
    assert gate.run(root, tmp_path/'second') == 2


def test_lock_patch_uses_registry_integrity_and_existing_postcss_range():
    lock = json.loads((ROOT/'apps/web/package-lock.json').read_text())
    entry = lock['packages']['node_modules/source-map-js']
    assert entry['version'] == '1.2.2'
    assert entry['integrity'] == 'sha512-KGj/8Y43x35aZVDtt+J4mK1hoLGHULMYfSkODJNQjNDC3oW1PqPoxMwo0pLUsWM/UEGzON/NxeHywEfNXNP3Vw=='
    assert entry['resolved'] == 'https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz'
    assert lock['packages']['node_modules/postcss']['dependencies']['source-map-js'] == '^1.2.1'
    assert entry['dev'] is True
    assert len(gate.lock_nodes((ROOT/'apps/web/package-lock.json').read_bytes())) == 175


@pytest.mark.parametrize('name,job', [('shop-quality.yml', 'web'), ('workspace-experience.yml', 'browser')])
def test_workflows_enforce_gate_and_preserve_failure_artifacts(name, job):
    workflow = yaml.safe_load((ROOT/'.github/workflows'/name).read_text())
    steps = workflow['jobs'][job]['steps']
    gates = [s for s in steps if 'tools.shop_quality.npm_audit_gate' in s.get('run', '')]
    assert len(gates) == 1 and gates[0].get('continue-on-error') is not True
    assert '--project apps/web' in gates[0]['run'] and '--out ' in gates[0]['run']
    assert '|| true' not in gates[0]['run'] and 'set +e' not in gates[0]['run']
    assert any('actions/setup-python@' in step.get('uses', '') for step in steps[:steps.index(gates[0])])
    uploads = [s for s in steps if 'actions/upload-artifact@' in s.get('uses', '')]
    assert uploads and all(s.get('if') == 'always()' for s in uploads)
    assert workflow['permissions'] == {'contents': 'read'}


def test_cli_no_npm_fails_instead_of_using_fixture_report(tmp_path):
    root = project(tmp_path); out = tmp_path/'cli'
    import os
    env = dict(os.environ, PATH='/no-executables'); env.pop('GITHUB_STEP_SUMMARY', None)
    completed = subprocess.run([sys.executable, '-m', 'tools.shop_quality.npm_audit_gate',
        '--project', str(root), '--out', str(out)], cwd=ROOT, env=env, capture_output=True, timeout=10)
    assert completed.returncode == 2
    assert json.loads((out/'audit.json').read_text())['reasons'] == ['npm_not_available']


@pytest.mark.parametrize('source', [1241209, 10**9, 2**53-1])
def test_real_registry_advisory_identifier_is_not_a_package_count(source):
    value = report('high'); value['vulnerabilities']['source-map-js']['via'][0]['source'] = source
    assert gate.evaluate(encode(value), 1, NODES)['exit_code'] == 1
