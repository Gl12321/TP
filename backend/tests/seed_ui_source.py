import asyncio
from datetime import date
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg


async def seed():
    directory = Path(".runtime/postgres-check")
    record = json.loads((directory / "connection.json").read_text(encoding="utf-8-sig"))
    url = record["url"].replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlsplit(url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("UI fixture requires an isolated loopback PostgreSQL server")
    suffix = uuid4().hex[:10]
    database = "razbor_ui_" + suffix
    username = "razbor_ui_reader_" + suffix
    password = secrets.token_urlsafe(32)
    admin = await asyncpg.connect(url)
    try:
        await admin.execute(f"CREATE ROLE \"{username}\" LOGIN PASSWORD '{password}'")
        await admin.execute(f'CREATE DATABASE "{database}"')
        connection = await asyncpg.connect(url, database=database)
        try:
            await connection.execute(
                "CREATE SCHEMA reporting; CREATE TABLE reporting.stores (code text PRIMARY KEY,name text NOT NULL,city text NOT NULL); CREATE TABLE reporting.sales (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,store_code text NOT NULL REFERENCES reporting.stores(code),business_day date NOT NULL,net_revenue numeric(14,2) NOT NULL,checks integer NOT NULL,internal_note text); CREATE INDEX sales_store_day ON reporting.sales(store_code,business_day)"
            )
            await connection.executemany(
                "INSERT INTO reporting.stores(code,name,city) VALUES($1,$2,$3)",
                [("A", "Арбат", "Москва"), ("B", "Невский", "Санкт-Петербург")],
            )
            records = []
            for code, previous, current in (
                ("A", [70000, 80000, 90000, 60000], [100000, 100000, 120000, 80000]),
                ("B", [50000, 50000, 60000, 40000], [80000, 120000, 60000, 40000]),
            ):
                for index, amount in enumerate(previous):
                    records.append(
                        (
                            code,
                            date(2026, 9, 27 + index),
                            amount,
                            amount // 1000,
                            "test-only restricted field",
                        )
                    )
                for index, amount in enumerate(current):
                    records.append(
                        (
                            code,
                            date(2026, 10, 1 + index),
                            amount,
                            amount // 1000,
                            "test-only restricted field",
                        )
                    )
            await connection.executemany(
                "INSERT INTO reporting.sales(store_code,business_day,net_revenue,checks,internal_note) VALUES($1,$2,$3,$4,$5)",
                records,
            )
            await connection.execute(
                f'GRANT USAGE ON SCHEMA reporting TO "{username}"; GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO "{username}"'
            )
        finally:
            await connection.close()
        await admin.execute(
            f'ALTER ROLE "{username}" IN DATABASE "{database}" SET default_transaction_read_only = on'
        )
    finally:
        await admin.close()
    fixture = {
        "source": {
            "name": "Проверка финансов: PostgreSQL",
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "database": database,
            "username": username,
            "password": password,
            "schemas": ["reporting"],
            "ssl_mode": "disable",
        },
        "stores": [
            {"name": "Арбат", "code": "A", "city": "Москва", "owner_name": "Тестовая сеть"},
            {
                "name": "Невский",
                "code": "B",
                "city": "Санкт-Петербург",
                "owner_name": "Тестовая сеть",
            },
        ],
        "policies": {
            "tables": [
                {
                    "schema": "reporting",
                    "name": "stores",
                    "columns": ["code", "name", "city"],
                    "store_column": "code",
                    "shared": False,
                },
                {
                    "schema": "reporting",
                    "name": "sales",
                    "columns": ["id", "store_code", "business_day", "net_revenue", "checks"],
                    "store_column": "store_code",
                    "shared": False,
                },
            ]
        },
        "metric": {
            "key": "net_revenue",
            "name": "Выручка после возвратов",
            "description": "Сумма net_revenue по дате business_day. Данные в рублях, возвраты уже учтены в сумме, одна строка на точку и день.",
            "unit": "RUB",
            "table_schema": "reporting",
            "table_name": "sales",
            "value_column": "net_revenue",
            "date_column": "business_day",
            "store_column": "store_code",
            "aggregation": "sum",
        },
        "plans": [
            {"store_code": "A", "period": "2026-10-01", "amount": "500000"},
            {"store_code": "B", "period": "2026-10-01", "amount": "250000"},
        ],
        "expected": {
            "date_from": "2026-10-01",
            "date_to": "2026-10-31",
            "actual": "700000.00",
            "plan": "750000.0000",
            "attainment": "93.33",
            "previous": "500000.00",
            "change_percent": "40.00",
            "stores": {"A": "400000.00", "B": "300000.00"},
        },
    }
    output = directory / "ui-source.json"
    output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "UI reporting fixture is ready; private connection manifest: .runtime/postgres-check/ui-source.json"
    )


if __name__ == "__main__":
    asyncio.run(seed())
