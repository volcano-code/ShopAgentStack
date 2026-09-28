"""Prevent CI configuration regressions; not a hosted Actions execution claim."""
from pathlib import Path
import subprocess
import sys
import os
import re

import pytest
from urllib.parse import urlsplit

import yaml

from tools.shop_observability.wait_ready import ENDPOINTS

ROOT = Path(__file__).resolve().parents[3]


def load(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


DOMAIN_TESTS = ["AgentOperationTest", "RefundServiceTest", "ProductQueryServiceTest",
                "SupportServiceTest", "CatalogManagementServiceTest", "FulfillmentServiceTest"]
PORTAL_TESTS = ["OrderOwnershipTest", "CartPricingTest"]


def commerce_commands():
    steps = load('.github/workflows/shop-quality.yml')['jobs']['commerce']['steps']
    return [s['run'] for s in steps if 'run' in s]


def test_java_gate_is_domain_scoped_and_tests_are_not_skipped():
    commands = commerce_commands()
    build = next(c for c in commands if 'mvn -B' in c)
    assert '-DskipTests=false' in build and '-Ddocker.skip=true' in build
    assert '-Dsurefire.failIfNoSpecifiedTests=false' in build
    assert '-pl mall-portal,mall-admin -am clean verify' in build
    selected = re.search(r"-Dtest=([^'\s]+)", build).group(1).split(',')
    assert set(selected) == set(DOMAIN_TESTS + PORTAL_TESTS) and len(selected) == 8
    gates = [c for c in commands if 'tools.shop_quality.junit_gate' in c]
    assert len(gates) == 1
    assert '--root services/commerce/shop-agent-stack-after-sale/target/surefire-reports' in gates[0]
    assert '--root services/commerce/mall-portal/target/surefire-reports' in gates[0]
    assert 'TEST-com.macro.mall.shopagentstack.${test}.xml' in gates[0]
    assert 'TEST-com.macro.mall.portal.service.${test}.xml' in gates[0]


@pytest.mark.parametrize('missing', [None] + DOMAIN_TESTS + PORTAL_TESTS)
def test_each_required_report_must_exist(tmp_path, missing):
    # These synthetic XML documents test the gate, not the underlying Java business logic.
    for module, package, classes in [
        ('shop-agent-stack-after-sale', 'com.macro.mall.shopagentstack', DOMAIN_TESTS),
        ('mall-portal', 'com.macro.mall.portal.service', PORTAL_TESTS),
    ]:
        target = tmp_path / 'services/commerce' / module / 'target/surefire-reports'
        target.mkdir(parents=True)
        for name in classes:
            if name != missing:
                (target / f'TEST-{package}.{name}.xml').write_text(
                    f'<testsuite><testcase classname="{package}.{name}" name="synthetic"/></testsuite>')
        # Passing an unrelated class must not conceal a missing required class.
        (target / 'TEST-Unrelated.xml').write_text('<testsuite><testcase name="unrelated"/></testsuite>')
    gate = next(c for c in commerce_commands() if 'tools.shop_quality.junit_gate' in c)
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', gate], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (missing is None), result.stdout + result.stderr


@pytest.mark.parametrize('element', ['skipped', 'failure', 'error'])
def test_report_file_without_successful_execution_fails(tmp_path, element):
    report = tmp_path / 'TEST-com.macro.mall.shopagentstack.AgentOperationTest.xml'
    report.write_text(f'<testsuite><testcase name="synthetic"><{element}/></testcase></testsuite>')
    proc = subprocess.run([sys.executable, '-m', 'tools.shop_quality.junit_gate', '--root',
                           str(tmp_path), '--pattern', report.name],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 1


def test_unrelated_tests_do_not_make_missing_domain_tests_pass(tmp_path):
    (tmp_path / 'unrelated').mkdir()
    (tmp_path / 'unrelated/TEST-pass.xml').write_text('<testsuite><testcase name="other"/></testsuite>')
    proc = subprocess.run([sys.executable, '-m', 'tools.shop_quality.junit_gate', '--root',
                           str(tmp_path / 'owned'), '--pattern', 'TEST-*.xml'],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 1


def test_ci_jobs_record_checkout_and_config_hashes():
    workflow = load('.github/workflows/shop-quality.yml')
    for name in ['quality-tooling', 'web', 'commerce', 'agent']:
        job = workflow['jobs'][name]
        assert 'needs' not in job  # Unrelated failures must not suppress execution evidence.
        commands = [s.get('run', '') for s in job['steps']]
        assert any('git rev-parse HEAD HEAD^{tree}' in c and 'git ls-files -s' in c for c in commands)
        uploads = [s for s in job['steps'] if s.get('uses', '').startswith('actions/upload-artifact@')]
        assert any('artifacts/' in s['with']['path'] for s in uploads)


def test_readiness_ports_match_loopback_compose_ports():
    services = load('deploy/observability/compose.yaml')['services']
    for name, url in ENDPOINTS.items():
        service = services['otel-collector' if name == 'collector' else name]
        parsed = urlsplit(url)
        assert parsed.hostname == '127.0.0.1'
        assert any(p.startswith(f'127.0.0.1:{parsed.port}:') for p in service['ports'])
    collector = load('deploy/observability/otel-collector.yaml')
    assert 'health_check' in collector['service']['extensions']
    assert collector['extensions']['health_check']['endpoint'] == '0.0.0.0:13133'


def test_transport_workflow_runs_real_readback_after_bounded_readiness():
    wf = load('.github/workflows/observability-smoke.yml')
    assert set(wf['on']) == {'pull_request', 'workflow_dispatch'}
    assert 'tools/shop_observability/**' in wf['on']['pull_request']['paths']
    steps = wf['jobs']['collector-tempo']['steps']
    commands = [s.get('run', '') for s in steps]
    ready = next(i for i, c in enumerate(commands) if 'tools.shop_observability.wait_ready' in c)
    smoke = next(i for i, c in enumerate(commands) if 'tools.shop_observability.collector_smoke' in c)
    assert ready < smoke
    assert '--timeout 120' in commands[ready]
    assert any('git rev-parse HEAD HEAD^{tree}' in c for c in commands)
    assert any('logs --no-color otel-collector tempo' in c for c in commands)


def test_diagnostic_network_can_publish_without_public_default_bind():
    config = load('deploy/observability/compose.yaml')
    network = config['networks']['default']
    assert network['driver'] == 'bridge'
    assert network.get('internal', 'false') == 'false'
    assert network['driver_opts']['com.docker.network.bridge.host_binding_ipv4'] == '127.0.0.1'
    for service in config['services'].values():
        assert all(port.startswith('127.0.0.1:') for port in service.get('ports', []))
