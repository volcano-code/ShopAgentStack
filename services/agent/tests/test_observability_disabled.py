"""Default service remains importable even without any OpenTelemetry installation."""
import os
from pathlib import Path
import subprocess
import sys


def test_disabled_import_and_provider_call_need_no_otel():
    service = Path(__file__).resolve().parents[1]
    program = r'''
import asyncio, importlib.abc, sys
class BlockOTel(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "opentelemetry" or fullname.startswith("opentelemetry."):
            raise ImportError("OpenTelemetry is intentionally unavailable in this test")
sys.meta_path.insert(0, BlockOTel())
from shop_agent_stack import observability, providers
assert observability.Runtime.from_env({}).telemetry is None
assert observability.Runtime.from_env({"SHOP_AGENT_STACK_OTEL_ENABLED":"false"}).telemetry is None
async def run():
    try:
        await providers.complete("fixture", [{"role":"user","content":"synthetic"}], [])
    except ValueError as exc:
        assert "测试引擎未启用" in str(exc)
    else:
        raise AssertionError("fixture must not become a live-model fallback")
asyncio.run(run())
assert not any(k.startswith("opentelemetry") for k in sys.modules)
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith(("SHOP_AGENT_STACK_", "OTEL_"))}
    env["PYTHONPATH"] = str(service)
    result = subprocess.run([sys.executable, "-c", program], cwd=service,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
