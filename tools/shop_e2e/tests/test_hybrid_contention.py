"""Guard the real Hybrid worker's critical-section budget structurally.

The separate Hybrid E2E executes actual BGE/Milvus search and measures it.
These static tests prevent regressions that inadvertently put slow embedding
or Milvus indexing back under the exclusive search lock.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def procedures():
    src=(ROOT / "services/retrieval/online.py").read_text()
    module=ast.parse(src)
    return {f.name:f for f in module.body if isinstance(f,(ast.FunctionDef,ast.AsyncFunctionDef))}


def is_lock_with(node):
    return isinstance(node, ast.With) and any(
        isinstance(item.context_expr, ast.Name) and item.context_expr.id == "LOCK"
        for item in node.items)


def test_real_worker_does_not_encode_or_upsert_inside_query_mutex():
    worker=procedures()["worker"]
    protected=[node for node in ast.walk(worker) if is_lock_with(node)]
    assert len(protected)>=2
    def expensive(node):
        return isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and (
            node.func.attr in ("encode","create_collection","upsert","flush",
                               "load_collection","list_collections","drop_collection"))
    all_expensive=[n for n in ast.walk(worker) if expensive(n)]
    assert len(all_expensive)>=6
    for lock in protected:
        assert not any(expensive(n) for n in ast.walk(lock)), (
            "A slow index build or collection cleanup must not hold LOCK")


def test_search_admission_is_explicitly_bounded_not_no_wait_forever():
    search=procedures()["search"]
    waits=[n for n in ast.walk(search) if isinstance(n,ast.Call) and
           isinstance(n.func,ast.Attribute) and n.func.attr=="acquire"]
    assert len(waits)==1
    kwargs={arg.arg:arg.value for arg in waits[0].keywords}
    assert "timeout" in kwargs
    assert isinstance(kwargs["timeout"],ast.Constant)
    assert 0<kwargs["timeout"].value<=0.5
