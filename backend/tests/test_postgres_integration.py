import asyncio
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
import os
from pathlib import Path
import secrets
import time
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
import asyncpg
import httpx
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from backend.app.access.models import Membership, User, Workspace
from backend.app.access.policy import from_membership
from backend.app.analytics.models import Store
from backend.app.assistant.models import Conversation, QueryRun
from backend.app.assistant.routes import cancel
from backend.app.infrastructure.config import Settings
from backend.app.infrastructure.database import Database, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import SecretStore
from backend.app.jobs.models import Job, RunEvent
from backend.app.jobs.service import finish_job, renew_lease
from backend.app.sources.models import Source
from backend.app.sources.service import (
    ScopedExecutor,
    SourceConnections,
    authorized_catalog,
    inspect_source,
)
from sql_agent.contracts import QueryContext, QueryError


TEST_URL = os.getenv("RAZBOR_TEST_DATABASE_URL")


@unittest.skipUnless(
    TEST_URL, "Set RAZBOR_TEST_DATABASE_URL to an isolated local PostgreSQL test cluster"
)
class PostgreSQLIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = make_url(TEST_URL)
        if cls.base_url.drivername not in {
            "postgresql",
            "postgresql+asyncpg",
        } or cls.base_url.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Integration tests require an explicit local PostgreSQL test cluster")
        suffix = uuid4().hex[:16]
        cls.database_name = "razbor_it_" + suffix
        cls.reader_name = "razbor_reader_" + suffix
        cls.reader_password = secrets.token_hex(24)
        cls.created_database = cls.created_role = False
        cls.database_url = cls.base_url.set(
            drivername="postgresql+asyncpg", database=cls.database_name
        ).render_as_string(hide_password=False)
        try:
            asyncio.run(cls.create_database())
            root = Path(__file__).resolve().parents[2]
            config = Config(str(root / "backend/alembic.ini"))
            config.set_main_option("script_location", str(root / "backend/migrations"))
            config.set_main_option("sqlalchemy.url", cls.database_url.replace("%", "%%"))
            command.upgrade(config, "head")
            asyncio.run(cls.create_reporting_data())
        except BaseException:
            asyncio.run(cls.remove_database())
            raise

    @classmethod
    async def admin_connection(cls, *, application=False):
        return await asyncpg.connect(
            host=cls.base_url.host,
            port=cls.base_url.port or 5432,
            user=cls.base_url.username,
            password=cls.base_url.password,
            database=cls.database_name if application else cls.base_url.database,
            timeout=10,
            command_timeout=10,
        )

    @classmethod
    async def create_database(cls):
        connection = await cls.admin_connection()
        try:
            await connection.execute(f'CREATE DATABASE "{cls.database_name}"')
            cls.created_database = True
            await connection.execute(
                f"CREATE ROLE \"{cls.reader_name}\" LOGIN PASSWORD '{cls.reader_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            cls.created_role = True
        finally:
            await connection.close()

    @classmethod
    async def create_reporting_data(cls):
        connection = await cls.admin_connection(application=True)
        try:
            await connection.execute("""
                CREATE SCHEMA reporting;
                CREATE TABLE reporting.stores(code TEXT PRIMARY KEY, city TEXT NOT NULL);
                CREATE TABLE reporting.sales(
                    id BIGINT PRIMARY KEY,
                    store_code TEXT NOT NULL REFERENCES reporting.stores(code),
                    amount NUMERIC(20,4),
                    day DATE NOT NULL,
                    time_value TIME NOT NULL,
                    elapsed INTERVAL NOT NULL,
                    secret TEXT
                );
                CREATE TABLE reporting.refunds(
                    id BIGINT PRIMARY KEY,
                    store_code TEXT NOT NULL REFERENCES reporting.stores(code),
                    sale_id BIGINT NOT NULL REFERENCES reporting.sales(id),
                    amount NUMERIC(20,4) NOT NULL
                );
                INSERT INTO reporting.stores VALUES ('A','Moscow'),('B','Kazan');
                INSERT INTO reporting.sales VALUES
                    (1,'A',123456789012345.6789,'2026-09-01','12:34:56',INTERVAL '1 day 2 seconds','hidden A'),
                    (2,'A',0.0001,'2026-09-30','23:59:59',INTERVAL '3 seconds','hidden A2'),
                    (3,'B',999999.1111,'2026-09-01','00:00:00',INTERVAL '4 seconds','hidden B');
                INSERT INTO reporting.refunds VALUES (1,'A',1,5),(2,'B',1,500);
                CREATE VIEW reporting.slow_sales AS
                    SELECT id,store_code,amount+(SELECT 0::NUMERIC FROM pg_sleep(5)) AS amount FROM reporting.sales;
            """)
            await connection.execute(f'GRANT USAGE ON SCHEMA reporting TO "{cls.reader_name}"')
            await connection.execute(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO "{cls.reader_name}"'
            )
        finally:
            await connection.close()

    @classmethod
    async def remove_database(cls):
        if not cls.database_name.startswith("razbor_it_") or not cls.reader_name.startswith(
            "razbor_reader_"
        ):
            raise RuntimeError("Unexpected integration-test resource name")
        connection = await cls.admin_connection()
        try:
            if cls.created_database:
                await connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",
                    cls.database_name,
                )
                await connection.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}"')
            if cls.created_role:
                await connection.execute(f'DROP ROLE IF EXISTS "{cls.reader_name}"')
        finally:
            await connection.close()

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.remove_database())

    async def asyncSetUp(self):
        self.settings = Settings(
            database_url=self.database_url,
            secret_key="postgres-integration-secret-" * 2,
            secure_cookies=False,
            source_hosts=(self.base_url.host,),
            sql_timeout_ms=2000,
            sql_lock_timeout_ms=500,
        )
        self.source = Source(
            id="reporting-test-source",
            workspace_id="test",
            name="Reporting",
            host=self.base_url.host,
            port=self.base_url.port or 5432,
            database=self.database_name,
            username=self.reader_name,
            encrypted_password=SecretStore(self.settings.secret_key).encrypt(self.reader_password),
            schemas=["reporting"],
            ssl_mode="disable",
            catalog_version=0,
            policy_revision=1,
            enabled=True,
        )
        self.connections = SourceConnections(total=1, per_source=1, acquire_timeout=0.5)
        await inspect_source(self.source, self.settings, self.connections)
        self.source.policies = [
            {
                "schema": "reporting",
                "name": "sales",
                "columns": ["id", "store_code", "amount", "day", "time_value", "elapsed"],
                "store_column": "store_code",
                "shared": False,
            },
            {
                "schema": "reporting",
                "name": "refunds",
                "columns": ["id", "store_code", "sale_id", "amount"],
                "store_column": "store_code",
                "shared": False,
            },
            {
                "schema": "reporting",
                "name": "stores",
                "columns": ["code", "city"],
                "store_column": None,
                "shared": True,
            },
            {
                "schema": "reporting",
                "name": "slow_sales",
                "columns": ["id", "store_code", "amount"],
                "store_column": "store_code",
                "shared": False,
            },
        ]
        self.access_checks = 0

    async def check_access(self):
        self.access_checks += 1

    def executor(self, settings=None):
        return ScopedExecutor(
            self.source, ["A"], settings or self.settings, self.check_access, self.connections
        )

    async def wait_for_slow_query(self):
        connection = await self.admin_connection(application=True)
        try:
            async with asyncio.timeout(3):
                while not await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 AND application_name='razbor_reader' AND wait_event='PgSleep')",
                    self.database_name,
                ):
                    await asyncio.sleep(0.01)
        finally:
            await connection.close()

    async def test_real_catalog_decimal_dates_and_every_join_source_scope(self):
        tables = authorized_catalog(self.source)
        sales = next(table for table in tables if table.ref.name == "sales")
        self.assertEqual(sales.primary_key, ("id",))
        self.assertEqual(sales.foreign_keys[0].target.name, "stores")
        executor = self.executor()
        result = await executor.execute(
            'SELECT SUM(s."amount") AS "total" FROM "reporting"."sales" AS s', QueryContext()
        )
        self.assertEqual(result.rows, [["123456789012345.6790"]])
        self.assertEqual(result.column_types, ["numeric"])
        self.assertEqual(executor.execution["parameters"], [["A"]])
        self.assertIn("ANY(CAST($1 AS TEXT[]))", executor.execution["sql"])
        self.assertNotIn("secret", executor.execution["sql"])
        result = await self.executor().execute(
            'SELECT s."id", r."amount" FROM "reporting"."sales" AS s LEFT JOIN "reporting"."refunds" AS r ON s."id" = r."sale_id" ORDER BY s."id"',
            QueryContext(),
        )
        self.assertEqual(result.rows, [[1, "5.0000"], [2, None]])
        result = await self.executor().execute(
            'SELECT s."day", s."time_value", s."elapsed" FROM "reporting"."sales" AS s WHERE s."id" = 1',
            QueryContext(),
        )
        self.assertEqual(
            result.rows, [["2026-09-01", "12:34:56", {"days": 1, "seconds": 2, "microseconds": 0}]]
        )
        self.assertGreaterEqual(self.access_checks, 6)

    async def test_overview_comparison_and_daily_series_execute_real_scoped_sql(self):
        from backend.app.analytics.service import comparison_sql

        metric = SimpleNamespace(
            table_schema="reporting",
            table_name="sales",
            store_column="store_code",
            date_column="day",
            value_column="amount",
            aggregation="sum",
        )
        sql = comparison_sql(
            metric, date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 1), date(2026, 8, 31)
        )
        result = await self.executor().execute(sql, QueryContext())
        self.assertEqual(result.rows, [["A", "123456789012345.6790", 2, 2, None, 0, 0]])
        result = await self.executor().execute(
            'SELECT EXTRACT(YEAR FROM t."day"),EXTRACT(MONTH FROM t."day"),EXTRACT(DAY FROM t."day"),SUM(t."amount"),COUNT(t."amount") FROM "reporting"."sales" AS t GROUP BY EXTRACT(YEAR FROM t."day"),EXTRACT(MONTH FROM t."day"),EXTRACT(DAY FROM t."day")',
            QueryContext(),
        )
        self.assertEqual(
            sorted([int(part) for part in row[:3]] for row in result.rows),
            [[2026, 9, 1], [2026, 9, 30]],
        )
        self.assertEqual([row[4] for row in result.rows], [1, 1])

    async def test_full_http_workflow_with_ordinary_application_role(self):
        from backend.app.main import create_app
        from backend.app.jobs.service import heartbeat
        from backend.app.worker import Worker
        from sql_agent.contracts import AgentResult

        role = "razbor_http_" + uuid4().hex[:12]
        password = secrets.token_hex(24)
        admin = await self.admin_connection(application=True)
        await admin.execute(
            f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        await admin.execute(
            f'GRANT USAGE ON SCHEMA public TO "{role}"; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{role}"; GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"'
        )
        url = self.base_url.set(
            drivername="postgresql+asyncpg",
            database=self.database_name,
            username=role,
            password=password,
        ).render_as_string(hide_password=False)
        settings = replace(
            self.settings, database_url=url, bootstrap_token="http-test-setup", job_lease_seconds=30
        )
        app = create_app(settings)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        manager = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        try:
            async with app.state.database.engine.connect() as connection:
                flags = (
                    await connection.execute(
                        text(
                            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                        )
                    )
                ).one()
                self.assertFalse(any(flags))
            response = await client.post(
                "/api/v1/auth/bootstrap",
                json={
                    "email": "http-owner@example.org",
                    "name": "HTTP Owner",
                    "password": "HTTP-owner-password-123",
                    "workspace_name": "HTTP Network",
                    "bootstrap_token": "http-test-setup",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            session = response.json()
            client.headers["X-CSRF-Token"] = session["csrf_token"]
            prefix = "/api/v1/workspaces/" + session["workspaces"][0]["id"]
            stores = []
            for code in ("A", "B"):
                response = await client.post(
                    prefix + "/stores", json={"name": code, "code": code, "city": "City " + code}
                )
                self.assertEqual(response.status_code, 201, response.text)
                stores.append(response.json())
            response = await client.post(
                prefix + "/sources",
                json={
                    "name": "Actual PostgreSQL",
                    "host": self.base_url.host,
                    "port": self.base_url.port,
                    "database": self.database_name,
                    "username": self.reader_name,
                    "password": self.reader_password,
                    "ssl_mode": "disable",
                    "schemas": ["reporting"],
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            source_id = response.json()["id"]
            response = await client.post(prefix + "/sources/" + source_id + "/test")
            self.assertEqual(response.json()["status"], "ready", response.text)
            response = await client.put(
                prefix + "/sources/" + source_id + "/policies",
                json={
                    "tables": [
                        {
                            "schema": "reporting",
                            "name": "sales",
                            "columns": ["id", "store_code", "day", "amount"],
                            "store_column": "store_code",
                            "shared": False,
                        }
                    ]
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            response = await client.post(
                prefix + "/metrics",
                json={
                    "key": "revenue",
                    "name": "Revenue",
                    "description": "Sum of amount by business day",
                    "source_id": source_id,
                    "table_schema": "reporting",
                    "table_name": "sales",
                    "store_column": "store_code",
                    "date_column": "day",
                    "value_column": "amount",
                    "aggregation": "sum",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            metric_id = response.json()["id"]
            response = await client.get(
                prefix + "/stores/" + stores[0]["id"] + "/analytics",
                params={"metric_id": metric_id, "date_from": "2026-09-01", "date_to": "2026-09-30"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["series"][0]["date"], "2026-09-01")
            self.assertEqual(response.json()["series"][0]["value"], "123456789012345.6789")
            self.assertIsNone(response.json()["series"][1]["value"])
            response = await client.post(
                prefix + "/cases",
                json={
                    "title": "Actual financial basis",
                    "store_ids": [stores[0]["id"]],
                    "measurement": {
                        "metric_id": metric_id,
                        "date_from": "2026-09-01",
                        "date_to": "2026-09-30",
                    },
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            case_id = response.json()["id"]
            response = await client.get(prefix + "/cases/" + case_id)
            self.assertEqual(
                response.json()["measurements"][0]["overview"]["totals"]["actual"],
                "123456789012345.6790",
            )
            invite = (
                await client.post(
                    prefix + "/invitations",
                    json={
                        "email": "http-manager@example.org",
                        "role": "store_manager",
                        "all_stores": False,
                        "store_ids": [stores[0]["id"]],
                        "data_access": True,
                    },
                )
            ).json()
            response = await manager.post(
                "/api/v1/auth/invitations/accept",
                json={
                    "token": invite["token"],
                    "name": "HTTP Manager",
                    "password": "HTTP-manager-password-123",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            manager.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            self.assertEqual((await manager.get(prefix + "/cases/" + case_id)).status_code, 200)
            forbidden = await manager.get(
                prefix + "/stores/" + stores[1]["id"] + "/analytics",
                params={"metric_id": metric_id, "date_from": "2026-09-01", "date_to": "2026-09-30"},
            )
            self.assertEqual(forbidden.status_code, 403)

            class SQLContractEngine:
                model = "fixed-sql-contract-test"

                async def run(engine, run, source, stores, metrics, base, context, check_access):
                    executor = ScopedExecutor(
                        source,
                        [store.code for store in stores],
                        settings,
                        check_access,
                        app.state.source_connections,
                    )
                    sql = run.sql or 'SELECT SUM(t."amount") AS total FROM "reporting"."sales" AS t'
                    return AgentResult("success", result=await executor.execute(sql, context))

            async with app.state.database.sessions.begin() as db:
                await heartbeat(db, "http-worker", "ready", "fixed-sql-contract-test")
            conversation = (await manager.post(prefix + "/conversations", json={})).json()
            response = await manager.post(
                prefix + "/conversations/" + conversation["id"] + "/messages",
                json={
                    "question": "Revenue",
                    "source_id": source_id,
                    "store_ids": [stores[0]["id"]],
                    "version": 0,
                    "idempotency_key": "http-real-source-query",
                },
            )
            self.assertEqual(response.status_code, 202, response.text)
            run_id = response.json()["id"]
            worker = Worker(app.state.database, settings, SQLContractEngine())
            self.assertTrue(await worker.run_once())
            result = (await manager.get(prefix + "/runs/" + run_id)).json()
            self.assertEqual(result["status"], "succeeded", result)
            self.assertEqual(result["result"]["rows"], [["123456789012345.6790"]])
            report = (
                await manager.post(
                    prefix + "/reports", json={"title": "Actual report", "run_id": run_id}
                )
            ).json()
            response = await client.post(
                prefix + "/reports/" + report["id"] + "/refresh",
                json={"idempotency_key": "http-report-refresh"},
            )
            self.assertEqual(response.status_code, 202, response.text)
            self.assertTrue(await worker.run_once())
            history = (await manager.get(prefix + "/reports/" + report["id"])).json()
            self.assertEqual(history["refreshes"][0]["status"], "succeeded")
            question = (
                await client.post(
                    prefix + "/cases/" + case_id + "/questions",
                    json={
                        "body": "Confirm the period",
                        "assignee_id": (await manager.get("/api/v1/auth/session")).json()["user"][
                            "id"
                        ],
                    },
                )
            ).json()
            self.assertTrue((await manager.get(prefix + "/cases")).json()[0]["pending_for_me"])
            response = await manager.post(
                prefix + "/cases/" + case_id + "/questions/" + question["id"] + "/answer",
                json={"answer": "The period has been checked"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse((await manager.get(prefix + "/cases")).json()[0]["pending_for_me"])
        finally:
            await client.aclose()
            await manager.aclose()
            await app.state.database.close()
            await admin.execute(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')
            await admin.close()

    async def test_adversarial_predicate_wildcard_and_hidden_fields(self):
        result = await self.executor().execute(
            'SELECT s.* FROM "reporting"."sales" AS s WHERE s."store_code" = \'B\' OR 1 = 1 ORDER BY s."id"',
            QueryContext(),
        )
        self.assertEqual([row[0] for row in result.rows], [1, 2])
        self.assertNotIn("secret", result.columns)
        for sql in (
            'SELECT s."secret" FROM "reporting"."sales" AS s',
            'DELETE FROM "reporting"."sales"',
            'SELECT * FROM "public"."app_users"',
        ):
            with self.subTest(sql=sql), self.assertRaises(QueryError):
                await self.executor().execute(sql, QueryContext())
        result = await self.executor().execute(
            'SELECT SUM(s."amount") FROM "reporting"."sales" AS s WHERE s."store_code" = \'B\'',
            QueryContext(),
        )
        self.assertEqual(result.rows, [[None]])

    async def test_reader_connection_is_read_only_and_column_write_grants_rejected(self):
        async with self.connections.connect(self.source, self.settings) as connection:
            async with connection.transaction(readonly=True):
                self.assertEqual(await connection.fetchval("SHOW transaction_read_only"), "on")
                with self.assertRaises(asyncpg.PostgresError):
                    await connection.execute("UPDATE reporting.sales SET amount=0")
        connection = await self.admin_connection(application=True)
        try:
            await connection.execute(
                f'GRANT UPDATE(amount) ON reporting.sales TO "{self.reader_name}"'
            )
            with self.assertRaises(AppError) as caught:
                await inspect_source(self.source, self.settings, self.connections)
            self.assertEqual(caught.exception.code, "unsafe_role")
        finally:
            await connection.execute(
                f'REVOKE UPDATE(amount) ON reporting.sales FROM "{self.reader_name}"'
            )
            await connection.close()

    async def test_timeout_releases_connection_capacity(self):
        executor = self.executor(replace(self.settings, sql_timeout_ms=100))
        started = time.monotonic()
        with self.assertRaises(QueryError) as caught:
            await executor.execute(
                'SELECT SUM(s."amount") FROM "reporting"."slow_sales" AS s', QueryContext()
            )
        self.assertEqual(caught.exception.code, "sql_timeout")
        self.assertLess(time.monotonic() - started, 3)
        result = await self.executor().execute(
            'SELECT COUNT(*) FROM "reporting"."sales" AS s', QueryContext()
        )
        self.assertEqual(result.rows, [[2]])

    async def test_task_cancellation_releases_native_query_and_pool_slot(self):
        executor = self.executor(replace(self.settings, sql_timeout_ms=10000))
        pending = asyncio.create_task(
            executor.execute(
                'SELECT SUM(s."amount") FROM "reporting"."slow_sales" AS s', QueryContext()
            )
        )
        try:
            await self.wait_for_slow_query()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=3)
            result = await self.executor().execute(
                'SELECT COUNT(*) FROM "reporting"."sales" AS s', QueryContext()
            )
            self.assertEqual(result.rows, [[2]])
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_context_cancellation_and_deadline_interrupt_running_postgres_query(self):
        for cancellation in ("event", "deadline"):
            with self.subTest(cancellation=cancellation):
                context = QueryContext()
                executor = self.executor(replace(self.settings, sql_timeout_ms=10000))
                pending = asyncio.create_task(
                    executor.execute(
                        'SELECT SUM(s."amount") FROM "reporting"."slow_sales" AS s', context
                    )
                )
                try:
                    await self.wait_for_slow_query()
                    started = time.monotonic()
                    if cancellation == "event":
                        context.cancelled.set()
                    else:
                        context.deadline = started
                    with self.assertRaises(QueryError) as caught:
                        await asyncio.wait_for(pending, timeout=3)
                    self.assertEqual(
                        caught.exception.code,
                        "cancelled" if cancellation == "event" else "query_timeout",
                    )
                    self.assertLess(time.monotonic() - started, 3)
                    result = await self.executor().execute(
                        'SELECT COUNT(*) FROM "reporting"."sales" AS s', QueryContext()
                    )
                    self.assertEqual(result.rows, [[2]])
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)

    async def test_shared_connection_limiter_and_statement_result_limits(self):
        async with self.connections.connect(self.source, self.settings):
            with self.assertRaises(QueryError) as caught:
                await self.executor().execute(
                    'SELECT COUNT(*) FROM "reporting"."sales" AS s', QueryContext()
                )
            self.assertEqual(caught.exception.code, "source_busy")
        result = await self.executor(replace(self.settings, max_result_rows=1)).execute(
            'SELECT s."id" FROM "reporting"."sales" AS s ORDER BY s."id"', QueryContext()
        )
        self.assertEqual(result.rows, [[1]])
        self.assertTrue(result.truncated)

    async def test_real_migrations_and_cross_workspace_constraints(self):
        database = Database(self.database_url)
        try:
            run_id, _, _ = await self.seed_job(database)
            async with database.sessions.begin() as db:
                run = await db.get(QueryRun, run_id)
                user_id = run.user_id
                other = Workspace(id=str(uuid4()), name="Other")
                db.add(other)
                await db.flush()
                wrong_workspace = other.id
                db.add(
                    Membership(
                        workspace_id=wrong_workspace,
                        user_id=user_id,
                        role="director",
                        all_stores=True,
                        data_access=True,
                    )
                )
            from backend.app.analytics.models import Report

            with self.assertRaises(IntegrityError):
                async with database.sessions.begin() as db:
                    db.add(
                        Report(
                            workspace_id=wrong_workspace,
                            title="Forged",
                            run_id=run_id,
                            created_by=user_id,
                        )
                    )
                    await db.flush()
        finally:
            await database.close()

    async def seed_job(self, database):
        suffix = uuid4().hex
        async with database.sessions.begin() as db:
            user = User(
                id=str(uuid4()), email=suffix + "@example.org", name="Owner", password_hash="test"
            )
            workspace = Workspace(id=str(uuid4()), name="Test network")
            db.add_all([user, workspace])
            await db.flush()
            db.add(
                Membership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="director",
                    all_stores=True,
                    data_access=True,
                    owner=True,
                )
            )
            source = Source(
                id=str(uuid4()),
                workspace_id=workspace.id,
                name="Test",
                host=self.source.host,
                port=self.source.port,
                database=self.source.database,
                username=self.source.username,
                encrypted_password=self.source.encrypted_password,
                schemas=["reporting"],
                catalog_version=1,
                policy_revision=1,
                catalog=self.source.catalog,
                policies=self.source.policies,
                status="ready",
            )
            store = Store(id=str(uuid4()), workspace_id=workspace.id, code="A", name="A")
            db.add_all([source, store])
            await db.flush()
            conversation = Conversation(id=str(uuid4()), workspace_id=workspace.id, user_id=user.id)
            db.add(conversation)
            await db.flush()
            run = QueryRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                user_id=user.id,
                conversation_id=conversation.id,
                source_id=source.id,
                question="Revenue",
                store_ids=[store.id],
                context={"catalog_version": 1, "policy_revision": 1},
                idempotency_key=suffix,
                request_hash=suffix,
                membership_revision=1,
                conversation_version=1,
            )
            db.add(run)
            await db.flush()
            job = Job(id=str(uuid4()), run_id=run.id)
            db.add(job)
            await db.flush()
            return run.id, job.id, source.id

    async def test_expired_worker_cannot_renew_or_publish_and_advisory_lock_is_exclusive(self):
        database = Database(self.database_url)
        try:
            run_id, job_id, _ = await self.seed_job(database)
            async with database.sessions.begin() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="running",
                        lease_token="test-fence",
                        lease_until=utcnow() - timedelta(seconds=1),
                    )
                )
                await db.execute(
                    update(QueryRun).where(QueryRun.id == run_id).values(status="running")
                )
            async with database.sessions.begin() as db:
                self.assertIsNone(await renew_lease(db, job_id, "test-fence", 30))
                self.assertFalse(
                    await finish_job(
                        db,
                        job_id,
                        "test-fence",
                        {"status": "succeeded", "result": {"rows": [[99]]}},
                    )
                )
            async with database.sessions() as db:
                run = await db.get(QueryRun, run_id)
                self.assertIsNone(run.result)
                self.assertEqual(run.status, "running")
        finally:
            await database.close()
        first, second = (
            await self.admin_connection(application=True),
            await self.admin_connection(application=True),
        )
        try:
            self.assertTrue(await first.fetchval("SELECT pg_try_advisory_lock(7272626)"))
            self.assertFalse(await second.fetchval("SELECT pg_try_advisory_lock(7272626)"))
            await first.execute("SELECT pg_advisory_unlock(7272626)")
            self.assertTrue(await second.fetchval("SELECT pg_try_advisory_lock(7272626)"))
        finally:
            await first.close()
            await second.close()

    async def test_cancel_waiting_for_publish_cannot_overwrite_committed_success(self):
        database = Database(self.database_url)
        pending = None
        try:
            run_id, job_id, _ = await self.seed_job(database)
            async with database.sessions.begin() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="running",
                        lease_token="publish-fence",
                        lease_until=utcnow() + timedelta(seconds=30),
                    )
                )
                await db.execute(
                    update(QueryRun).where(QueryRun.id == run_id).values(status="running")
                )
            blocker = await self.admin_connection(application=True)
            try:
                started = asyncio.Event()
                cancellation_pid = []

                async def cancellation():
                    async with database.sessions.begin() as db:
                        run = await db.get(QueryRun, run_id)
                        member = await db.scalar(
                            select(Membership).where(
                                Membership.workspace_id == run.workspace_id,
                                Membership.user_id == run.user_id,
                            )
                        )
                        cancellation_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
                        started.set()
                        return await cancel(run_id, from_membership(member), db)

                async with database.sessions.begin() as db:
                    outcome = {
                        "status": "succeeded",
                        "sql": 'SELECT COUNT(*) FROM "reporting"."sales"',
                        "result": {
                            "columns": [{"name": "count", "type": "int8"}],
                            "rows": [[2]],
                            "truncated": False,
                            "row_count": 1,
                        },
                    }
                    self.assertTrue(await finish_job(db, job_id, "publish-fence", outcome))
                    await db.flush()
                    pending = asyncio.create_task(cancellation())
                    await asyncio.wait_for(started.wait(), timeout=3)
                    async with asyncio.timeout(3):
                        while not await blocker.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')",
                            cancellation_pid[0],
                        ):
                            await asyncio.sleep(0.01)
                    self.assertFalse(pending.done())
                result = await asyncio.wait_for(pending, timeout=3)
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(result["result"]["rows"], [[2]])
                async with database.sessions() as db:
                    run = await db.get(QueryRun, run_id)
                    job = await db.get(Job, job_id)
                    events = (
                        await db.scalars(
                            select(RunEvent)
                            .where(RunEvent.run_id == run_id)
                            .order_by(RunEvent.sequence)
                        )
                    ).all()
                    self.assertEqual((run.status, job.status), ("succeeded", "succeeded"))
                    self.assertEqual([event.sequence for event in events], [1])
            finally:
                await blocker.close()
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await database.close()
