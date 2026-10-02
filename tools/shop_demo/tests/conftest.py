from pathlib import Path
import pytest


@pytest.fixture
def checkout(tmp_path: Path):
    root = tmp_path / "checkout"
    files = {
        "services/commerce/mall-portal/target/mall-portal-1.0-SNAPSHOT.jar": "jar-placeholder-portal",
        "services/commerce/mall-admin/target/mall-admin-1.0-SNAPSHOT.jar": "jar-placeholder-admin",
        "apps/web/dist/index.html": "<html>synthetic unit input, not a built application</html>",
        "apps/web/dist/assets/main.js": "console.log('test');",
        "evaluation/retrieval-matrix.json": "{}",
        "deploy/nginx/shop_agent_stack-p2.conf": "server {}",
        "deploy/config/application-shop_agent_stack.yml": "x: unit",
        "deploy/retrieval/embed-etcd-e2e.yaml": "x: unit",
        "deploy/retrieval/milvus-e2e.yaml": "x: unit",
        "services/agent/Dockerfile": "FROM unit-only",
        "services/agent/requirements.lock": "unit-only",
        "services/agent/requirements-observability.txt": "unit-only",
        "services/agent/shop_agent_stack/__init__.py": "",
        "services/agent/tests/test_example.py": "",
        "services/retrieval/Dockerfile": "FROM unit-only",
        "services/retrieval/requirements.lock": "unit-only",
        "services/retrieval/online.py": "",
    }
    for i in range(10):
        files[f"deploy/mysql/init/{i:02d}-test.sql"] = "SELECT 1;"
    for name, value in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    return root


@pytest.fixture
def options():
    return {"retrieval": "bm25", "port": 18030, "fixture": False, "model_network": False}
