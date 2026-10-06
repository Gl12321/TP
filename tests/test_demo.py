import asyncio
from unittest.mock import AsyncMock

import pytest

from tools.demo import __main__ as demo
from tools.demo import database, source, state


@pytest.fixture
def files(tmp_path, monkeypatch):
    paths = state.DemoFiles(tmp_path)
    monkeypatch.setattr(state.DemoFiles, "for_scenario", lambda scenario: paths)
    monkeypatch.setattr(demo, "preflight", lambda port: None)
    return paths


def test_scenarios_and_damaged_state_preservation(files, monkeypatch):
    assert len(state.new_accounts(8766)["accounts"]) == 6
    assert [a["role"] for a in state.new_accounts(8767, "onboarding")["accounts"]] == ["director"]
    files.path("state").write_text('{"broken":true}', encoding="utf-8")
    prepare = AsyncMock()
    monkeypatch.setattr(demo, "prepare", prepare)
    assert demo.main(["--prepare-only"]) == 1
    assert files.path("state").read_text(encoding="utf-8") == '{"broken":true}'
    prepare.assert_not_called()


def test_missing_demo_dsn_and_foreign_cleanup_never_connect(files, monkeypatch):
    monkeypatch.delenv("RAZBOR_DEMO_DATABASE_URL", raising=False)
    monkeypatch.setenv("RAZBOR_TEST_DATABASE_URL", "postgresql://test:unused@127.0.0.1/test")
    connect = AsyncMock()
    monkeypatch.setattr(source.asyncpg, "connect", connect)
    assert demo.main(["--scenario", "onboarding", "--prepare-only"]) == 1
    assert not files.path("state").exists()
    generated = state.new_state("postgresql://admin:unused@127.0.0.1/demo", 8766)
    generated["database_url"] = "postgresql+asyncpg://app:unused@127.0.0.1/production"
    with pytest.raises(state.DemoError):
        asyncio.run(
            database.cleanup_application("postgresql://admin:unused@127.0.0.1/demo", generated)
        )
    connect.assert_not_called()


def test_source_cleanup_failure_preserves_resource_intent(files, monkeypatch):
    monkeypatch.setenv("RAZBOR_DEMO_DATABASE_URL", "postgresql://admin:unused@127.0.0.1/demo")
    connection = AsyncMock()
    connection.execute.side_effect = [
        None,
        RuntimeError("create failed"),
        RuntimeError("cleanup failed"),
    ]

    async def connect(url):
        assert files.path("state").is_file() and files.path("source").is_file()
        return connection

    monkeypatch.setattr(source.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        demo.prepare(8766, files, "network")
    saved, intent = state.load(files.path("state")), state.load(files.path("source"))["source"]
    assert not saved["ready"] and saved["preparation_stage"] == "source"
    assert intent["username"] in connection.execute.await_args_list[0].args[0]
    assert intent["database"] in connection.execute.await_args_list[1].args[0]


def test_onboarding_creation_failure_preserves_state_without_source(files, monkeypatch):
    monkeypatch.setenv("RAZBOR_DEMO_DATABASE_URL", "postgresql://admin:unused@127.0.0.1/demo")
    create = AsyncMock(side_effect=RuntimeError("unconfirmed cleanup"))
    seed = AsyncMock()
    monkeypatch.setattr(database, "create_application", create)
    monkeypatch.setattr(source, "seed", seed)
    with pytest.raises(RuntimeError, match="unconfirmed cleanup"):
        demo.prepare(8767, files, "onboarding")
    assert state.load(files.path("state"))["preparation_stage"] == "application"
    assert not files.path("source").exists()
    seed.assert_not_called()
