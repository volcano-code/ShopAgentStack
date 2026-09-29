"""Native Milvus Standalone storage is an explicit scope, not an S3 substitute."""
from pathlib import Path
import yaml
from tools.shop_e2e import stack

ROOT = Path(__file__).resolve().parents[3]
P = "shop-e2e-" + "a" * 32


def test_native_standalone_still_requires_real_server_and_persistence():
    spec = stack.compose(ROOT, ROOT / ".local/test", P, hybrid=True)
    assert "minio" not in spec["services"] and "etcd" not in spec["services"]
    server = spec["services"]["milvus"]
    assert server["image"] == "milvusdb/milvus:v2.6.15"
    assert server["command"] == ["milvus", "run", "standalone"]
    assert server["environment"]["DEPLOY_MODE"] == "STANDALONE"
    assert server["environment"]["ETCD_USE_EMBED"] == "true"
    assert server["environment"]["COMMON_STORAGETYPE"] == "local"
    assert server["networks"] == ["business"] and "ports" not in server
    assert "hybrid_milvus:/var/lib/milvus" in server["volumes"]
    assert all(v["read_only"] for v in server["volumes"] if isinstance(v, dict))
    assert "security_opt" not in server  # no seccomp-unconfined escape hatch


def test_registry_preflight_precedes_model_preparation():
    workflow = yaml.load((ROOT / ".github/workflows/hybrid-retrieval-e2e.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["hybrid"]["steps"]
    names = [s.get("name", "") for s in steps]
    preflight = names.index("Check vector database image before expensive preparation")
    assert preflight < names.index("Build real Java applications with established regressions")
    assert "docker pull" in steps[preflight]["run"]
    assert '["milvus"]' in steps[preflight]["run"]


def test_native_configs_do_not_fall_back_to_remote_object_store():
    spec = yaml.safe_load((ROOT / "deploy/retrieval/milvus-e2e.yaml").read_text())
    assert spec == {"common": {"storageType": "local"}, "mq": {"type": "woodpecker"},
                    "woodpecker": {"storage": {"type": "local", "rootPath": "/var/lib/milvus/woodpecker"}}}
    etcd = yaml.safe_load((ROOT / "deploy/retrieval/embed-etcd-e2e.yaml").read_text())
    assert etcd["listen-client-urls"] == etcd["advertise-client-urls"] == "http://127.0.0.1:2379"
    # No endpoint, collection or readback assertions in the six-stage gate were removed.
    from tools.shop_e2e import hybrid
    assert len(hybrid.STAGES) == 6
