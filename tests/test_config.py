from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


AVAILABLE = all(importlib.util.find_spec(name) for name in ("yaml", "pydantic_settings"))


@unittest.skipUnless(AVAILABLE, "Нужны PyYAML и pydantic-settings")
class ConfigTests(unittest.TestCase):
    def setUp(self):
        from src.core.config import Settings
        from src.core.presets import load_config, resolve_models

        self.Settings = Settings
        self.load_config = load_config
        self.resolve_models = resolve_models
        self.config = load_config()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def settings(self, **values):
        return self.Settings(_env_file=None, DB_USER="importer", DB_PASSWORD="secret", DB_NAME="test", **values)

    def test_default_model_and_limits_come_from_yaml(self):
        settings = self.settings()
        self.assertEqual(settings.MODEL_PRESET, self.config["model"])
        self.assertEqual(settings.LLM_TIMEOUT_SECONDS, self.config["settings"]["LLM_TIMEOUT_SECONDS"])
        self.assertEqual(settings.MODELS["llm"]["repo_id"], self.config["models"][settings.MODEL_PRESET]["repo_id"])

    def test_empty_compose_model_uses_yaml_default(self):
        with patch.dict(os.environ, {"MODEL_PRESET": ""}):
            self.assertEqual(self.settings().MODEL_PRESET, self.config["model"])

    def test_environment_selects_model_and_overrides_limit(self):
        with patch.dict(os.environ, {"MODEL_PRESET": "qwen3.5-27b", "MAX_RESULT_ROWS": "17"}):
            settings = self.settings()
        self.assertEqual(settings.MODELS["llm"]["params"]["n_ctx"], 4096)
        self.assertEqual(settings.MAX_RESULT_ROWS, 17)
        self.assertGreater(settings.MODELS["llm"]["params"]["n_threads"], 0)

    def test_unknown_model_fails_with_available_names(self):
        with self.assertRaisesRegex(ValueError, "qwen3-8b"):
            self.settings(MODEL_PRESET="missing")

    def test_nonpositive_limit_fails_before_model_loading(self):
        with self.assertRaises(ValueError):
            self.settings(MAX_RESULT_ROWS=0)

    def test_unknown_yaml_setting_is_rejected(self):
        config = deepcopy(self.config)
        config["settings"]["MISSPELLED_LIMIT"] = 1
        with patch("src.core.config.load_config", return_value=config), self.assertRaisesRegex(ValueError, "MISSPELLED_LIMIT"):
            self.settings()

    def test_invalid_generation_settings_fail_during_preflight(self):
        for changes in ({"n_ctx": -1}, {"max_tokens": 8192}, {"n_batch": 9000},
                        {"temperature": float("nan")}, {"n_ubatch": 1024}, {"n_gpu_layers": -2}):
            config = deepcopy(self.config)
            config["generation"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.resolve_models(config, "qwen3-8b")

    def test_model_paths_are_relative_to_project_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = self.resolve_models(self.config, "qwen3-8b", root)
        self.assertEqual(Path(models["llm"]["params"]["model_path"]).parent, root / "models")
        self.assertEqual(Path(models["embedder"]["cache_path"]), root / "models" / "embedder")

    def test_invalid_model_hash_is_rejected(self):
        import yaml

        config = deepcopy(self.config)
        config["models"]["qwen3-8b"]["sha256"] = "missing"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.load_config(path)

    def test_invalid_retrieval_catalog_fails_before_downloading(self):
        import yaml

        changes = [
            {"revision": "main"}, {"repo_id": "missing-owner"}, {"max_length": 0},
            {"files": []}, {"files": {}}, {"files": {"config.json": "invalid"}},
            *({"files": {name: "a" * 40}} for name in
              ("../config.json", "/config.json", "C:/config.json", "a\\config.json", "a//config.json")),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for change in changes:
                config = deepcopy(self.config)
                config["retrieval_models"]["embedder"].update(change)
                path.write_text(yaml.safe_dump(config), encoding="utf-8")
                with self.subTest(change=change), self.assertRaises(ValueError):
                    self.load_config(path)

    def test_malformed_yaml_has_a_readable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("model: [unfinished", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Не удалось прочитать YAML"):
                self.load_config(path)
