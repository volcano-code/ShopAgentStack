from pathlib import Path
import json
import shutil
import pytest
from tools.shop_demo import state as st
from tools.shop_demo.seed_data import bundle
ROOT=Path(__file__).resolve().parents[3]


def sources(checkout):
    for name in ("catalog", "knowledge"):
        shutil.copytree(ROOT/name, checkout/name)
    shutil.copytree(ROOT/"apps/web/public/products",checkout/"apps/web/dist/products")


def test_ui_opt_in_requires_import_flag(options):
    with pytest.raises(ValueError): st.validate_options(options|{"seed_review_ui":True})
    st.validate_options(options|{"seed_import":True,"seed_review_ui":True})


@pytest.mark.parametrize("bad",["true",1,None,[]])
def test_invalid_ui_flag_refused(options,bad):
    with pytest.raises(ValueError): st.validate_options(options|{"seed_import":True,"seed_review_ui":bad})


def test_old_states_do_not_gain_a_template(checkout,options):
    state=checkout/".local/demo";st.init(checkout,state,options|{"seed_import":True})
    _,spec=st.load(checkout,state)
    assert "SHOP_AGENT_STACK_DEMO_IMPORT_UI_ENABLED" not in spec["services"]["admin"]["environment"]
    assert not (state/"runtime/demo-import-bundle.json").exists()


def test_template_is_private_snapshot_not_web_asset(checkout,options):
    sources(checkout);state=checkout/".local/demo"
    st.init(checkout,state,options|{"seed_import":True,"seed_review_ui":True})
    _,spec=st.load(checkout,state)
    template=state/"runtime/demo-import-bundle.json"
    assert json.loads(template.read_text())==bundle(checkout,state)
    manifest=json.loads((state/"manifest.json").read_text())
    assert manifest["files"]["runtime/demo-import-bundle.json"]==st.sha(template)
    for name,svc in spec["services"].items():
        mounts=[v for v in svc.get("volumes",[]) if isinstance(v,dict) and v.get("source")==str(template)]
        if name=="admin":assert len(mounts)==1 and mounts[0]["read_only"] is True
        else:assert not mounts
    assert not list((state/"runtime/apps/web/dist").rglob("*bundle.json"))
    template.chmod(0o600);template.write_text("tampered")
    with pytest.raises(ValueError):st.load(checkout,state)


def test_cli_ui_opt_in_is_explicit():
    from tools.shop_demo import __main__ as cli
    with pytest.raises(SystemExit) as error:cli.main(["init","--unknown-ui-option"])
    assert error.value.code==2


def test_browser_workflow_gate_matches_actual_pytest_report(tmp_path, monkeypatch, capsys):
    """Exercise the workflow's real argv, not merely the presence of a gate command."""
    import shlex
    import sys
    import yaml
    from tools.shop_quality import junit_gate
    workflow=yaml.load((ROOT/".github/workflows/admin-review-ui.yml").read_text(),Loader=yaml.BaseLoader)
    step=next(x for x in workflow["jobs"]["browser-review"]["steps"] if x.get("name")=="Test private template snapshot contracts")
    lines=step["run"].splitlines()
    pytest_args=shlex.split(next(x for x in lines if "python -m pytest" in x))
    report=tmp_path/next(x.split("=",1)[1] for x in pytest_args if x.startswith("--junitxml="))
    report.parent.mkdir(parents=True)
    args=shlex.split(next(x for x in lines if "python -m tools.shop_quality.junit_gate" in x))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,"argv",["junit_gate",*args[3:]])
    # Fail closed for missing, empty, skipped-only or failing evidence; accept real executed cases.
    assert junit_gate.main()==1
    for content,code in [('<testsuite><testcase name="one"/></testsuite>',0),
                         ('<testsuite/>',1),
                         ('<testsuite><testcase><skipped/></testcase></testsuite>',1),
                         ('<testsuite><testcase><failure/></testcase></testsuite>',1)]:
        report.write_text(content)
        assert junit_gate.main()==code
    capsys.readouterr()
