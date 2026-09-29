"""Regression for the real publisher registry used by the disposable stack."""
from pathlib import Path
from tools.shop_e2e import stack

ROOT = Path(__file__).resolve().parents[3]
P = "shop-e2e-" + "a" * 32


def test_minio_uses_publisher_registry_without_changing_release():
    spec = stack.compose(ROOT, ROOT / ".local/test", P, hybrid=True)
    assert spec["services"]["minio"]["image"] == "quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z"
    assert spec["services"]["minio"]["networks"] == ["business"]
    assert "ports" not in spec["services"]["minio"]
