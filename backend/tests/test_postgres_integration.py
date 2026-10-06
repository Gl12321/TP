import asyncio
from dataclasses import replace
import os
from pathlib import Path
import secrets
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
import asyncpg
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from backend.app.access.models import Membership, User, Workspace
from backend.app.collaboration.models import Case
from backend.app.infrastructure.config import Settings
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import SecretStore
from backend.app.sources.models import Source
from backend.app.sources.service import ScopedExecutor, SourceConnections, inspect_source
from sql_agent.contracts import QueryContext, QueryError


TEST_URL = os.getenv("RAZBOR_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set RAZBOR_TEST_DATABASE_URL to a local test cluster")
class PostgreSQLIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = make_url(TEST_URL)
        if cls.base_url.drivername not in {"postgresql", "postgresql+asyncpg"} or (
            cls.base_url.host not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("Integration tests require an explicit local PostgreSQL cluster")
        suffix = uuid4().hex[:16]
        cls.database_name, cls.reader_name = "razbor_it_" + suffix, "razbor_reader_" + suffix
        cls.password = secrets.token_hex(24)
        cls.created_database = cls.created_role = False
        cls.database_url = cls.base_url.set(
            drivername="postgresql+asyncpg", database=cls.database_name
        ).render_as_string(hide_password=False)
        cls.addClassCleanup(lambda: asyncio.run(cls.remove_database()))
        asyncio.run(cls.create_database())
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "backend/alembic.ini"))
        config.set_main_option("script_location", str(root / "backend/migrations"))
        config.set_main_option("sqlalchemy.url", cls.database_url.replace("%", "%%"))
        command.upgrade(config, "head")

    @classmethod
    async def connect(cls, *, application=False):
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
        connection = await cls.connect()
        try:
            await connection.execute(f'CREATE DATABASE "{cls.database_name}"')
            cls.created_database = True
            await connection.execute(
                f"CREATE ROLE \"{cls.reader_name}\" LOGIN PASSWORD '{cls.password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            cls.created_role = True
        finally:
            await connection.close()
        connection = await cls.connect(application=True)
        try:
            await connection.execute("""
                CREATE SCHEMA reporting;
                CREATE TABLE reporting.sales(
                    id BIGINT PRIMARY KEY, store_code TEXT, amount NUMERIC(20,4), secret TEXT
                );
                CREATE TABLE reporting.refunds(
                    sale_id BIGINT, store_code TEXT, amount NUMERIC(20,4)
                );
                INSERT INTO reporting.sales VALUES
                    (1,'A',123456789012345.6789,'hidden'),(2,'A',0.0001,'hidden'),(3,'B',999,'hidden');
                INSERT INTO reporting.refunds VALUES (1,'A',5),(1,'B',500);
                CREATE VIEW reporting.slow_sales AS
                    SELECT id,store_code,amount+(SELECT 0::NUMERIC FROM pg_sleep(5)) AS amount
                    FROM reporting.sales;
            """)
            await connection.execute(f'GRANT USAGE ON SCHEMA reporting TO "{cls.reader_name}"')
            await connection.execute(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO "{cls.reader_name}"'
            )
        finally:
            await connection.close()

    @classmethod
    async def remove_database(cls):
        if not cls.created_database and not cls.created_role:
            return
        connection = await cls.connect()
        try:
            if cls.created_database:
                await connection.execute(f'DROP DATABASE "{cls.database_name}" WITH (FORCE)')
            if cls.created_role:
                await connection.execute(f'DROP ROLE "{cls.reader_name}"')
        finally:
            await connection.close()

    async def asyncSetUp(self):
        self.settings = Settings(
            database_url=self.database_url,
            secret_key="postgres-integration-secret-" * 2,
            secure_cookies=False,
            source_hosts=(self.base_url.host,),
            sql_timeout_ms=1000,
        )
        self.source = Source(
            id="reporting",
            workspace_id="test",
            name="Reporting",
            host=self.base_url.host,
            port=self.base_url.port or 5432,
            database=self.database_name,
            username=self.reader_name,
            encrypted_password=SecretStore(self.settings.secret_key).encrypt(self.password),
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
                "name": name,
                "columns": columns,
                "store_column": "store_code",
                "shared": False,
            }
            for name, columns in (
                ("sales", ["id", "store_code", "amount"]),
                ("refunds", ["sale_id", "store_code", "amount"]),
                ("slow_sales", ["id", "store_code", "amount"]),
            )
        ]

    async def check_access(self):
        return None

    def executor(self, **settings):
        return ScopedExecutor(
            self.source,
            ["A"],
            replace(self.settings, **settings),
            self.check_access,
            self.connections,
        )

    async def test_readonly_role_decimal_and_scopes_on_both_join_sides(self):
        executor = self.executor()
        result = await executor.execute(
            "SELECT SUM(s.amount) FROM reporting.sales AS s", QueryContext()
        )
        self.assertEqual(result.rows, [["123456789012345.6790"]])
        self.assertEqual(result.column_types, ["numeric"])
        self.assertEqual(executor.execution["parameters"], [["A"]])
        self.assertNotIn("secret", executor.execution["sql"])
        result = await executor.execute(
            "SELECT s.id,r.amount FROM reporting.sales AS s LEFT JOIN reporting.refunds AS r "
            "ON s.id=r.sale_id ORDER BY s.id",
            QueryContext(),
        )
        self.assertEqual(result.rows, [[1, "5.0000"], [2, None]])
        async with self.connections.connect(self.source, self.settings) as connection:
            async with connection.transaction(readonly=True):
                self.assertEqual(await connection.fetchval("SHOW transaction_read_only"), "on")
                with self.assertRaises(asyncpg.PostgresError):
                    await connection.execute("UPDATE reporting.sales SET amount=0")
        admin = await self.connect(application=True)
        try:
            await admin.execute(f'GRANT UPDATE(amount) ON reporting.sales TO "{self.reader_name}"')
            with self.assertRaises(AppError) as caught:
                await inspect_source(self.source, self.settings, self.connections)
            self.assertEqual(caught.exception.code, "unsafe_role")
        finally:
            await admin.execute(
                f'REVOKE UPDATE(amount) ON reporting.sales FROM "{self.reader_name}"'
            )
            await admin.close()

    async def test_timeout_and_cancellation_release_connection_capacity(self):
        slow_sql = "SELECT SUM(s.amount) FROM reporting.slow_sales AS s"
        with self.assertRaises(QueryError) as caught:
            await self.executor(sql_timeout_ms=100).execute(slow_sql, QueryContext())
        self.assertEqual(caught.exception.code, "sql_timeout")
        context = QueryContext()
        pending = asyncio.create_task(
            self.executor(sql_timeout_ms=10000).execute(slow_sql, context)
        )
        admin = await self.connect(application=True)
        try:
            async with asyncio.timeout(3):
                while not await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 "
                    "AND application_name='razbor_reader' AND wait_event='PgSleep')",
                    self.database_name,
                ):
                    await asyncio.sleep(0.01)
            context.cancelled.set()
            with self.assertRaises(QueryError) as caught:
                await asyncio.wait_for(pending, timeout=3)
            self.assertEqual(caught.exception.code, "cancelled")
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await admin.close()
        result = await self.executor(max_result_rows=1).execute(
            "SELECT s.id FROM reporting.sales AS s ORDER BY s.id", QueryContext()
        )
        self.assertEqual(result.rows, [[1]])
        self.assertTrue(result.truncated)

    async def test_migrated_database_rejects_cross_workspace_membership(self):
        database = Database(self.database_url)
        try:
            async with database.sessions.begin() as db:
                db.add_all(
                    [
                        Workspace(id="own", name="Own"),
                        Workspace(id="other", name="Other"),
                        User(
                            id="author",
                            email="author@test.local",
                            name="Author",
                            password_hash="unused",
                        ),
                    ]
                )
                await db.flush()
                db.add(Membership(workspace_id="own", user_id="author", role="director"))
            async with database.sessions.begin() as db:
                db.add(Case(workspace_id="own", created_by="author", title="Valid", store_ids=[]))
            with self.assertRaises(IntegrityError) as caught:
                async with database.sessions.begin() as db:
                    db.add(
                        Case(
                            workspace_id="other", created_by="author", title="Forged", store_ids=[]
                        )
                    )
            self.assertIn("fk_case_creator_member", str(caught.exception.orig))
        finally:
            await database.close()
