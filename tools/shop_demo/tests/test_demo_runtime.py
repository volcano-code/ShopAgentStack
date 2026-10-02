"""Deterministic Docker command doubles exercise guardrails; hosted smoke is separate."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from tools.shop_demo import runtime as r
from tools.shop_demo import state as s
from tools.shop_demo import __main__ as cli


class FakeDocker:
    def __init__(self, owner, spec):
        self.owner, self.spec = owner, spec
        self.current = {k: [] for k in ("container", "network", "volume")}
        self.calls = []
        self.fail = None
        self.endpoint = "unix:///var/run/docker.sock"

    def __call__(self, state, stage, args, **kwargs):
        self.calls.append((stage, args))
        if stage == self.fail:
            raise r.DemoError("synthetic command failure")
        if stage == "docker-context":
            return json.dumps([{"Endpoints": {"docker": {"Host": self.endpoint}}}])
        if stage.startswith("list-"):
            kind = stage[5:]
            return " ".join(str(i) for i in range(len(self.current[kind])))
        if stage.startswith("inspect-"):
            return json.dumps(self.current[stage[8:]])
        project = self.owner["project"]
        labels = {s.LABEL: project, "com.docker.compose.project": project}
        if stage in ("prepare-containers", "start"):
            if not self.current["volume"]:
                self.current["volume"] = [{"Name": project + "_" + name, "CreatedAt": "synthetic-time",
                    "Labels": labels.copy()} for name in self.spec["volumes"]]
            self.current["container"] = [{"Config": {"Labels": labels | {"com.docker.compose.service": name}},
                "State": {"Status": "running" if stage == "start" else "created",
                          "Running": stage == "start", "Health": {"Status": "healthy"}}}
                for name, spec in self.spec["services"].items() if not spec.get("profiles")]
        if stage == "hybrid-ready":
            return json.dumps({"ready": True, "generation": "synthetic-only", "epoch": 1})
        if stage == "stop-retain-data":
            for item in self.current["container"]:
                item["State"].update(Status="exited", Running=False)
        if stage == "destroy-confirmed-demo":
            self.current = {k: [] for k in self.current}
        return ""


class Health:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    status = 200


@pytest.fixture
def demo(checkout, options, monkeypatch):
    state = checkout / ".local/demo"
    owner = s.init(checkout, state, options)
    _, spec = s.load(checkout, state)
    docker = FakeDocker(owner, spec)
    monkeypatch.setattr(r, "ROOT", checkout)
    monkeypatch.setattr(r, "command", docker)
    monkeypatch.setattr(r.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **kw: Health()))
    return state, owner, docker


def test_start_stop_resume_and_idempotency(demo):
    state, _, docker = demo
    assert r.up(state)["ready"]
    before = deepcopy(docker.current["volume"])
    assert r.stop(state)["volumes_retained"]
    assert not r.status(state)["ready"]
    assert r.up(state)["ready"]
    assert r.up(state)["ready"]
    assert docker.current["volume"] == before
    assert [stage for stage, _ in docker.calls].count("build-agent") == 1
    assert not any("down" in args for _, args in docker.calls)


@pytest.mark.parametrize("mode", ("missing", "replaced", "foreign"))
def test_lost_or_foreign_volume_prevents_start(demo, mode):
    state, _, docker = demo
    r.up(state)
    if mode == "missing":
        docker.current["volume"].pop()
    elif mode == "replaced":
        docker.current["volume"][0]["CreatedAt"] = "new-time"
    else:
        docker.current["volume"][0]["Labels"].pop(s.LABEL)
    before = len(docker.calls)
    with pytest.raises(ValueError):
        r.up(state)
    assert "start" not in [stage for stage, _ in docker.calls[before:]]


@pytest.mark.parametrize("action", ("stop", "destroy"))
def test_foreign_container_never_stopped_or_deleted(demo, action):
    state, owner, docker = demo
    r.up(state)
    docker.current["container"][0]["Config"]["Labels"][s.LABEL] = "someone-else"
    before = len(docker.calls)
    with pytest.raises(ValueError):
        r.stop(state) if action == "stop" else r.destroy(state, owner["project"])
    assert not any(stage in ("stop-retain-data", "destroy-confirmed-demo") for stage, _ in docker.calls[before:])


def test_new_state_does_not_adopt_existing_resources(demo):
    state, owner, docker = demo
    docker.current["volume"] = [{"Name": "collision", "CreatedAt": "now", "Labels": {
        s.LABEL: owner["project"], "com.docker.compose.project": owner["project"]}}]
    with pytest.raises(ValueError):
        r.up(state)
    assert not any(stage == "start" for stage, _ in docker.calls)


def test_destroy_requires_exact_confirmation_and_terminal_state(demo):
    state, owner, docker = demo
    r.up(state)
    with pytest.raises(ValueError):
        r.destroy(state, "yes")
    assert docker.current["volume"]
    assert r.destroy(state, owner["project"])["destroyed"]
    assert all(not items for items in docker.current.values())
    assert (state / "accounts.json").exists()
    with pytest.raises(ValueError):
        r.up(state)


def test_failed_start_does_not_erase_data_or_lock(demo):
    state, _, docker = demo
    r.up(state)
    before = deepcopy(docker.current["volume"])
    docker.fail = "start"
    with pytest.raises(r.DemoError):
        r.up(state)
    assert docker.current["volume"] == before
    assert not (state / "command.lock").exists()
    assert not any(stage == "destroy-confirmed-demo" for stage, _ in docker.calls)


def test_remote_docker_endpoint_refused(demo):
    state, _, docker = demo
    docker.endpoint = "tcp://external.invalid:2376"
    with pytest.raises(ValueError):
        r.up(state)
    assert [v[0] for v in docker.calls] == ["docker-context"]


def test_environment_does_not_inherit_other_deployment(monkeypatch):
    for name in ("SHOP_AGENT_STACK_DB_PASSWORD", "SHOP_E2E_URL", "SHOP_DEMO_STATE", "COMPOSE_FILE", "DOCKER_HOST"):
        monkeypatch.setenv(name, "must-not-leak")
    monkeypatch.setenv("PATH", "example-path")
    result = r.environment()
    assert "must-not-leak" not in result.values()
    assert result["PATH"] == "example-path"


def test_default_cli_never_prints_generated_passwords(checkout, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", checkout)
    state = checkout / ".local/demo"
    assert cli.main(["init", "--state", str(state)]) == 0
    output = capsys.readouterr()
    accounts = json.loads((state / "accounts.json").read_text())
    assert all(v["password"] not in output.out + output.err for v in accounts)
    assert "credentials_file" in output.out


def test_cli_failure_is_not_success(checkout, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", checkout)
    assert cli.main(["init", "--state", str(checkout / ".local/demo"), "--port", "80"]) == 2


def test_hybrid_start_waits_for_index_not_just_alive(checkout, options, monkeypatch):
    state = checkout / ".local/hybrid"
    owner = s.init(checkout, state, options | {"retrieval": "hybrid"})
    _, spec = s.load(checkout, state)
    docker = FakeDocker(owner, spec)
    monkeypatch.setattr(r, "ROOT", checkout)
    monkeypatch.setattr(r, "command", docker)
    monkeypatch.setattr(r.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **kw: Health()))
    assert r.up(state)["hybrid"]["ready"]
    stages = [name for name, _ in docker.calls]
    assert stages.index("prefetch-public-models") < stages.index("start") < stages.index("hybrid-ready")
    assert "worker.get('ready')" in r.HYBRID_READY and "job.get('status') == 'READY'" in r.HYBRID_READY


def test_first_health_failure_binds_volumes_before_business_start(demo):
    state, _, docker = demo
    docker.fail = "start"
    with pytest.raises(r.DemoError):
        r.up(state)
    life = json.loads((state / "lifecycle.json").read_text())
    assert life["phase"] == "starting"
    assert life["volumes"] == r.volume_identity(docker.current["volume"])
    assert life["volumes"]
    assert all(v["State"]["Status"] == "created" for v in docker.current["container"])
    docker.fail = None
    assert r.up(state)["ready"]
    assert [stage for stage, _ in docker.calls].count("prepare-containers") == 1


@pytest.mark.parametrize("change", ("missing", "replaced"))
def test_first_start_failure_cannot_silently_reinitialize_storage(demo, change):
    state, _, docker = demo
    docker.fail = "start"
    with pytest.raises(r.DemoError):
        r.up(state)
    if change == "missing":
        docker.current["volume"].pop()
    else:
        docker.current["volume"][0]["CreatedAt"] = "recreated-after-failure"
    docker.fail = None
    before = len(docker.calls)
    with pytest.raises(ValueError):
        r.up(state)
    assert not any(stage in ("prepare-containers", "start") for stage, _ in docker.calls[before:])


def test_interrupted_unbound_state_is_not_adopted(demo):
    state, _, docker = demo
    r.up(state)
    s._atomic(state / "lifecycle.json", {"phase": "starting", "volumes": None})
    before = len(docker.calls)
    with pytest.raises(ValueError, match="unbound"):
        r.up(state)
    assert not any(stage in ("prepare-containers", "start", "build-agent") for stage, _ in docker.calls[before:])


@pytest.mark.parametrize("action", ("status", "stop", "destroy"))
def test_every_lifecycle_command_refuses_remote_context(demo, action):
    state, owner, docker = demo
    r.up(state)
    docker.endpoint = "tcp://example.invalid:2376"
    before = len(docker.calls)
    with pytest.raises(ValueError, match="local Docker"):
        r.destroy(state, owner["project"]) if action == "destroy" else getattr(r, action)(state)
    assert [stage for stage, _ in docker.calls[before:]] == ["docker-context"]
