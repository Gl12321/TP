import argparse
import os
import sys

from src.core.presets import load_config
from src.runtime.models import ensure_models


def prepare_database() -> None:
    import psycopg2
    from psycopg2 import sql

    reader = os.environ["DB_READ_USER"]
    if reader == os.environ["DB_USER"]:
        raise ValueError("Для чтения нужна отдельная роль PostgreSQL.")
    with psycopg2.connect(host="db", port=5432, dbname=os.environ["DB_NAME"],
                          user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                          connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (reader,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(reader)))
            cursor.execute(sql.SQL(
                "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %s"
            ).format(sql.Identifier(reader)), (os.environ["DB_READ_PASSWORD"],))
            cursor.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(sql.Identifier(reader)))
            cursor.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(reader)))
    print("PostgreSQL: роль чтения готова.", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        if args.list_models:
            for name, model in config["models"].items():
                size = model["size_bytes"] / 1024 ** 3
                default = " (по умолчанию)" if name == config["model"] else ""
                print(f"{name:18} {size:5.2f} ГиБ GGUF  {model['label']}{default}")
            return 0
        from src.core.config import Settings

        settings = Settings()
        print(f"Выбрана модель: {settings.MODEL_PRESET}", flush=True)
        if args.check:
            return 0
        ensure_models(settings.MODELS)
        prepare_database()
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"Подготовка не завершена: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
