"""Static CI contract only; actual acceptance comes from hosted smoke artifacts."""
from pathlib import Path
import re
import subprocess
import yaml
from tools.shop_demo.state import ROOT


def test_read_only_pinned_workflow_and_narrow_artifacts():
    source = (ROOT / '.github/workflows/local-demo.yml').read_text()
    w = yaml.load(source, Loader=yaml.BaseLoader)
    assert set(w['on']) == {'pull_request', 'workflow_dispatch'}
    assert w['permissions'] == {'contents': 'read'}
    job = w['jobs']['persistent-demo']
    assert job['strategy']['matrix']['retrieval'] == ['bm25', 'hybrid']
    for step in job['steps']:
        assert 'continue-on-error' not in step
        if 'uses' in step:
            assert re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', step['uses'])
        if step.get('uses', '').startswith('actions/checkout@'):
            assert step['with']['persist-credentials'] == 'false'
        if step.get('uses', '').startswith('actions/upload-artifact@'):
            assert '/artifacts/' in step['with']['path']
            assert 'accounts.json' not in step['with']['path']
            assert 'logs' not in step['with']['path']
        if 'run' in step:
            run = re.sub(r'\$\{\{.*?\}\}', 'hybrid', step['run'])
            result = subprocess.run(['bash', '-n'], input=run, text=True, capture_output=True)
            assert result.returncode == 0, result.stderr
    assert 'tools.shop_demo build' in source and 'tools.shop_demo.smoke' in source
    assert 'tools.shop_quality.junit_gate' in source
    assert '|| true' not in source and 'secrets.' not in source
