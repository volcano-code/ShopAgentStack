"""Independent real Milvus readback in the isolated retrieval container; no encoders mocked."""
import argparse
import json
from pathlib import Path

EXPERIMENTAL = "shop_agent_stack_eval_m13c_sentinel"


def main():
    from pymilvus import MilvusClient
    import httpx
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "readback"])
    action = parser.parse_args().action
    client = MilvusClient(uri="http://milvus:19530", timeout=10)
    try:
        if action == "seed":
            if client.has_collection(EXPERIMENTAL):
                raise ValueError("sentinel collection already exists")
            client.create_collection(collection_name=EXPERIMENTAL, dimension=2, metric_type="COSINE", consistency_level="Strong")
            client.insert(collection_name=EXPERIMENTAL, data=[{"id": 9000001, "vector": [1.0, 0.0]}])
            client.flush(collection_name=EXPERIMENTAL)
            print(json.dumps({"experimental_created": True}))
            return
        key = Path("/run/secrets/index_key").read_text().strip()
        with httpx.Client(trust_env=False, timeout=10) as http:
            r = http.get("http://retrieval-worker:8020/status", headers={"X-ShopAgentStack-Index": key})
            r.raise_for_status()
            state = r.json()
        if not state.get("ready"):
            raise ValueError("worker is not ready at readback")
        collection = state["collection"]
        collections = sorted(client.list_collections())
        if collections != sorted([collection, EXPERIMENTAL]):
            raise ValueError("old live generation retained or experimental collection deleted")
        stats = client.get_collection_stats(collection_name=collection)
        rows = client.query(collection_name=collection, filter="", output_fields=["count(*)"], consistency_level="Strong")
        count = int(rows[0]["count(*)"])
        if count != int(stats["row_count"]) or count != state["clauses"]:
            raise ValueError("Milvus/worker row count mismatch")
        sentinel = client.get(collection_name=EXPERIMENTAL, ids=[9000001], output_fields=["id"])
        if len(sentinel) != 1 or sentinel[0]["id"] != 9000001:
            raise ValueError("experimental collection was mutated")
        manifest = json.loads(Path("/models/m13c-model-manifest.json").read_text())
        print(json.dumps({"verified": True, "collection": collection, "count": count,
                          "epoch": state["epoch"], "digest": state["digest"],
                          "collections": collections, "experimental_preserved": True,
                          "model_manifest": manifest}))
    finally:
        client.close()


if __name__ == "__main__":
    main()
