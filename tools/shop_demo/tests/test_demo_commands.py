"""Actual subprocess logging tests; no Docker daemon or external service is used."""
import os
import stat
import sys
import pytest
from tools.shop_demo import runtime as r


def test_command_captures_private_output_without_echoing(tmp_path, capsys):
    (tmp_path / 'logs').mkdir()
    sentinel = 'SYNTHETIC-NOT-A-CREDENTIAL'
    result = r.command(tmp_path, 'unit-output', [sys.executable, '-c', f'print({sentinel!r})'])
    assert sentinel in result
    assert sentinel not in capsys.readouterr().out
    files = list((tmp_path / 'logs').glob('*.log'))
    assert len(files) == 1 and sentinel in files[0].read_text()
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_command_failure_retains_first_private_log(tmp_path):
    (tmp_path / 'logs').mkdir()
    with pytest.raises(r.DemoError):
        r.command(tmp_path, 'unit-failure', [sys.executable, '-c', "print('FIRST-FAILURE'); raise SystemExit(7)"])
    r.command(tmp_path, 'unit-failure', [sys.executable, '-c', "print('LATER-PASS')"])
    files = list((tmp_path / 'logs').glob('*.log'))
    assert len(files) == 2
    assert any('FIRST-FAILURE' in p.read_text() for p in files)


def test_command_timeout_is_bounded(tmp_path):
    (tmp_path / 'logs').mkdir()
    with pytest.raises(r.DemoError, match='TimeoutExpired'):
        r.command(tmp_path, 'unit-timeout', [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.05)
    assert 'TimeoutExpired' in next((tmp_path / 'logs').glob('*.log')).read_text()
