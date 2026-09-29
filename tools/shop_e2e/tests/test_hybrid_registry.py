"""Regression for the real publisher registry used by the disposable stack."""
from pathlib import Path
from tools.shop_e2e import stack

ROOT = Path(__file__).resolve().parents[3]
P = "shop-e2e-" + "a" * 32


def test_minio_publisher_image_is_digest_pinned_and_private():
    spec = stack.compose(ROOT, ROOT / ".local/test", P, hybrid=True)
    assert spec["services"]["minio"]["image"] == "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
    assert spec["services"]["minio"]["networks"] == ["business"]
    assert "ports" not in spec["services"]["minio"]


def test_registry_preflight_precedes_model_preparation():
    import yaml
    workflow = yaml.load((ROOT / ".github/workflows/hybrid-retrieval-e2e.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["hybrid"]["steps"]
    names = [s.get("name", "") for s in steps]
    preflight = names.index("Check pinned object store before expensive preparation")
    assert preflight < names.index("Build real Java applications with established regressions")
    assert "docker pull" in steps[preflight]["run"]
    assert 'compose' in steps[preflight]["run"]
