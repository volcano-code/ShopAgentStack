"""Pure file/configuration tests; not evidence of running Docker or live models."""
import json
from pathlib import Path
import stat
import pytest
from tools.shop_demo import state as s
from tools.shop_e2e.stack import compose as e2e_compose


def test_private_snapshot_and_no_inherited_files(checkout, options):
    (checkout / ".env").write_text("THIS_MUST_NOT_BE_IMPORTED=yes")
    state = checkout / ".local/demo"
    owner = s.init(checkout, state, options)
    loaded, spec = s.load(checkout, state)
    assert owner == loaded
    assert spec["name"].startswith("shop-demo-")
    assert "THIS_MUST_NOT" not in (state / ".env").read_text()
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / "accounts.json").stat().st_mode) == 0o600
    assert all(v["labels"] == {s.LABEL: owner["project"]} for v in spec["services"].values())
    assert spec["services"]["agent"]["environment"]["SHOP_AGENT_STACK_ENABLE_TEST_PROVIDER"] == "false"
    assert spec["services"]["agent"]["networks"] == ["business"]
    assert spec["services"]["web"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert len(spec["volumes"]) == 5
    assert spec["services"]["redis"]["command"][-2:] == ["--appendonly", "yes"]
    assert len(list((state / "init").glob("*.sql"))) == 10


def test_snapshot_is_independent_of_checkout_changes(checkout, options):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    before = s.sha(state / "runtime/apps/web/dist/index.html")
    (checkout / "apps/web/dist/index.html").write_text("new build")
    s.load(checkout, state)
    assert s.sha(state / "runtime/apps/web/dist/index.html") == before


@pytest.mark.parametrize("name", ["compose.json", "owner.json", ".env", "agent-key", "accounts.json",
    "runtime/apps/web/dist/index.html", "init/000-00-test.sql"])
def test_changed_snapshot_fails_closed(checkout, options, name):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    (state / name).write_text("changed")
    with pytest.raises(ValueError):
        s.load(checkout, state)


def test_new_unmanifested_file_is_rejected(checkout, options):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    (state / "runtime/apps/web/dist/injected.html").write_text("untracked")
    with pytest.raises(ValueError):
        s.load(checkout, state)


def test_repeated_init_preserves_secret(checkout, options):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    before = (state / ".env").read_bytes()
    with pytest.raises(FileExistsError):
        s.init(checkout, state, options)
    assert (state / ".env").read_bytes() == before


@pytest.mark.parametrize("change", [{"retrieval": "fake"}, {"port": 80}, {"port": 65536}, {"port": True},
    {"fixture": "true"}, {"model_network": 1}, {"fixture": True, "model_network": True}, {"typo": False}])
def test_invalid_init_has_no_side_effect(checkout, options, change):
    state = checkout / ".local/demo"
    with pytest.raises(ValueError):
        s.init(checkout, state, options | change)
    assert not state.exists()


def test_state_outside_local_refused(checkout, options):
    with pytest.raises(ValueError):
        s.init(checkout, checkout.parent / "outside", options)


def test_state_symlink_refused(checkout, options):
    (checkout / ".local").mkdir()
    (checkout / ".local/link").symlink_to(checkout.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        s.init(checkout, checkout / ".local/link/demo", options)


def test_snapshot_symlink_refused(checkout, options):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    (state / "agent-key").unlink()
    (state / "agent-key").symlink_to(state / ".env")
    with pytest.raises(ValueError):
        s.load(checkout, state)


def test_missing_build_refused_before_init(checkout, options):
    (checkout / "apps/web/dist/index.html").unlink()
    state = checkout / ".local/demo"
    with pytest.raises(ValueError):
        s.init(checkout, state, options)
    assert not state.exists()


def test_source_symlink_refused(checkout, options):
    source = checkout / "apps/web/dist/assets/main.js"
    source.unlink()
    source.symlink_to(checkout / "apps/web/dist/index.html")
    with pytest.raises(ValueError):
        s.init(checkout, checkout / ".local/demo", options)


def test_hybrid_uses_native_storage_no_test_account_mount(checkout, options):
    state = checkout / ".local/hybrid"
    owner = s.init(checkout, state, options | {"retrieval": "hybrid"})
    _, spec = s.load(checkout, state)
    services = spec["services"]
    assert "minio" not in services and "milvus" in services
    assert "v2.6.15" in services["milvus"]["image"]
    assert services["milvus"]["environment"]["COMMON_STORAGETYPE"] == "local"
    assert services["retrieval-worker"]["environment"]["HF_HUB_OFFLINE"] == "1"
    text = json.dumps(spec)
    assert "test_accounts" not in text and "/opt/shop_e2e" not in text
    assert "/workspace/tools/shop_e2e" not in text
    assert "accounts.json" not in text
    assert services["model-prefetch"]["networks"] == ["edge"]
    assert all("key" not in str(v) for v in services["model-prefetch"]["volumes"])
    assert owner["options"]["fixture"] is False


@pytest.mark.parametrize("mode", ("fixture", "model_network"))
def test_only_explicit_mode_is_enabled(checkout, options, mode):
    state = checkout / ".local/demo"
    s.init(checkout, state, options | {mode: True})
    _, spec = s.load(checkout, state)
    agent = spec["services"]["agent"]
    assert (agent["environment"]["SHOP_AGENT_STACK_ENABLE_TEST_PROVIDER"] == "true") == (mode == "fixture")
    assert ("model-egress" in agent["networks"]) == (mode == "model_network")
    assert "accounts.json" not in json.dumps(agent)


def test_demo_cannot_adopt_e2e_namespace(checkout, options):
    state = checkout / ".local/demo"
    with pytest.raises(ValueError):
        s.layout(state, {"project": "shop-e2e-" + "a" * 32, "options": options})
    with pytest.raises(ValueError):
        e2e_compose(checkout, state, "shop-demo-" + "a" * 32)


@pytest.mark.parametrize("name", ("../../outside", "/tmp/outside", r"..\outside"))
def test_manifest_traversal_rejected(checkout, options, name):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    manifest = json.loads((state / "manifest.json").read_text())
    manifest["files"][name] = "0" * 64
    (state / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        s.load(checkout, state)


@pytest.mark.parametrize("name", (".env", "accounts.json", ""))
def test_loosened_secret_permissions_rejected(checkout, options, name):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    (state / name).chmod(0o777)
    with pytest.raises(ValueError):
        s.load(checkout, state)


def test_concurrent_commands_are_rejected_and_lock_released(checkout, options):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    with pytest.raises(RuntimeError):
        with s.locked(checkout, state):
            with pytest.raises(FileExistsError):
                with s.locked(checkout, state):
                    pass
            raise RuntimeError("unit failure")
    assert not (state / "command.lock").exists()


@pytest.mark.parametrize("value", ([], None, "invalid"))
def test_malformed_manifest_type_is_controlled(checkout, options, value):
    state = checkout / ".local/demo"
    s.init(checkout, state, options)
    (state / "manifest.json").write_text(json.dumps(value))
    with pytest.raises(ValueError):
        s.load(checkout, state)


def test_shared_local_directory_is_not_a_demo(checkout, options):
    with pytest.raises(ValueError, match="dedicated demo subdirectory"):
        s.init(checkout, checkout / ".local", options)
    assert not (checkout / ".local").exists()
