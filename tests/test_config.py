from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


AVAILABLE = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(AVAILABLE, "Нужен PyYAML")
class ConfigTests(unittest.TestCase):
    def setUp(self):
        from runtime.config import load_config, resolve_models

        self.load_config = load_config
        self.resolve_models = resolve_models
        self.config = load_config()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_default_model_and_limits_come_from_yaml(self):
        models = self.resolve_models(self.config, self.config["model"])
        self.assertEqual(
            models["llm"]["repo_id"], self.config["models"][self.config["model"]]["repo_id"]
        )
        self.assertEqual(set(self.config["models"]), {"qwen3.5-4b", "qwen3.5-9b", "qwen3.5-27b"})

    def test_empty_compose_model_uses_yaml_default(self):
        from runtime.prepare import main

        with (
            patch.dict(os.environ, {"MODEL_PRESET": ""}),
            patch("runtime.prepare.ensure_models") as prepare,
        ):
            self.assertEqual(main([]), 0)
        self.assertEqual(
            prepare.call_args.args[0]["llm"]["repo_id"],
            self.config["models"][self.config["model"]]["repo_id"],
        )

    def test_environment_selects_model_without_downloading_during_preflight(self):
        from runtime.prepare import main

        with (
            patch.dict(os.environ, {"MODEL_PRESET": "qwen3.5-27b"}),
            patch("runtime.prepare.ensure_models") as prepare,
        ):
            self.assertEqual(main(["--check"]), 0)
        prepare.assert_not_called()
        models = self.resolve_models(self.config, "qwen3.5-27b")
        self.assertEqual(models["llm"]["params"]["n_ctx"], 4096)
        self.assertGreater(models["llm"]["params"]["n_threads"], 0)

    def test_unknown_model_fails_with_available_names(self):
        with self.assertRaisesRegex(ValueError, "qwen3.5-4b"):
            self.resolve_models(self.config, "missing")

    def test_invalid_generation_settings_fail_during_preflight(self):
        for changes in (
            {"n_ctx": -1},
            {"max_tokens": 8192},
            {"n_batch": 9000},
            {"temperature": float("nan")},
            {"n_ubatch": 1024},
            {"n_gpu_layers": -2},
        ):
            config = deepcopy(self.config)
            config["generation"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.resolve_models(config, "qwen3.5-4b")

    def test_model_paths_are_relative_to_project_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = self.resolve_models(self.config, "qwen3.5-4b", root)
        self.assertEqual(Path(models["llm"]["params"]["model_path"]).parent, root / "models")
        self.assertEqual(Path(models["embedder"]["cache_path"]), root / "models" / "embedder")

    def test_invalid_model_hash_is_rejected(self):
        import yaml

        config = deepcopy(self.config)
        config["models"]["qwen3.5-4b"]["sha256"] = "missing"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.load_config(path)

    def test_invalid_limits_fail_before_downloading_or_stopping_worker(self):
        import yaml

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for name, value in (
                ("MIN_FREE_MEMORY_MB", -1),
                ("EMBEDDING_BATCH_SIZE", 0),
                ("MAX_CORRECTIONS", -1),
                ("RERANKER_BATCH_SIZE", True),
                ("LLM_TIMEOUT_SECONDS", "600"),
            ):
                config = deepcopy(self.config)
                config["settings"][name] = value
                path.write_text(yaml.safe_dump(config), encoding="utf-8")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                    self.load_config(path)
            config["settings"] = {**self.config["settings"], "MAX_CORRECTIONS": 0}
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            self.assertEqual(self.load_config(path)["settings"]["MAX_CORRECTIONS"], 0)

    def test_invalid_retrieval_catalog_fails_before_downloading(self):
        import yaml

        changes = [
            {"revision": "main"},
            {"repo_id": "missing-owner"},
            {"max_length": 0},
            {"files": []},
            {"files": {}},
            {"files": {"config.json": "invalid"}},
            *(
                {"files": {name: "a" * 40}}
                for name in (
                    "../config.json",
                    "/config.json",
                    "C:/config.json",
                    "a\\config.json",
                    "a//config.json",
                )
            ),
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
