from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from tools import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = self.root / "backup.zip"
        self.output = self.root / "restored.env"
        self.values = {
            "DB_USER": "administrator",
            "DB_PASSWORD": "admin-password",
            "DB_NAME": "postgres",
            "APP_DB_USER": "application",
            "APP_DB_PASSWORD": "app-password",
            "APP_DB_NAME": "current",
            "APP_SECRET_KEY": "previous-secret-key-with-32-characters",
            "APP_DATABASE_URL": "original-database-url",
        }
        self.transport = Mock(values=self.values)
        self.secret = "restored-encryption-key-with-32-characters"
        self.files = {"database.dump": b"dump contents", "config.yaml": b"model: test\n"}

    def write_archive(self, *, corrupt=False, extra=False, malformed=False):
        manifest = {
            "format": 1,
            "secret_key": self.secret,
            "files": {
                name: hashlib.sha256(value).hexdigest() for name, value in self.files.items()
            },
        }
        if malformed:
            manifest["files"] = None
        with ZipFile(self.archive, "w") as archive:
            for name, value in self.files.items():
                archive.writestr(name, b"corrupt" if corrupt and name == "database.dump" else value)
            archive.writestr("manifest.json", json.dumps(manifest))
            if extra:
                archive.writestr("../outside", "unexpected")

    def restore(self, database="recovered"):
        with redirect_stdout(io.StringIO()):
            backup.restore(self.transport, self.archive, database, self.output)

    def test_invalid_archive_never_touches_postgresql(self):
        for options in ({"corrupt": True}, {"extra": True}, {"malformed": True}):
            with self.subTest(options=options):
                self.write_archive(**options)
                with self.assertRaises(ValueError):
                    self.restore()
                self.transport.pg.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_original_database_and_unsafe_names_are_rejected_before_restore(self):
        for name in ("current", "postgres", 'name"; DROP DATABASE current', "a" * 64):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.restore(name)
        self.transport.pg.assert_not_called()

    def test_existing_database_is_never_dropped_when_creation_fails(self):
        self.write_archive()
        self.transport.pg.side_effect = RuntimeError("database already exists")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.restore()
        self.assertEqual(self.transport.pg.call_count, 1)
        self.assertFalse(self.output.exists())

    def test_failed_restore_removes_only_new_database_and_publishes_no_configuration(self):
        self.write_archive()

        def execute(program, arguments, **kwargs):
            if program == "pg_restore":
                raise RuntimeError("damaged dump")

        self.transport.pg.side_effect = execute
        with self.assertRaisesRegex(RuntimeError, "damaged dump"):
            self.restore()
        commands = [call.args[1] for call in self.transport.pg.call_args_list]
        self.assertIn('DROP DATABASE "recovered" WITH (FORCE)', commands[-1])
        self.assertFalse(self.output.exists())
        self.transport.application.assert_not_called()

    def test_success_restores_encryption_key_and_revokes_sessions_before_publishing(self):
        self.write_archive()
        self.restore()
        values = backup.read_environment(self.output)
        self.assertEqual(values["APP_DB_NAME"], "recovered")
        self.assertEqual(values["APP_SECRET_KEY"], self.secret)
        self.assertEqual(values["APP_DB_PASSWORD"], self.values["APP_DB_PASSWORD"])
        self.assertNotIn("APP_DATABASE_URL", values)
        self.assertEqual(
            [call.args for call in self.transport.application.call_args_list],
            [("recovered", self.secret, "migrate"), ("recovered", self.secret, "restore-state")],
        )

    def test_existing_archive_cannot_be_overwritten(self):
        self.archive.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            backup.create(self.transport, self.archive)
        self.assertEqual(self.archive.read_bytes(), b"existing")
        self.transport.pg.assert_not_called()

    def test_create_and_unpack_preserve_dump_config_and_key(self):
        (self.root / "config.yaml").write_bytes(self.files["config.yaml"])

        def dump(program, arguments, *, destination):
            self.assertEqual(program, "pg_dump")
            destination.write(self.files["database.dump"])

        self.transport.pg.side_effect = dump
        with patch.object(backup, "ROOT", self.root), redirect_stdout(io.StringIO()):
            backup.create(self.transport, self.archive)
        restored = self.root / "unpacked"
        restored.mkdir()
        manifest = backup.unpack(self.archive, restored)
        self.assertEqual(manifest["secret_key"], self.values["APP_SECRET_KEY"])
        for name, value in self.files.items():
            self.assertEqual((restored / name).read_bytes(), value)
