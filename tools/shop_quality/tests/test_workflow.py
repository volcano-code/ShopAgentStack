"""Static configuration checks only; this does not run GitHub Actions."""
from pathlib import Path
import re
import subprocess
import shutil
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]

def workflow():
    # YAML 1.1 safe_load converts 'on' to bool; BaseLoader retains GitHub's spelling.
    return yaml.load((ROOT / '.github/workflows/shop-quality.yml').read_text(), Loader=yaml.BaseLoader)

def test_ci_is_read_only_no_privileged_trigger():
    w = workflow()
    assert set(w['on']) == {'push', 'pull_request', 'workflow_dispatch'}
    assert w['permissions'] == {'contents': 'read'}
    assert 'pull_request_target' not in w['on']
    assert w['concurrency']['cancel-in-progress'] == 'true'

def test_ci_actions_are_immutable_pins():
    for job in workflow()['jobs'].values():
        for step in job['steps']:
            if 'uses' in step:
                assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', step['uses'])
                if step['uses'].startswith('actions/checkout@'):
                    assert step['with']['persist-credentials'] == 'false'

def test_ci_does_not_mask_test_failures():
    w = workflow()
    for job in w['jobs'].values():
        assert 'continue-on-error' not in job
        assert int(job['timeout-minutes']) > 0
        for step in job['steps']:
            assert 'continue-on-error' not in step
            run = step.get('run', '')
            assert '|| true' not in run
            assert '--if-present' not in run
            assert 'secrets.' not in run
            assert 'verify-p3c' not in run and 'verify-p4' not in run

def test_upstream_entrypoints_and_no_zero_test_green():
    w = workflow()
    text = str(w)
    assert 'npm --prefix apps/web run test:unit' in text
    assert 'npm --prefix apps/web run build' in text
    assert '-DskipTests=false' in text and '-Ddocker.skip=true' in text
    assert '-pl mall-portal,mall-admin -am clean verify' in text
    assert '-Dsurefire.failIfNoSpecifiedTests=false' in text
    assert 'tools.shop_quality.junit_gate' in text
    assert '--network none' in text
    assert '--tmpfs /data:rw,uid=10001,gid=10001,mode=700' in text
    assert set(w['jobs']['required']['needs']) == {'quality-tooling','web','commerce','agent'}
    assert w['jobs']['required']['if'] == 'always()'

@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required only for shell syntax validation")
def test_embedded_bash_syntax():
    for job in workflow()['jobs'].values():
        for step in job['steps']:
            if 'run' in step:
                run = re.sub(r'\$\{\{.*?\}\}', 'VALUE', step['run'])
                proc = subprocess.run(['bash', '-n'], input=run, text=True, capture_output=True)
                assert proc.returncode == 0, proc.stderr
