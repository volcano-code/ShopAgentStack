"""Regression for official MySQL directory permissions and bounded failure diagnostics."""
import io
import json
import tarfile
import pytest

from tools.shop_demo import backup_volume as v


@pytest.fixture
def private(tmp_path):
    path = tmp_path / 'vault'
    path.mkdir(mode=0o700)
    return path


def test_mysql_sticky_directory_mode_is_preserved(private):
    source = private / 'mysql'; source.mkdir(mode=0o1777); source.chmod(0o1777)
    (source / 'database').write_bytes(b'synthetic-cold-database')
    archive = private / 'mysql.tgz'
    digest = v.pack(source, archive, 'mysql_data')
    target = private / 'restored'; target.mkdir()
    assert v.unpack(archive, target) == digest
    assert target.stat().st_mode & 0o7777 == 0o1777
    assert (target / 'database').read_bytes() == b'synthetic-cold-database'


@pytest.mark.parametrize('mode,kind', [(0o4755,tarfile.REGTYPE),(0o2755,tarfile.REGTYPE),
                                      (0o1755,tarfile.REGTYPE),(0o2770,tarfile.DIRTYPE)])
def test_privilege_bits_still_rejected(private,mode,kind):
    archive = private / 'bad-mode.tgz'
    with tarfile.open(archive,'w:gz') as tar:
        root=tarfile.TarInfo('.');root.type=tarfile.DIRTYPE;tar.addfile(root)
        item=tarfile.TarInfo('entry');item.type=kind;item.mode=mode;tar.addfile(item,io.BytesIO())
    with pytest.raises(ValueError): v.inventory(archive)


def test_helper_error_reports_only_structural_frames(monkeypatch,capsys):
    def fail(): raise ValueError('synthetic-secret-and-private-path')
    monkeypatch.setattr(v,'main',fail)
    assert v.run_cli()==1
    output=capsys.readouterr()
    assert 'synthetic-secret' not in output.out + output.err
    report=json.loads(output.out)
    assert report['verified'] is False and report['failure_type']=='ValueError'
    assert set(report)=={'verified','failure_type','helper_frames'}


def test_helper_diagnostic_filter_rejects_private_payloads(private):
    from tools.shop_demo.backup_smoke import helper_diagnostics
    (private/'logs').mkdir()
    (private/'logs/001-cold-pack-mysql_data.log').write_text(json.dumps({
        'verified':False,'failure_type':'ValueError','helper_frames':[{'function':'inventory','line':30,'local':'secret'},
        {'function':'secret-function','line':42}],'detail':'secret'}))
    result=helper_diagnostics((private,))
    assert result==[{'failure_type':'ValueError','helper_frames':[{'function':'inventory','line':30}]}]
    assert 'secret' not in json.dumps(result)
