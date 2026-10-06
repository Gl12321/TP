import hashlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import run
from runtime import models


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(run.shutil, "which", lambda name: "docker")
    process = Mock(return_value=SimpleNamespace(returncode=0, stdout="linux"))
    monkeypatch.setattr(run.subprocess, "run", process)
    return tmp_path, process


def compose_calls(process):
    return [
        call.args[0][call.args[0].index("-f") + 2 :]
        for call in process.call_args_list
        if call.args[0][:2] == ["docker", "compose"] and "-f" in call.args[0]
    ]


def test_host_check_reads_docker_without_creating_runtime(launcher, monkeypatch, subtests):
    root, process = launcher
    for outcome in ("linux", "windows", "missing", "stopped"):
        with subtests.test(outcome=outcome):
            monkeypatch.setattr(
                run.shutil, "which", lambda name: None if outcome == "missing" else "docker"
            )
            process.side_effect = None
            process.return_value = SimpleNamespace(stdout=outcome)
            if outcome == "stopped":
                process.side_effect = subprocess.CalledProcessError(1, ["docker", "info"])
            assert run.main(["--check"]) == (0 if outcome == "linux" else 1)
            assert not (root / ".runtime").exists()
    assert all(call.args[0][1] in {"compose", "info"} for call in process.call_args_list)


def test_restart_preserves_secrets_and_without_ai_never_prepares_models(launcher):
    root, process = launcher
    assert run.main(["--without-ai"]) == 0
    credentials = root / ".runtime/compose.env"
    original = credentials.read_bytes()
    assert run.main(["--without-ai"]) == 0
    assert credentials.read_bytes() == original
    calls = compose_calls(process)
    migrate = ["run", "--rm", "--no-deps", "migrate"]
    api = ["up", "--detach", "--wait", "--wait-timeout", "120", "api"]
    assert calls.index(migrate) < calls.index(api)
    assert ["run", "--rm", "--no-deps", "prepare"] not in calls
    assert not any(call[0] == "up" and "worker" in call for call in calls)
    process.reset_mock()
    assert run.main(["--stop"]) == 0
    assert compose_calls(process)[-1] == ["down", "--remove-orphans"]
    assert credentials.read_bytes() == original
    recovered = root / "recovered.env"
    run.credentials(recovered)
    restored = recovered.read_bytes()
    process.reset_mock()
    assert run.main(["--env-file", str(recovered), "--without-ai"]) == 0
    assert recovered.read_bytes() == restored
    for call in process.call_args_list:
        command = call.args[0]
        if "--env-file" in command:
            assert run.Path(command[command.index("--env-file") + 1]).samefile(recovered)


@pytest.mark.parametrize("stage", ["--check", "migrate"])
def test_failed_preparation_does_not_start_application(launcher, stage):
    _, process = launcher

    def fail(command, **kwargs):
        if command[-1] == stage:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0, stdout="linux")

    process.side_effect = fail
    assert run.main([]) == 1
    assert not any(
        call[0] == "up" and call[-1] in {"api", "worker"} for call in compose_calls(process)
    )


def test_model_integrity_and_reuse_without_network(tmp_path, monkeypatch):
    path = tmp_path / "model.gguf"
    content = b"GGUF" + bytes(range(64))
    config = {
        "repo_id": "fixture/model",
        "revision": "1" * 40,
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "params": {"model_path": str(path)},
    }
    path.write_bytes(content)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    models.ensure_llm(config)
    assert path.with_suffix(".ready.json").exists()
    path.write_bytes(content[:-1] + b"!")
    download = Mock(return_value=str(path))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    with pytest.raises(ValueError):
        models.ensure_llm(config)
    identity = {key: config[key] for key in ("repo_id", "revision", "sha256", "size_bytes")}
    assert not models.ready(path.with_suffix(".ready.json"), identity, tmp_path)
