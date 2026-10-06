import argparse
import asyncio
import importlib
import os
import sys

from tools.demo.state import (
    DemoFiles,
    DemoError,
    check_files,
    environment,
    load,
    new_accounts,
    new_state,
    preflight,
    save,
    validate,
)


def prepare(port, files, scenario):
    from tools.demo.source import cleanup, local_url, seed
    from tools.demo.database import cleanup_application, create_application

    try:
        admin_url = local_url()
    except ValueError as error:
        raise DemoError(
            "Для первой подготовки задайте RAZBOR_DEMO_DATABASE_URL: административное подключение "
            "к отдельному демонстрационному PostgreSQL на localhost, 127.0.0.1 или ::1."
        ) from error
    state, accounts = new_state(admin_url, port, scenario), new_accounts(port, scenario)
    save(files.path("state"), state)
    source = None
    application_created = False
    creation_pending = False
    try:
        if scenario == "network":
            state["preparation_stage"] = "source"
            save(files.path("state"), state)
            creation_pending = True
            source = asyncio.run(
                seed(admin_url, on_prepare=lambda intent: save(files.path("source"), intent))
            )
            creation_pending = False
            save(files.path("source"), source)
        state["preparation_stage"] = "application"
        save(files.path("state"), state)
        creation_pending = True
        asyncio.run(create_application(admin_url, state))
        creation_pending = False
        application_created = True
        os.environ.update(environment(state, files))
        from backend.app.bootstrap import migrate
        from backend.app.main import create_app
        from tools.demo.seed import populate, populate_onboarding

        migrate()
        if scenario == "network":
            asyncio.run(populate(create_app(), state, accounts, source))
        else:
            asyncio.run(populate_onboarding(create_app(), state, accounts))
        save(files.path("accounts"), accounts)
        state.pop("preparation_stage")
        state["ready"] = True
        save(files.path("state"), state)
    except BaseException:
        cleanup_failed = creation_pending
        if application_created:
            try:
                asyncio.run(cleanup_application(admin_url, state))
            except Exception:
                cleanup_failed = True
        if source is not None:
            try:
                asyncio.run(cleanup(admin_url, source))
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            print(
                f"Создание или очистка ресурсов не подтверждены. Сохраните {files.directory}: "
                "state.json и source.json содержат идентификаторы для проверки.",
                file=sys.stderr,
            )
        else:
            for name in ("state", "source", "accounts"):
                files.path(name).unlink(missing_ok=True)
        raise
    return state


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Самостоятельная демонстрация продукта: заполненная сеть или первоначальная настройка, без моделей."
    )
    parser.add_argument("--scenario", choices=("network", "onboarding"), default="network")
    parser.add_argument(
        "--port", type=int, default=None, help="порт первого запуска: network 8766, onboarding 8767"
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--prepare-only", action="store_true", help="создать/проверить demo без HTTP-сервера"
    )
    action.add_argument(
        "--accounts",
        action="store_true",
        help="явно вывести локальные демонстрационные логины и пароли",
    )
    action.add_argument(
        "--check",
        action="store_true",
        help="проверить зависимости, frontend, порт и PostgreSQL без подготовки и миграций",
    )
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("порт должен быть от 1 до 65535")
    files = DemoFiles.for_scenario(args.scenario)
    try:
        if args.accounts:
            state = load(files.path("state"))
            validate(state)
            accounts, _ = check_files(state, files)
            for account in accounts["accounts"]:
                print(f"{account['name']}\n{account['email']}\n{account['password']}\n")
            return 0
        state = load(files.path("state")) if files.path("state").exists() else None
        if state is not None:
            validate(state)
            check_files(state, files)
            if state.get("scenario", "network") != args.scenario:
                raise DemoError("Сценарий не соответствует сохранённому состоянию demo.")
            if args.port is not None and args.port != state["port"]:
                raise DemoError(
                    "У подготовленного demo другой порт. Используйте команду без --port."
                )
        elif any(files.path(name).exists() for name in ("source", "accounts", "credentials")):
            raise DemoError(
                f"В {files.directory} есть данные без state.json. Автоматическое пересоздание отключено."
            )
        port = (
            state["port"] if state else args.port or (8766 if args.scenario == "network" else 8767)
        )
        preflight(port)
        if args.check:
            for module in (
                "fastapi",
                "sqlalchemy",
                "asyncpg",
                "alembic",
                "cryptography",
                "sqlglot",
                "httpx",
                "yaml",
                "sql_agent",
            ):
                importlib.import_module(module)
            from tools.demo.database import check_administrator, check_application

            if state is not None:
                asyncio.run(check_application(state))
            else:
                from tools.demo.source import local_url

                try:
                    admin_url = local_url()
                except ValueError as error:
                    raise DemoError(
                        "Для первой подготовки задайте RAZBOR_DEMO_DATABASE_URL; инструкция: docs/demo/README.md."
                    ) from error
                asyncio.run(check_administrator(admin_url))
            print(
                f"Demo {args.scenario}: зависимости, frontend, порт {port} и PostgreSQL доступны. Данные не изменены."
            )
            return 0
        files.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        from run import launch_lock

        with launch_lock(files.directory / "launch.lock"):
            if state is None and files.path("state").exists():
                raise DemoError("Состояние demo изменилось. Повторите запуск.")
            if state is None:
                print(
                    "Подготовка отдельного demo: PostgreSQL, роли и учебные сценарии.", flush=True
                )
                state = prepare(port, files, args.scenario)
            else:
                os.environ.update(environment(state, files))
                from backend.app.bootstrap import migrate
                from tools.demo.database import check_application

                asyncio.run(check_application(state))
                migrate()
            print(f"Demo: http://127.0.0.1:{port}")
            print(
                f"Аккаунты: {files.path('accounts')}; показать: python -m tools.demo --scenario {args.scenario} --accounts"
            )
            if args.scenario == "network":
                print(
                    f"Период выручки: {state['period']['date_from']} — {state['period']['date_to']}; исходный факт 700 000 ₽, план 750 000 ₽."
                )
            else:
                print("Пустое пространство: начните с подключения источника и настройки точек.")
            print("Модели и worker выключены; вопросы агенту не исполняются.", flush=True)
            if not args.prepare_only:
                import uvicorn

                print("Для остановки HTTP-сервера: Ctrl+C.", flush=True)
                uvicorn.run(
                    "backend.app.main:app", host="127.0.0.1", port=port, log_level="warning"
                )
            else:
                print("Подготовка завершена. HTTP-сервер не запущен.")
        return 0
    except DemoError as error:
        print(str(error), file=sys.stderr)
    except ModuleNotFoundError as error:
        print(
            f"Не установлена зависимость {error.name}. Установите requirements.txt и packages/sql_agent; команда ничего не скачивает.",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        return 130
    except ConnectionRefusedError:
        print(
            "PostgreSQL не принимает подключения. Запустите локальный сервер PostgreSQL "
            "и повторите команду demo. Адрес сохранён в state.json выбранного сценария.",
            file=sys.stderr,
        )
    except Exception as error:
        print(
            f"Demo не запущено: {type(error).__name__}. Проверьте локальный PostgreSQL, зависимости и состояние {files.directory}. Секреты не выводятся.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
