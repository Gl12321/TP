from types import SimpleNamespace
from unittest.mock import Mock

from tools import check


def test_postgres_without_explicit_database_never_starts_tests(monkeypatch):
    monkeypatch.delenv("RAZBOR_TEST_DATABASE_URL", raising=False)
    execute = Mock()
    monkeypatch.setattr(check.subprocess, "run", execute)
    assert check.main(["postgres"]) == 2
    execute.assert_not_called()


def test_first_failure_stops_remaining_suites(monkeypatch):
    monkeypatch.setattr(check, "preflight", lambda *args: None)
    execute = Mock(return_value=SimpleNamespace(returncode=7))
    monkeypatch.setattr(check.subprocess, "run", execute)
    assert check.main(["quick"]) == 7
    assert execute.call_count == 1
