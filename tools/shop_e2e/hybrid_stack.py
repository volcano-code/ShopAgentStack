"""Optional, isolated real-model/Milvus services; no display stack or external volumes."""
from pathlib import Path


def add_hybrid(root: Path, state: Path, project: str, services: dict, bind, *, probe_mounts: bool = True) -> list[str]:
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
    # Native Milvus standalone storage, not Milvus Lite or a simulated S3 server.
    services["milvus"] = {"image": "milvusdb/milvus:v2.6.15",
        "command": ["milvus", "run", "standalone"],
        "environment": {"DEPLOY_MODE": "STANDALONE", "ETCD_USE_EMBED": "true",
                        "ETCD_DATA_DIR": "/var/lib/milvus/etcd", "ETCD_CONFIG_PATH": "/milvus/configs/embedEtcd.yaml",
                        "COMMON_STORAGETYPE": "local"},
        "volumes": ["hybrid_milvus:/var/lib/milvus",
                    bind(root / "deploy/retrieval/embed-etcd-e2e.yaml", "/milvus/configs/embedEtcd.yaml"),
                    bind(root / "deploy/retrieval/milvus-e2e.yaml", "/milvus/configs/user.yaml")],
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
    if probe_mounts:
        services["agent"]["volumes"].extend([
            bind(root / "tools/shop_e2e", "/opt/shop_e2e"),
            bind(state / "accounts.json", "/run/secrets/test_accounts")])
    return [models, "hybrid_milvus"]
