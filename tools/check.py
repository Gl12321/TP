import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
POSTGRES_TEST = "backend/tests/test_postgres_integration.py"
MODES = {
    "budget": "Физические строки тестов, помощников и проверочных конфигураций: не больше 3000",
    "quick": "Автономные Python-тесты, тесты frontend и TypeScript; без PostgreSQL и ML",
    "runtime": "Запуск, конфигурация и резервирование; внешние команды подменены",
    "backend": "API, роли и бизнес-сценарии на временных SQLite",
    "agent": "Грамматика SQL, AST, промпты, поиск и конвейер; без моделей",
    "frontend": "Vitest и TypeScript; без сервера приложения",
    "postgres": "Интеграция с отдельным локальным PostgreSQL",
    "browser": "Рабочий маршрут Playwright в отдельном тестовом приложении",
}


def test_lines():
    directories = ("tests", "backend/tests", "packages/sql_agent/tests", "frontend/tests")
    files = {
        path
        for directory in directories
        for path in (ROOT / directory).rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx", ".js", ".json", ".sql", ".yaml", ".yml"}
    }
    files.update(
        ROOT / path
        for path in (
            "tools/check.py",
            "tools/smoke.py",
            "tools/acceptance.py",
            "tools/test-postgres.compose.yml",
            ".github/workflows/verify.yml",
            "frontend/playwright.config.ts",
            "frontend/vite.config.ts",
            "frontend/package.json",
            "pytest.ini",
            "requirements-dev.txt",
        )
    )
    return sum(len(path.read_text(encoding="utf-8-sig").splitlines()) for path in files)


def plan(mode):
    commands = []
    suites = {
        "quick": [
            "backend/tests",
            "packages/sql_agent/tests",
            "tests",
            f"--ignore={POSTGRES_TEST}",
        ],
        "runtime": ["tests"],
        "backend": ["backend/tests", f"--ignore={POSTGRES_TEST}"],
        "agent": ["packages/sql_agent/tests"],
        "postgres": [POSTGRES_TEST],
    }
    if mode in suites:
        commands.append((ROOT, [sys.executable, "-m", "pytest", *suites[mode], "-q", "-ra"]))
    node = shutil.which("node") or "node"
    if mode in {"quick", "frontend"}:
        commands.extend(
            [
                (FRONTEND, [node, "node_modules/vitest/vitest.mjs", "run"]),
                (FRONTEND, [node, "node_modules/typescript/bin/tsc", "-b"]),
            ]
        )
    if mode == "browser":
        commands.append((FRONTEND, [node, "node_modules/@playwright/test/cli.js", "test"]))
    return commands


def browser_file(environment, name):
    value = environment.get(name)
    if not value:
        raise ValueError(f"Задайте {name}; инструкция: docs/development/testing.md")
    path = Path(value).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ValueError(f"{name} должен указывать на доступный JSON-файл") from None
    if not isinstance(document, dict):
        raise ValueError(f"{name} должен содержать JSON-объект")
    environment[name] = str(path)
    return document


def preflight(mode, commands, environment):
    if any(command[1:3] == ["-m", "pytest"] for _, command in commands):
        if importlib.util.find_spec("pytest") is None:
            raise ValueError(
                "Нужны зависимости разработки: python -m pip install -r requirements-dev.txt"
            )
    if mode in {"quick", "frontend", "browser"}:
        if not shutil.which("node"):
            raise ValueError("Node.js должен быть доступен в PATH")
        for directory, command in commands:
            if directory == FRONTEND and not (directory / command[1]).is_file():
                raise ValueError(
                    "Не подготовлены зависимости frontend: выполните npm ci в frontend"
                )
    if mode == "postgres":
        url = urlsplit(environment.get("RAZBOR_TEST_DATABASE_URL", ""))
        if url.scheme not in {"postgresql", "postgresql+asyncpg"} or url.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError(
                "Задайте RAZBOR_TEST_DATABASE_URL отдельного локального PostgreSQL; "
                "без него режим не считается выполненным"
            )
    if mode == "browser":
        account = browser_file(environment, "APP_UI_CREDENTIALS")
        if not all(
            isinstance(account.get(key), str) and account[key]
            for key in ("url", "email", "password")
        ):
            raise ValueError("APP_UI_CREDENTIALS должен содержать url, email и password")
        url = urlsplit(account["url"])
        if url.scheme not in {"http", "https"} or url.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Браузерный режим предназначен для отдельного приложения на loopback")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Проверки Разбора без автоматической установки зависимостей и моделей.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"{name:10} {description}" for name, description in MODES.items()),
    )
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--dry-run", action="store_true", help="показать команды без выполнения")
    args = parser.parse_args(argv)
    commands = plan(args.mode)
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        lines = test_lines()
        print(f"Тестовый бюджет: {lines} / 3000 физических строк.", flush=True)
        if lines > 3000:
            raise ValueError("Превышен лимит тестового кода; сократите повторяющиеся сценарии")
        if args.mode == "budget":
            return 0
        if not args.dry_run:
            preflight(args.mode, commands, environment)
        print(MODES[args.mode], flush=True)
        for directory, command in commands:
            display = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
            print(f"[{directory.relative_to(ROOT) or '.'}] {display}", flush=True)
            if not args.dry_run:
                result = subprocess.run(command, cwd=directory, env=environment, check=False)
                if result.returncode:
                    return result.returncode
        if not args.dry_run:
            print("Режим завершён. Пропущенные проверки указаны отдельно в выводе наборов.")
        return 0
    except (OSError, ValueError) as error:
        print(f"Проверки не запущены или прерваны: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Проверки остановлены.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
