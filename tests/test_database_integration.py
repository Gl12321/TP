from importlib.util import find_spec
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4
from types import SimpleNamespace


TEST_URL = os.getenv("TEXT2SQL_TEST_DATABASE_URL")
READ_URL = os.getenv("TEXT2SQL_TEST_READ_URL")
AVAILABLE = bool(TEST_URL and find_spec("sqlalchemy") and find_spec("psycopg2"))


@unittest.skipUnless(AVAILABLE, "Нужны тестовый PostgreSQL URL и установленные драйверы")
class ImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from src.database.identifiers import quote_identifier
        self.schema = "text2sql_test_" + uuid4().hex[:16]
        self.quoted = quote_identifier(self.schema)
        self.engine = create_engine(TEST_URL)
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "source.db"
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._remove_schema)

    def _remove_schema(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {self.quoted} CASCADE")

    def _sqlite(self, script):
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(script)
        finally:
            connection.close()

    def test_failed_data_transfer_preserves_previous_schema(self):
        from src.database.migration.importer import DatabaseImporter
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {self.quoted}")
            connection.exec_driver_sql(f"CREATE TABLE {self.quoted}.previous(value TEXT)")
            connection.exec_driver_sql(f"INSERT INTO {self.quoted}.previous VALUES ('retained')")
        self._sqlite("CREATE TABLE replacement(quantity INTEGER); INSERT INTO replacement VALUES ('not an integer');")
        with self.assertRaises(Exception):
            DatabaseImporter(TEST_URL).migrate_db(self.schema, str(self.path))
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql(f"SELECT value FROM {self.quoted}.previous").scalar_one(), "retained")

    def test_cycles_composite_keys_and_empty_sequence(self):
        from src.database.migration.importer import DatabaseImporter
        self._sqlite('''
            CREATE TABLE a(id INTEGER PRIMARY KEY, other INTEGER REFERENCES b(id));
            CREATE TABLE b(id INTEGER PRIMARY KEY, other INTEGER REFERENCES a(id));
            INSERT INTO a VALUES(5, 7);
            INSERT INTO b VALUES(7, 5);
            CREATE TABLE empty(id INTEGER PRIMARY KEY);
            CREATE TABLE composite(a INTEGER, b INTEGER, PRIMARY KEY(a,b));
            INSERT INTO composite VALUES(1, 2);
            CREATE TABLE text_key(id TEXT PRIMARY KEY);
            INSERT INTO text_key VALUES('plain');
        ''')
        DatabaseImporter(TEST_URL).migrate_db(self.schema, str(self.path))
        with self.engine.begin() as connection:
            self.assertEqual(connection.exec_driver_sql(f"INSERT INTO {self.quoted}.empty DEFAULT VALUES RETURNING id").scalar_one(), 1)
            self.assertEqual(connection.exec_driver_sql(f"INSERT INTO {self.quoted}.a DEFAULT VALUES RETURNING id").scalar_one(), 6)
            self.assertEqual(connection.exec_driver_sql(f"SELECT a, b FROM {self.quoted}.composite").one(), (1, 2))
            self.assertEqual(connection.exec_driver_sql(f"SELECT id FROM {self.quoted}.text_key").scalar_one(), "plain")

    def test_foreign_key_can_reference_explicit_unique_index(self):
        from src.database.migration.importer import DatabaseImporter


        self._sqlite('''
            CREATE TABLE z_parent(code TEXT NOT NULL);
            CREATE UNIQUE INDEX parent_code_unique ON z_parent(code);
            CREATE TABLE a_child(id INTEGER PRIMARY KEY, code TEXT REFERENCES z_parent(code));
            INSERT INTO z_parent VALUES('valid');
            INSERT INTO a_child VALUES(1, 'valid');
        ''')
        DatabaseImporter(TEST_URL).migrate_db(self.schema, str(self.path))
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql(
                f"SELECT p.code FROM {self.quoted}.a_child c "
                f"JOIN {self.quoted}.z_parent p ON p.code = c.code"
            ).scalar_one(), "valid")
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.exec_driver_sql(
                f"INSERT INTO {self.quoted}.a_child VALUES (2, 'missing')"
            )

    def test_replacement_preserves_external_view_and_foreign_key(self):
        from src.database.identifiers import quote_identifier
        from src.database.migration.importer import DatabaseImporter
        from src.domain.query import QueryError
        external = quote_identifier("text2sql_test_" + uuid4().hex[:16])

        def remove_external():
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {external} CASCADE")

        self.addCleanup(remove_external)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {self.quoted}")
            connection.exec_driver_sql(f"CREATE SCHEMA {external}")
            connection.exec_driver_sql(f"CREATE TABLE {self.quoted}.parent(id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(f"INSERT INTO {self.quoted}.parent VALUES(1)")
            connection.exec_driver_sql(f"CREATE VIEW {external}.summary AS SELECT * FROM {self.quoted}.parent")
            connection.exec_driver_sql(f"CREATE TABLE {external}.child(id INTEGER REFERENCES {self.quoted}.parent(id))")
        self._sqlite("CREATE TABLE replacement(id INTEGER PRIMARY KEY);")
        invoked = []
        with self.assertRaises(QueryError) as error:
            DatabaseImporter(TEST_URL).migrate_db(self.schema, str(self.path), before_replace=lambda: invoked.append(True))
        self.assertEqual(error.exception.code, "external_dependencies")
        self.assertEqual(invoked, [])
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql(f"SELECT id FROM {external}.summary").scalar_one(), 1)
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.exec_driver_sql(f"INSERT INTO {external}.child VALUES (99)")

    def test_numeric_values_are_not_rounded_by_sqlite_declared_scale(self):
        from decimal import Decimal
        from src.database.migration.importer import DatabaseImporter
        self._sqlite('''
            CREATE TABLE amounts(fraction NUMERIC(10,2), counter NUMERIC);
            INSERT INTO amounts VALUES(1.2345, 9223372036854775807);
        ''')
        DatabaseImporter(TEST_URL).migrate_db(self.schema, str(self.path))
        with self.engine.connect() as connection:
            values = connection.exec_driver_sql(
                f"SELECT fraction, counter FROM {self.quoted}.amounts"
            ).one()
            self.assertEqual(values, (Decimal("1.2345"), Decimal("9223372036854775807")))

    def test_invalid_boolean_rolls_back_instead_of_becoming_true(self):
        from sqlalchemy.exc import StatementError
        from src.database.migration.importer import DatabaseImporter
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {self.quoted}")
            connection.exec_driver_sql(f"CREATE TABLE {self.quoted}.previous(value INTEGER)")
            connection.exec_driver_sql(f"INSERT INTO {self.quoted}.previous VALUES (42)")
        self._sqlite("CREATE TABLE flags(enabled BOOLEAN); INSERT INTO flags VALUES(2);")
        with self.assertRaises(StatementError):
            DatabaseImporter(TEST_URL).migrate_db(self.schema, str(self.path))
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql(
                f"SELECT value FROM {self.quoted}.previous"
            ).scalar_one(), 42)


@unittest.skipUnless(AVAILABLE and READ_URL and find_spec("asyncpg"), "Нужно отдельное тестовое подключение чтения и asyncpg")
class ExecutorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.engine import make_url
        from src.database.client import PostgresClient
        from src.database.executor import SQLExecutor
        from src.database.identifiers import quote_identifier
        self.client = PostgresClient(TEST_URL, READ_URL)
        self.executor = SQLExecutor(self.client, max_rows=2)
        self.schema_name = "text2sql_test_" + uuid4().hex[:16]
        self.schema = quote_identifier(self.schema_name)
        reader = quote_identifier(make_url(READ_URL).username)
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self._remove_schema)
        async with self.client.admin_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {self.schema}"))
            await connection.execute(text(f"CREATE TABLE {self.schema}.samples(id INTEGER)"))
            await connection.execute(text(f"INSERT INTO {self.schema}.samples VALUES (1),(2),(3)"))
            await connection.execute(text(f"GRANT USAGE ON SCHEMA {self.schema} TO {reader}"))
            await connection.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {reader}"))

    async def _remove_schema(self):
        from sqlalchemy import text
        async with self.client.admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE"))

    async def test_limit_and_literal_are_preserved(self):
        from src.domain.query import QueryContext
        query = f"SELECT id, ':value;a' AS marker FROM {self.schema}.samples ORDER BY id"
        result = await self.executor.execute(query, QueryContext())
        self.assertEqual(result.rows, [[1, ":value;a"], [2, ":value;a"]])
        self.assertTrue(result.truncated)

    async def test_cancellation_releases_connection(self):
        from src.domain.query import QueryContext, QueryError
        context = QueryContext()
        timer = asyncio.get_running_loop().call_later(0.1, context.cancelled.set)
        try:
            with self.assertRaises(QueryError) as raised:
                await asyncio.wait_for(self.executor.execute("SELECT pg_sleep(30)", context), timeout=3)
            self.assertEqual(raised.exception.code, "cancelled")
        finally:
            timer.cancel()
        result = await self.executor.execute("SELECT 1", QueryContext())
        self.assertEqual(result.rows, [[1]])

    async def test_timeout_does_not_enter_correction_loop(self):
        from src.domain.query import QueryContext, QueryError
        self.client.statement_timeout_ms = 50
        with self.assertRaises(QueryError) as raised:
            await self.executor.execute("SELECT pg_sleep(1)", QueryContext())
        self.assertEqual(raised.exception.code, "query_timeout")
        self.assertFalse(raised.exception.retryable)

    async def test_readonly_is_enforced_even_without_ast_validator(self):
        from src.domain.query import QueryContext, QueryError
        with self.assertRaises(QueryError) as raised:
            await self.executor.execute(f"DELETE FROM {self.schema}.samples", QueryContext())
        self.assertEqual(raised.exception.code, "query_forbidden")

    async def test_metadata_preserves_fk_target_visible_in_search_path(self):
        from src.database.identifiers import quote_identifier
        from src.database.metadata import SchemaReader
        target_name = "text2sql_test_" + uuid4().hex[:16]
        target = quote_identifier(target_name)

        async def cleanup():
            async with self.client.admin_engine.begin() as connection:
                await connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {target} CASCADE")

        self.addAsyncCleanup(cleanup)
        async with self.client.admin_engine.begin() as connection:
            await connection.exec_driver_sql(f"CREATE SCHEMA {target}")
            await connection.exec_driver_sql(f"CREATE TABLE {target}.parent(id INTEGER PRIMARY KEY)")
            await connection.exec_driver_sql(
                f"CREATE TABLE {self.schema}.child(parent_id INTEGER REFERENCES {target}.parent(id))"
            )
        real_engine = self.client.admin_engine

        class VisibleTargetEngine:
            @asynccontextmanager
            async def connect(self):
                async with real_engine.connect() as connection:
                    await connection.exec_driver_sql(f"SET LOCAL search_path = {target}, pg_catalog")
                    yield connection

        reader = SchemaReader(SimpleNamespace(admin_engine=VisibleTargetEngine()))
        tables = await reader.get_tables([self.schema_name])
        child = next(table for table in tables if table.ref.name == "child")
        self.assertEqual(child.foreign_keys[0].target.schema, target_name)
        self.assertEqual(child.foreign_keys[0].target.name, "parent")


if __name__ == "__main__":
    unittest.main()
