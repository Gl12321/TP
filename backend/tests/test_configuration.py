import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.infrastructure.config import Settings, load_settings


class ConfigurationTests(unittest.TestCase):
    def load(self, text, environment=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(text, encoding="utf-8")
            with patch.dict(
                os.environ, {"APP_CONFIG_PATH": str(path), **(environment or {})}, clear=True
            ):
                return load_settings()

    def test_yaml_and_explicit_environment_override(self):
        settings = self.load(
            "application:\n  secure_cookies: false\n  source_hosts: [reporting.local]\n  session_ttl_seconds: 300\nsettings:\n  SQL_TIMEOUT_MS: 4000\n  SQL_LOCK_TIMEOUT_MS: 800\n",
            {"APP_SECURE_COOKIES": "true", "APP_SQL_TIMEOUT_MS": "5000"},
        )
        self.assertTrue(settings.secure_cookies)
        self.assertEqual(settings.source_hosts, ("reporting.local",))
        self.assertEqual(settings.sql_timeout_ms, 5000)
        self.assertEqual(settings.sql_lock_timeout_ms, 800)
        self.assertEqual(settings.session_ttl_seconds, 300)

    def test_invalid_or_unknown_configuration_is_rejected(self):
        for text, environment in (
            ("application:\n  secure_cookies: 'false'", {}),
            ("application:\n  secure_cookie: false", {}),
            ("settings:\n  SQL_TIMOUT_MS: 50", {}),
            ("application:\n  allowed_origins: ['https://example.org/path']", {}),
            ("application:\n  source_hosts: ['http://reporting.local']", {}),
            ("application:\n  job_lease_seconds: 0", {}),
            ("{}", {"APP_SECURE_COOKIES": "flase"}),
        ):
            with self.subTest(text=text, environment=environment), self.assertRaises(ValueError):
                self.load(text, environment)

    def test_secret_and_bootstrap_token_are_required_at_startup(self):
        with self.assertRaises(ValueError):
            Settings(secret_key="x" * 40, bootstrap_token="").validate()
        with self.assertRaises(ValueError):
            Settings(secret_key="short", bootstrap_token="token").validate()

    def test_passwords_are_not_silently_stripped(self):
        from backend.app.access.schemas import Login
        from backend.app.sources.schemas import SourceCreate

        self.assertEqual(
            Login(email=" a@example.org ", password=" password ").password, " password "
        )
        source = SourceCreate(
            name="Source",
            host="database.local",
            database="reporting",
            username="reader",
            password=" database password ",
            schemas=["reporting"],
        )
        self.assertEqual(source.password, " database password ")

    def test_worker_uses_launcher_model_preset(self):
        from backend.app.assistant.engine import LocalEngine

        config = {"model": "qwen3.5-9b", "settings": {}}
        models = {
            "embedder": {},
            "reranker": {},
            "llm": {"params": {"model_path": "selected.gguf"}},
        }
        with (
            patch.dict(os.environ, {"MODEL_PRESET": "qwen3.5-27b"}, clear=True),
            patch("runtime.config.load_config", return_value=config),
            patch("runtime.config.resolve_models", return_value=models) as resolve,
            patch("sql_agent.adapters.embedding.TableEmbedder"),
            patch("sql_agent.adapters.llama.LLMClient"),
            patch("sql_agent.adapters.reranker.TableReranker"),
            patch("sql_agent.retrieval.semantic.SemanticRetriever"),
        ):
            engine = LocalEngine(Settings())
            self.assertEqual(engine.model, "qwen3.5-27b")
            self.assertEqual(resolve.call_args.args[1], "qwen3.5-27b")


if __name__ == "__main__":
    unittest.main()
