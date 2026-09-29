"""Optional, isolated real-model/Milvus services; no display stack or external volumes."""
from pathlib import Path


def add_hybrid(root: Path, state: Path, project: str, services: dict, bind) -> list[str]:
    models = "hybrid_models"
    retrieval_image = project + "-retrieval:local"
    common_mounts = [bind(root / "services/retrieval", "/workspace/services/retrieval"),
                     bind(root / "services/agent", "/workspace/services/agent"),
                     bind(root / "evaluation/retrieval-matrix.json", "/workspace/evaluation/retrieval-matrix.json"),
                     bind(root / "tools/shop_e2e", "/workspace/tools/shop_e2e")]
    services["model-prefetch"] = {
        "profiles": ["prepare"], "image": retrieval_image,
        "build": {"context": str(root / "services/retrieval")},
        "command": ["python", "services/retrieval/cache_models.py"],
        "environment": {"HF_HOME": "/models", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "HF_HUB_DISABLE_XET": "1"},
        "volumes": [*common_mounts, models + ":/models"],
        "networks": ["edge"], "mem_limit": "1g",
    }
    services["etcd"] = {"image": "quay.io/coreos/etcd:v3.5.18",
        "command": ["etcd", "--advertise-client-urls=http://etcd:2379", "--listen-client-urls=http://0.0.0.0:2379", "--data-dir=/etcd"],
        "environment": {"ETCD_AUTO_COMPACTION_MODE": "revision", "ETCD_AUTO_COMPACTION_RETENTION": "1000"},
        "volumes": ["hybrid_etcd:/etcd"], "mem_limit": "256m"}
    services["minio"] = {"image": "quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z",
        "command": ["minio", "server", "/data"], "volumes": ["hybrid_minio:/data"], "mem_limit": "512m"}
    services["milvus"] = {"image": "milvusdb/milvus:v2.6.15",
        "command": ["milvus", "run", "standalone"],
        "environment": {"ETCD_ENDPOINTS": "etcd:2379", "MINIO_ADDRESS": "minio:9000"},
        "volumes": ["hybrid_milvus:/var/lib/milvus"], "depends_on": ["etcd", "minio"],
        "healthcheck": {"test": ["CMD", "curl", "-f", "http://localhost:9091/healthz"], "interval": "5s", "timeout": "3s", "retries": 60},
        "mem_limit": "2g"}
    services["retrieval-worker"] = {
        "image": retrieval_image, "command": ["python", "services/retrieval/online.py"],
        "environment": {"HF_HOME": "/models", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2"},
        "volumes": [*common_mounts, models + ":/models:ro", bind(state / "index-key", "/run/secrets/index_key")],
        "depends_on": {"portal": {"condition": "service_healthy"}, "milvus": {"condition": "service_healthy"}},
        "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8020/health',timeout=2)"],
                        "interval": "5s", "timeout": "3s", "retries": 60}, "mem_limit": "3g"}
    for name in ("portal", "commerce-mcp", "agent"):
        services[name].setdefault("volumes", []).append(bind(state / "index-key", "/run/secrets/index_key"))
    services["commerce-mcp"]["environment"]["SHOP_AGENT_STACK_RETRIEVAL_MODE"] = "hybrid"
    # Explicit test executable and per-run accounts. No host Docker socket in any service.
    services["agent"]["volumes"].extend([
        bind(root / "tools/shop_e2e", "/opt/shop_e2e"),
        bind(state / "accounts.json", "/run/secrets/test_accounts")])
    return [models, "hybrid_etcd", "hybrid_minio", "hybrid_milvus"]
