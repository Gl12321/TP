import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
from zipfile import BadZipFile, ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"database.dump", "manifest.json", "config.yaml"}


def read_environment(path):
    if not path.is_file():
        raise ValueError("Файл окружения не найден. Сначала подготовьте приложение.")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Некорректный файл окружения")
            values[key.strip()] = value.strip()
    for name in (
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME",
        "APP_DB_USER",
        "APP_DB_PASSWORD",
        "APP_DB_NAME",
        "APP_SECRET_KEY",
    ):
        if not values.get(name):
            raise ValueError(f"В окружении отсутствует {name}")
    return values


def identifier(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise ValueError(
            "Имя новой БД: латинские строчные буквы, цифры и подчёркивание, до 63 символов"
        )
    return '"' + value + '"'


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class Transport:
    def __init__(self, environment, env_file, pg_bin=None):
        self.values = environment
        self.env_file = env_file
        self.pg_bin = pg_bin
        self.project = "sql-agent-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]

    def compose(self, *arguments):
        return [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--env-file",
            str(self.env_file),
            "-f",
            str(ROOT / "docker-compose.yml"),
            *arguments,
        ]

    def pg(self, program, arguments, *, source=None, destination=None):
        environment = os.environ.copy()
        environment.update(self.values)
        if self.pg_bin is None:
            command = self.compose(
                "exec", "-T", "db", program, "-U", self.values["DB_USER"], *arguments
            )
        else:
            environment.update(PGPASSWORD=self.values["DB_PASSWORD"], PGCONNECT_TIMEOUT="10")
            suffix = ".exe" if os.name == "nt" else ""
            command = [
                str(self.pg_bin / (program + suffix)),
                "-h",
                self.values.get("DB_HOST", "127.0.0.1"),
                "-p",
                self.values.get("DB_PORT", "5432"),
                "-U",
                self.values["DB_USER"],
                *arguments,
            ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            stdin=source,
            stdout=destination or subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            message = result.stderr.decode("utf-8", errors="replace")[-3000:]
            for name in ("DB_PASSWORD", "APP_DB_PASSWORD", "APP_SECRET_KEY"):
                message = message.replace(self.values[name], "[redacted]")
            raise RuntimeError(f"{program}: {message.strip()}")
        return result.stdout

    def application(self, database, secret_key, command):
        environment = os.environ.copy()
        environment.update(self.values)
        environment.update(APP_DB_NAME=database, APP_SECRET_KEY=secret_key)
        if self.pg_bin is None:
            arguments = self.compose(
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "api",
                "-m",
                "backend.app.bootstrap",
                command,
            )
        else:
            host = self.values.get("DB_HOST", "127.0.0.1")
            port = self.values.get("DB_PORT", "5432")
            account = quote(self.values["APP_DB_USER"], safe="")
            password = quote(self.values["APP_DB_PASSWORD"], safe="")
            environment["APP_DATABASE_URL"] = (
                f"postgresql+asyncpg://{account}:{password}@{host}:{port}/{database}"
            )
            code = "import json,sys,runpy; sys.path[:0]=json.loads(sys.argv[1]); sys.argv=['bootstrap',sys.argv[2]]; runpy.run_module('backend.app.bootstrap',run_name='__main__')"
            arguments = [sys.executable, "-c", code, json.dumps([str(ROOT), *sys.path]), command]
        subprocess.run(arguments, cwd=ROOT, env=environment, check=True)


def create(transport, output):
    if output.exists():
        raise ValueError("Файл копии уже существует; выберите новое имя")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="razbor-backup-", dir=output.parent) as directory:
        directory = Path(directory)
        dump = directory / "database.dump"
        with dump.open("wb") as handle:
            transport.pg(
                "pg_dump",
                [
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    transport.values["APP_DB_NAME"],
                ],
                destination=handle,
            )
        config = ROOT / "config.yaml"
        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": transport.values["APP_DB_NAME"],
            "secret_key": transport.values["APP_SECRET_KEY"],
            "files": {"database.dump": digest(dump), "config.yaml": digest(config)},
        }
        temporary = directory / "archive.zip"
        with ZipFile(temporary, "w", compression=ZIP_STORED, allowZip64=True) as archive:
            archive.write(dump, "database.dump")
            archive.write(config, "config.yaml")
            archive.writestr("manifest.json", json.dumps(manifest))
        if os.name != "nt":
            temporary.chmod(0o600)
        created = False
        try:
            with output.open("xb") as target, temporary.open("rb") as source:
                created = True
                if os.name != "nt":
                    os.chmod(output, 0o600)
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise
    print(f"Копия создана: {output}")
    print("Архив содержит ключ расшифровки подключений. Храните его как секрет.")


def unpack(path, directory):
    with ZipFile(path) as archive:
        if set(archive.namelist()) != REQUIRED or len(archive.infolist()) != len(REQUIRED):
            raise ValueError("Неверный состав архива")
        if (
            archive.getinfo("manifest.json").file_size > 65536
            or archive.getinfo("config.yaml").file_size > 1048576
        ):
            raise ValueError("Некорректный размер метаданных копии")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != 1
            or not isinstance(manifest.get("secret_key"), str)
            or len(manifest["secret_key"]) < 32
            or any(character in manifest["secret_key"] for character in "\r\n\x00")
        ):
            raise ValueError("Неподдерживаемый формат копии")
        checksums = manifest.get("files")
        if (
            not isinstance(checksums, dict)
            or set(checksums) != {"database.dump", "config.yaml"}
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in checksums.values()
            )
        ):
            raise ValueError("Отсутствуют контрольные суммы файлов")
        total = sum(item.file_size for item in archive.infolist())
        if total + 104857600 > shutil.disk_usage(directory).free:
            raise ValueError("Недостаточно свободного места для проверки копии")
        for name in ("database.dump", "config.yaml"):
            destination = directory / name
            with archive.open(name) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
            if digest(destination) != manifest["files"][name]:
                raise ValueError(f"Повреждён файл {name}")
    return manifest


def restore(transport, archive, database, output_env):
    target = identifier(database)
    account = '"' + transport.values["APP_DB_USER"].replace('"', '""') + '"'
    if database in {transport.values["DB_NAME"], transport.values["APP_DB_NAME"]}:
        raise ValueError("Восстановление разрешено только в новую БД с отдельным именем")
    if output_env.exists():
        raise ValueError("Файл окружения восстановления уже существует")
    output_env.parent.mkdir(parents=True, exist_ok=True)
    created = False
    env_created = False
    with tempfile.TemporaryDirectory(prefix="razbor-restore-", dir=output_env.parent) as directory:
        directory = Path(directory)
        manifest = unpack(archive, directory)
        try:
            transport.pg(
                "psql",
                [
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-d",
                    transport.values["DB_NAME"],
                    "-c",
                    f"CREATE DATABASE {target} OWNER {account}",
                ],
            )
            created = True
            transport.pg(
                "psql",
                [
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-d",
                    database,
                    "-c",
                    f"REVOKE CONNECT ON DATABASE {target} FROM PUBLIC; "
                    "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
                ],
            )
            with (directory / "database.dump").open("rb") as dump:
                transport.pg(
                    "pg_restore",
                    [
                        "--exit-on-error",
                        "--single-transaction",
                        "--no-owner",
                        "--no-acl",
                        "--role",
                        transport.values["APP_DB_USER"],
                        "--dbname",
                        database,
                    ],
                    source=dump,
                )
            transport.application(database, manifest["secret_key"], "migrate")
            transport.application(database, manifest["secret_key"], "restore-state")
            values = {
                **transport.values,
                "APP_DB_NAME": database,
                "APP_SECRET_KEY": manifest["secret_key"],
            }
            values.pop("APP_DATABASE_URL", None)
            with output_env.open("x", encoding="utf-8", newline="\n") as handle:
                env_created = True
                if os.name != "nt":
                    os.chmod(output_env, 0o600)
                handle.write("".join(f"{key}={value}\n" for key, value in values.items()))
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            if env_created:
                output_env.unlink(missing_ok=True)
            if created:
                transport.pg(
                    "psql",
                    [
                        "-X",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-d",
                        transport.values["DB_NAME"],
                        "-c",
                        f"DROP DATABASE {target} WITH (FORCE)",
                    ],
                )
            raise
    print(f"Копия восстановлена в новую БД {database}; исходная БД сохранена.")
    print(f"Окружение для запуска: {output_env}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Резервная копия и восстановление Разбора в новую БД"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".runtime/compose.env")
    parser.add_argument(
        "--pg-bin", type=Path, help="Каталог PostgreSQL CLI для проверки без Docker"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    save = actions.add_parser("create")
    save.add_argument("archive", type=Path)
    recover = actions.add_parser("restore")
    recover.add_argument("archive", type=Path)
    recover.add_argument("--database", required=True)
    recover.add_argument("--output-env", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        transport = Transport(read_environment(args.env_file), args.env_file.resolve(), args.pg_bin)
        if args.action == "create":
            create(transport, args.archive.resolve())
        else:
            restore(transport, args.archive.resolve(), args.database, args.output_env.resolve())
    except (ValueError, OSError, RuntimeError, BadZipFile, subprocess.SubprocessError) as error:
        parser.exit(1, f"Операция не завершена: {error}\n")


if __name__ == "__main__":
    main()
