import argparse
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def credentials(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    defaults = {
        "DB_NAME": "text2sql",
        "DB_USER": "importer",
        "APP_DB_USER": "razbor_app",
        "APP_DB_NAME": "razbor",
    }
    missing = set(defaults) - values.keys()
    for name, value in defaults.items():
        values.setdefault(name, value)
    for name in ("DB_PASSWORD", "APP_DB_PASSWORD", "APP_SECRET_KEY", "APP_BOOTSTRAP_TOKEN"):
        if not values.get(name):
            values[name] = secrets.token_urlsafe(32)
            missing.add(name)
    if not missing and path.exists():
        return values
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        handle.write("".join(f"{key}={value}\n" for key, value in values.items()))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return values


@contextmanager
def launch_lock(path: Path):
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Другая команда запуска ещё выполняется.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Разбор: аналитика сети, вопросы к данным и работа команды."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--model",
        metavar="NAME",
        help="модель из config.yaml; без флага используется model из YAML",
    )
    action.add_argument(
        "--list-models", action="store_true", help="показать каталог моделей без загрузки весов"
    )
    action.add_argument(
        "--stop", action="store_true", help="остановить приложение, сохранив модели и данные"
    )
    parser.add_argument(
        "--without-ai",
        action="store_true",
        help="запустить приложение без загрузки и запуска моделей",
    )
    parser.add_argument("--env-file", type=Path, help="файл секретов и выбранной прикладной БД")
    args = parser.parse_args(argv)
    if args.without_ai and (args.stop or args.list_models):
        parser.error("--without-ai используется только при запуске приложения")
    if shutil.which("docker") is None:
        parser.exit(
            1,
            "Нужен работающий Docker с Compose v2: Docker Desktop на Windows или Docker Engine на Linux.\n",
        )
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    env_file = args.env_file.expanduser().resolve() if args.env_file else runtime / "compose.env"
    if args.env_file and not env_file.is_file():
        parser.exit(1, "Указанный файл окружения не найден.\n")
    environment = os.environ.copy()
    for name in (
        "MODEL_PRESET",
        "DB_NAME",
        "DB_USER",
        "DB_READ_USER",
        "DB_PASSWORD",
        "DB_READ_PASSWORD",
        "API_TOKEN",
        "APP_DB_USER",
        "APP_DB_NAME",
        "APP_DB_PASSWORD",
        "APP_DATABASE_URL",
        "APP_SECRET_KEY",
        "APP_BOOTSTRAP_TOKEN",
    ):
        environment.pop(name, None)
    if args.model:
        environment["MODEL_PRESET"] = args.model
    project = "sql-agent-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "docker-compose.yml"),
    ]

    def compose(*arguments, **kwargs):
        return subprocess.run(
            [*command, *arguments], cwd=ROOT, env=environment, check=True, **kwargs
        )

    try:
        with launch_lock(runtime / "launch.lock"):
            saved = credentials(env_file)
            compose("version", stdout=subprocess.DEVNULL)
            subprocess.run(
                ["docker", "info"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            if args.stop:
                compose("down", "--remove-orphans")
                print("Приложение остановлено. Модели и данные сохранены.")
                return 0
            if args.list_models:
                compose("run", "--rm", "--no-deps", "--build", "prepare", "--list-models")
                return 0
            compose("build", "prepare")
            compose("run", "--rm", "--no-deps", "prepare", "--check")
            compose("build", "api", "migrate", *([] if args.without_ai else ["worker"]))
            compose(
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "api",
                "-c",
                "from backend.app.infrastructure.config import load_settings; load_settings().validate()",
            )
            compose("stop", "worker")
            compose("up", "--detach", "--wait", "--wait-timeout", "120", "--remove-orphans", "db")
            compose("run", "--rm", "--no-deps", "bootstrap-db")
            compose("run", "--rm", "--no-deps", "migrate")
            compose("up", "--detach", "--wait", "--wait-timeout", "120", "api")
            port = environment.get("APP_PORT", "8000")
            print(f"Интерфейс доступен: http://localhost:{port}", flush=True)
            print(f"Код первоначальной настройки: {saved['APP_BOOTSTRAP_TOKEN']}", flush=True)
            print("Он нужен только для создания первого администратора в браузере.", flush=True)
            if not args.without_ai:
                print(
                    "Подготовка ассистента продолжается; интерфейс уже можно открыть.", flush=True
                )
                compose("run", "--rm", "--no-deps", "prepare")
                compose("up", "--detach", "--wait", "--wait-timeout", "900", "worker")
            print(f"Разбор готов: http://localhost:{port}\nОстановка: python run.py --stop")
            if args.without_ai:
                print("Генерация отключена. Обычный запуск подготовит модели и включит ассистента.")
            return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Запуск не завершён: {error}", file=sys.stderr)
        print(
            "Исправьте причину выше и повторите команду. Данные и скачанные файлы сохраняются.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("Подготовка прервана. Повторный запуск продолжит загрузки.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
