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


def credentials(path: Path) -> None:
    if path.exists():
        return
    values = {
        "DB_NAME": "text2sql", "DB_USER": "importer", "DB_READ_USER": "reader",
        "DB_PASSWORD": secrets.token_urlsafe(32), "DB_READ_PASSWORD": secrets.token_urlsafe(32),
        "API_TOKEN": secrets.token_urlsafe(32),
    }
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        handle.write("".join(f"{key}={value}\n" for key, value in values.items()))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


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
    parser = argparse.ArgumentParser(description="Вопрос к базе обычными словами — готовая таблица в браузере.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--model", metavar="NAME", help="модель из config.yaml; без флага используется model из YAML")
    action.add_argument("--list-models", action="store_true", help="показать каталог моделей без загрузки весов")
    action.add_argument("--stop", action="store_true", help="остановить приложение, сохранив модели и данные")
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        parser.exit(1, "Нужен работающий Docker с Compose v2: Docker Desktop на Windows или Docker Engine на Linux.\n")
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    environment = os.environ.copy()
    for name in ("MODEL_PRESET", "DB_NAME", "DB_USER", "DB_READ_USER", "DB_PASSWORD", "DB_READ_PASSWORD", "API_TOKEN"):
        environment.pop(name, None)
    if args.model:
        environment["MODEL_PRESET"] = args.model
    project = "sql-agent-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
    command = ["docker", "compose", "--project-name", project,
               "--env-file", str(runtime / "compose.env"), "-f", str(ROOT / "docker-compose.yml")]

    def compose(*arguments, **kwargs):
        return subprocess.run([*command, *arguments], cwd=ROOT, env=environment, check=True, **kwargs)

    try:
        with launch_lock(runtime / "launch.lock"):
            credentials(runtime / "compose.env")
            compose("version", stdout=subprocess.DEVNULL)
            subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
            if args.stop:
                compose("stop", "ui", "api", "db")
                print("Приложение остановлено. Модели и данные сохранены.")
                return 0
            if args.list_models:
                compose("run", "--rm", "--no-deps", "--build", "prepare", "--list-models")
                return 0
            compose("build", "prepare")
            compose("run", "--rm", "--no-deps", "prepare", "--check")
            compose("build", "api", "ui")
            compose("stop", "ui", "api")
            compose("up", "--detach", "--wait", "--wait-timeout", "120", "db")
            compose("run", "--rm", "--no-deps", "prepare")
            compose("up", "--detach", "--wait", "--wait-timeout", "900", "api", "ui")
            print("Готово: http://localhost:8501\nОстановка: python run.py --stop")
            return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Запуск не завершён: {error}", file=sys.stderr)
        print("Исправьте причину выше и повторите команду. Данные и скачанные файлы сохраняются.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Подготовка прервана. Повторный запуск продолжит загрузки.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
