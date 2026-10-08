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
