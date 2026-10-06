from datetime import date
import os
import re
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg


def local_url():
    url = os.environ.get("RAZBOR_DEMO_DATABASE_URL", "").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError(
            "RAZBOR_DEMO_DATABASE_URL must point to an isolated loopback PostgreSQL server"
        )
    return url


async def seed(url, *, on_prepare=None):
    parsed = urlsplit(url)
    suffix = uuid4().hex[:10]
    database = "razbor_demo_source_" + suffix
    username = "razbor_demo_reader_" + suffix
    password = secrets.token_urlsafe(32)
    source = {
        "name": "Демонстрационная сеть: PostgreSQL",
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": database,
        "username": username,
        "password": password,
        "schemas": ["reporting"],
        "ssl_mode": "disable",
    }
    if on_prepare is not None:
        on_prepare({"source": source})
    admin = await asyncpg.connect(url)
    created_role = created_database = False
    try:
        await admin.execute(f"CREATE ROLE \"{username}\" LOGIN PASSWORD '{password}'")
        created_role = True
        await admin.execute(f'CREATE DATABASE "{database}"')
        created_database = True
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
                            "internal demo field",
                        )
                    )
                for index, amount in enumerate(current):
                    records.append(
                        (
                            code,
                            date(2026, 10, 1 + index),
                            amount,
                            amount // 1000,
                            "internal demo field",
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
    except BaseException:
        if created_database:
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        if created_role:
            await admin.execute(f'DROP ROLE "{username}"')
        raise
    finally:
        await admin.close()
    dataset = {
        "source": source,
        "stores": [
            {"name": "Арбат", "code": "A", "city": "Москва", "owner_name": "Сеть Разбор"},
            {
                "name": "Невский",
                "code": "B",
                "city": "Санкт-Петербург",
                "owner_name": "Сеть Разбор",
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
        "summary": {
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
    return dataset


async def cleanup(url, dataset):
    source = dataset["source"]
    parsed = urlsplit(url)
    database = source["database"]
    username = source["username"]
    match = re.fullmatch(r"razbor_demo_source_([0-9a-f]{10})", database)
    if (
        not match
        or username != "razbor_demo_reader_" + match.group(1)
        or source["host"] != parsed.hostname
        or source["port"] != (parsed.port or 5432)
        or database == parsed.path.lstrip("/")
    ):
        raise ValueError("Manifest must describe this server's generated demo source")
    admin = await asyncpg.connect(url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{username}"')
    finally:
        await admin.close()
