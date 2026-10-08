"""Run the exact external-file entrypoint with synthetic modules and no service calls.

These tests prove Python import/bootstrap behavior only. The hosted --hybrid run
checks imports against the real pinned Agent image before starting any policy stage.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from tools.shop_e2e import hybrid

ROOT = Path(__file__).resolve().parents[3]


def image_layout(tmp_path):
    app = tmp_path / "app"
    (app / "shop_agent_stack").mkdir(parents=True)
    (app / "tests").mkdir()
    (app / "shop_agent_stack/__init__.py").write_text("")
    # Each call would fail immediately: preflight must only resolve imports.
    (app / "shop_agent_stack/tools.py").write_text("def connect(*args): raise AssertionError('network forbidden')\n")
    (app / "shop_agent_stack/business.py").write_text("def identity(*args): raise AssertionError('network forbidden')\n")
    (app / "tests/test_mcp_integration.py").write_text("def customer(*args): raise AssertionError('mutation forbidden')\n")
    # Keep this regression independent of whichever HTTPX version is on the host.
    (app / "httpx.py").write_text("class AsyncClient:\n def __init__(self,*args): raise AssertionError('network forbidden')\n")
    probe = tmp_path / "mounted/hybrid_probe.py"
    probe.parent.mkdir()
    shutil.copyfile(ROOT / "tools/shop_e2e/hybrid_probe.py", probe)
    return app, probe


def invoke(app, probe, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "SHOP_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, str(probe), *args], cwd=app, env=env,
                          text=True, capture_output=True, timeout=10)


def test_external_probe_imports_image_package_without_pythonpath(tmp_path):
    app, probe = image_layout(tmp_path)
    result = invoke(app, probe, "check-imports")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"imports_verified": True}
    # There are no /run/secrets or runtime state files in this process layout.
    assert not (tmp_path / "m13c-probe.json").exists()


def test_old_absolute_script_import_error_is_reproduced(tmp_path):
    app, probe = image_layout(tmp_path)
    original = probe.with_name("old_layout.py")
    original.write_text("import sys\nsys.path.insert(0,'" + str(app / "tests") + "')\nfrom shop_agent_stack import tools\n")
    result = invoke(app, original)
    assert result.returncode != 0
    assert "ModuleNotFoundError: No module named 'shop_agent_stack'" in result.stderr


@pytest.mark.parametrize("missing", ["shop_agent_stack/__init__.py", "tests/test_mcp_integration.py"])
def test_incomplete_image_layout_fails_closed(tmp_path, missing):
    app, probe = image_layout(tmp_path)
    (app / missing).unlink()
    result = invoke(app, probe, "check-imports")
    assert result.returncode != 0
    assert "probe requires the Agent image working directory" in result.stderr
    assert "imports_verified" not in result.stdout


def test_wrong_working_directory_not_silently_adopted(tmp_path):
    app, probe = image_layout(tmp_path)
    result = invoke(probe.parent, probe, "check-imports")
    assert result.returncode != 0
    assert "probe requires the Agent image working directory" in result.stderr


@pytest.mark.parametrize("response", [{}, {"imports_verified": False}, {"imports_verified": 1}, {"imports_verified": True, "unchecked": True}])
def test_import_preflight_required_before_policy_mutation(tmp_path, response):
    calls = []
    def command(state, name, argv, **kw):
        calls.append((name, argv, kw))
        return json.dumps(response)
    with pytest.raises(ValueError, match="preflight did not execute"):
        hybrid.collect(tmp_path, ["docker", "compose"], command, ROOT)
    assert len(calls) == 1
    name, argv, kw = calls[0]
    assert name == "hybrid-import-preflight"
    assert argv[-8:] == ["exec", "-T", "-w", "/app", "agent", "python", "/opt/shop_e2e/hybrid_probe.py", "check-imports"]
    assert kw["timeout"] == 30
