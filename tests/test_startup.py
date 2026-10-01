from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import run
from src.runtime import models


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(run, "ROOT", self.root))
        self.stack.enter_context(patch.object(run.shutil, "which", return_value="docker"))
        self.process = self.stack.enter_context(patch.object(run.subprocess, "run"))
        self.process.return_value = SimpleNamespace(returncode=0)
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        self.stack.enter_context(redirect_stderr(self.errors))

    def compose_calls(self):
        result = []
        for call in self.process.call_args_list:
            command = call.args[0]
            if command[:2] == ["docker", "compose"]:
                result.append(command[command.index("-f") + 2:])
        return result

    def test_repeated_launch_preserves_existing_credentials(self):
        with patch.object(run.secrets, "token_urlsafe", side_effect=["import-secret", "read-secret", "api-secret"]) as random:
            self.assertEqual(run.main([]), 0)
            path = self.root / ".runtime" / "compose.env"
            original = path.read_bytes()
            self.assertEqual(run.main([]), 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(random.call_count, 3)
        self.assertIn(b"DB_PASSWORD=import-secret\n", original)
        self.assertIn(b"DB_READ_PASSWORD=read-secret\n", original)
        self.assertIn(b"API_TOKEN=api-secret\n", original)

    def test_selected_model_is_passed_without_inheriting_credentials(self):
        inherited = {name: "foreign-value" for name in (
            "MODEL_PRESET", "DB_NAME", "DB_USER", "DB_READ_USER", "DB_PASSWORD", "DB_READ_PASSWORD", "API_TOKEN",
        )}
        with patch.dict(os.environ, inherited):
            self.assertEqual(run.main(["--model", "selected-model"]), 0)
        for call in self.process.call_args_list:
            if call.args[0][:2] == ["docker", "compose"]:
                environment = call.kwargs["env"]
                self.assertEqual(environment["MODEL_PRESET"], "selected-model")
                for name in inherited.keys() - {"MODEL_PRESET"}:
                    self.assertNotIn(name, environment)

    def test_listing_models_does_not_start_database_or_runtime_services(self):
        self.assertEqual(run.main(["--list-models"]), 0)
        calls = self.compose_calls()
        self.assertEqual(calls, [
            ["version"],
            ["run", "--rm", "--no-deps", "--build", "prepare", "--list-models"],
        ])
        self.assertFalse(any(call[0] in {"up", "start"} for call in calls))

    def test_stop_preserves_downloads_and_does_not_prepare_models(self):
        self.assertEqual(run.main(["--stop"]), 0)
        self.assertEqual(self.compose_calls(), [["version"], ["stop", "ui", "api", "db"]])

    def test_invalid_flags_exit_before_any_external_command(self):
        for arguments in (["--unknown-command"], ["--model"], ["--stop", "--model", "any"]):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as caught:
                run.main(arguments)
            self.assertEqual(caught.exception.code, 2)
        self.process.assert_not_called()
        self.assertFalse((self.root / ".runtime").exists())

    def test_application_starts_only_after_preparation_and_database_readiness(self):
        self.assertEqual(run.main(["--model", "chosen"]), 0)
        calls = self.compose_calls()
        check = calls.index(["run", "--rm", "--no-deps", "prepare", "--check"])
        build = calls.index(["build", "api", "ui"])
        stop = calls.index(["stop", "ui", "api"])
        database = calls.index(["up", "--detach", "--wait", "--wait-timeout", "120", "db"])
        prepare = calls.index(["run", "--rm", "--no-deps", "prepare"])
        application = calls.index(["up", "--detach", "--wait", "--wait-timeout", "900", "api", "ui"])
        self.assertLess(check, build)
        self.assertLess(build, stop)
        self.assertLess(stop, database)
        self.assertLess(database, prepare)
        self.assertLess(prepare, application)

    def test_failed_configuration_check_leaves_running_services_untouched(self):
        def execute(command, **kwargs):
            if command[-5:] == ["run", "--rm", "--no-deps", "prepare", "--check"]:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(returncode=0)

        self.process.side_effect = execute
        self.assertEqual(run.main(["--model", "unknown-model"]), 1)
        calls = self.compose_calls()
        self.assertFalse(any(call[0] in {"stop", "up", "start"} for call in calls))
        self.assertNotIn(["build", "api", "ui"], calls)
        self.assertNotIn(["run", "--rm", "--no-deps", "prepare"], calls)

    def test_failed_preparation_does_not_start_application(self):
        def execute(command, **kwargs):
            if command[-4:] == ["run", "--rm", "--no-deps", "prepare"]:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(returncode=0)

        self.process.side_effect = execute
        self.assertEqual(run.main([]), 1)
        self.assertFalse(any(call[0] == "up" and "api" in call for call in self.compose_calls()))
        self.assertTrue((self.root / ".runtime" / "compose.env").is_file())

    def test_interruption_during_preparation_does_not_start_application(self):
        def execute(command, **kwargs):
            if command[-4:] == ["run", "--rm", "--no-deps", "prepare"]:
                raise KeyboardInterrupt
            return SimpleNamespace(returncode=0)

        self.process.side_effect = execute
        self.assertEqual(run.main([]), 130)
        self.assertFalse(any(call[0] == "up" and "api" in call for call in self.compose_calls()))


class ModelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.content = b"GGUF" + bytes(range(64))
        self.path = self.root / "model.gguf"
        self.marker = self.path.with_suffix(".ready.json")
        self.config = {
            "repo_id": "fixture/model", "revision": "1" * 40, "filename": self.path.name,
            "sha256": hashlib.sha256(self.content).hexdigest(), "size_bytes": len(self.content),
            "params": {"model_path": str(self.path)},
        }
        self.identity = {key: self.config[key] for key in ("repo_id", "revision", "sha256", "size_bytes")}
        self.download = Mock()
        self.stack.enter_context(patch.dict(sys.modules, {
            "huggingface_hub": SimpleNamespace(hf_hub_download=self.download),
        }))

    def test_matching_existing_gguf_is_reused_without_huggingface(self):
        self.path.write_bytes(self.content)
        with patch.dict(sys.modules, {"huggingface_hub": None}):
            models.ensure_llm(self.config)
            models.ensure_llm(self.config)
        self.assertEqual(self.path.read_bytes(), self.content)
        self.assertTrue(models.ready(self.marker, self.identity, self.root))
        self.download.assert_not_called()

    def test_downloaded_gguf_is_marked_ready_only_after_hash_verification(self):
        self.download.side_effect = lambda **kwargs: self.path.write_bytes(self.content)
        models.ensure_llm(self.config)
        self.download.assert_called_once_with(
            repo_id=self.config["repo_id"], filename=self.path.name,
            revision=self.config["revision"], local_dir=self.root, force_download=False,
        )
        self.assertTrue(models.ready(self.marker, self.identity, self.root))

    def test_interrupted_download_is_not_ready_and_can_be_retried(self):
        def interrupted(**kwargs):
            self.path.write_bytes(self.content[:10])
            raise KeyboardInterrupt

        self.download.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt):
            models.ensure_llm(self.config)
        self.assertFalse(self.marker.exists())
        self.assertFalse(models.valid_gguf(self.path, self.config))
        self.download.side_effect = lambda **kwargs: self.path.write_bytes(self.content)
        models.ensure_llm(self.config)
        self.assertTrue(models.ready(self.marker, self.identity, self.root))

    def test_wrong_hash_with_matching_size_never_becomes_ready(self):
        corrupt = self.content[:-1] + b"!"
        self.path.write_bytes(corrupt)
        self.download.side_effect = lambda **kwargs: self.path.write_bytes(corrupt)
        with self.assertRaises(ValueError):
            models.ensure_llm(self.config)
        self.assertEqual(self.path.stat().st_size, self.config["size_bytes"])
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.download.call_args.kwargs["force_download"])

    def test_missing_invalid_header_or_incomplete_file_is_not_gguf(self):
        self.assertFalse(models.valid_gguf(self.path, self.config))
        for content in (b"", self.content[:10], b"NOPE" + self.content[4:]):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                self.assertFalse(models.valid_gguf(self.path, self.config))

    def test_manifest_is_invalidated_when_file_is_removed(self):
        self.path.write_bytes(self.content)
        models.mark_ready(self.marker, self.identity, self.root, [self.path.name])
        self.path.unlink()
        self.assertFalse(models.ready(self.marker, self.identity, self.root))

    def test_manifest_is_invalidated_when_size_or_modification_time_changes(self):
        self.path.write_bytes(self.content)
        models.mark_ready(self.marker, self.identity, self.root, [self.path.name])
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertFalse(models.ready(self.marker, self.identity, self.root))
        models.mark_ready(self.marker, self.identity, self.root, [self.path.name])
        stat = self.path.stat()
        self.path.write_bytes(self.content + b"extra")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertFalse(models.ready(self.marker, self.identity, self.root))

    def test_changed_model_revision_invalidates_ready_manifest(self):
        self.path.write_bytes(self.content)
        models.mark_ready(self.marker, self.identity, self.root, [self.path.name])
        changed = {**self.identity, "revision": "2" * 40}
        self.assertFalse(models.ready(self.marker, changed, self.root))

    def test_malformed_manifest_is_treated_as_unready(self):
        for value in ("not JSON", "null", "[]", "{}", json.dumps({
            "identity": self.identity, "files": [self.path.name],
        })):
            with self.subTest(manifest=value):
                self.marker.write_text(value, encoding="utf-8")
                self.assertFalse(models.ready(self.marker, self.identity, self.root))

    def test_partial_snapshot_does_not_receive_ready_marker(self):
        directory = self.root / "embedder"
        config = {"repo_id": "fixture/embedder", "revision": "3" * 40,
                  "files": {"config.json": hashlib.sha1(b"blob 2\0{}").hexdigest(),
                            "model.safetensors": hashlib.sha256(b"weights").hexdigest()},
                  "cache_path": str(directory)}

        def partial_snapshot(**kwargs):
            if kwargs["filename"] == "config.json":
                (directory / "config.json").write_bytes(b"{}")

        self.download.side_effect = partial_snapshot
        with self.assertRaises(ValueError):
            models.ensure_snapshot(config)
        self.assertFalse((directory / ".ready.json").exists())

    def test_missing_snapshot_file_triggers_preparation_again(self):
        directory = self.root / "reranker"
        config = {"repo_id": "fixture/reranker", "revision": "4" * 40,
                  "files": {"config.json": hashlib.sha1(b"blob 7\0fixture").hexdigest(),
                            "model.safetensors": hashlib.sha256(b"fixture").hexdigest()},
                  "cache_path": str(directory)}

        def complete_snapshot(**kwargs):
            (directory / kwargs["filename"]).write_bytes(b"fixture")

        self.download.side_effect = complete_snapshot
        models.ensure_snapshot(config)
        models.ensure_snapshot(config)
        self.assertEqual(self.download.call_count, 2)
        (directory / "model.safetensors").unlink()
        models.ensure_snapshot(config)
        self.assertEqual(self.download.call_count, 3)
        self.download.assert_called_with(
            repo_id=config["repo_id"], filename="model.safetensors", revision=config["revision"],
            local_dir=directory, force_download=False,
        )

    def test_valid_snapshot_without_marker_is_reused_without_network(self):
        directory = self.root / "embedder"
        (directory / "1_Pooling").mkdir(parents=True)
        (directory / "1_Pooling/config.json").write_bytes(b"{}")
        (directory / "model.safetensors").write_bytes(b"weights")
        config = {"repo_id": "fixture/embedder", "revision": "3" * 40,
                  "files": {"1_Pooling/config.json": hashlib.sha1(b"blob 2\0{}").hexdigest(),
                            "model.safetensors": hashlib.sha256(b"weights").hexdigest()},
                  "cache_path": str(directory)}
        with patch.dict(sys.modules, {"huggingface_hub": None}):
            models.ensure_snapshot(config)
        self.assertTrue((directory / ".ready.json").is_file())
        self.download.assert_not_called()

    def test_corrupt_snapshot_without_network_does_not_become_ready(self):
        directory = self.root / "reranker"
        directory.mkdir()
        (directory / "model.safetensors").write_bytes(b"damaged")
        config = {"repo_id": "fixture/reranker", "revision": "4" * 40,
                  "files": {"model.safetensors": hashlib.sha256(b"weights").hexdigest()},
                  "cache_path": str(directory)}
        self.download.side_effect = OSError("Network unavailable")
        with self.assertRaises(OSError):
            models.ensure_snapshot(config)
        self.assertFalse((directory / ".ready.json").exists())
        self.assertTrue(self.download.call_args.kwargs["force_download"])

    def test_downloaded_snapshot_with_wrong_hash_is_rejected(self):
        directory = self.root / "embedder"
        config = {"repo_id": "fixture/embedder", "revision": "3" * 40,
                  "files": {"model.safetensors": hashlib.sha256(b"weights").hexdigest()},
                  "cache_path": str(directory)}
        self.download.side_effect = lambda **kwargs: (directory / kwargs["filename"]).write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            models.ensure_snapshot(config)
        self.assertFalse((directory / ".ready.json").exists())


if __name__ == "__main__":
    unittest.main()
