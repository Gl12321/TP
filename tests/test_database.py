from importlib.util import find_spec
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.database.identifiers import quote_identifier, validate_schema_name
from src.database.migration.validation import configure_sqlite, validate_sqlite, validate_sqlite_path


HAS_SQLALCHEMY = find_spec("sqlalchemy") is not None


class IdentifierTests(unittest.TestCase):
    def test_quote_preserves_odd_identifiers(self):
        self.assertEqual(quote_identifier('sales"; DROP SCHEMA x; --'), '"sales""; DROP SCHEMA x; --"')
        self.assertEqual(validate_schema_name("Отчёт 2026"), "Отчёт 2026")

    def test_reserved_schemas(self):
        for name in ("public", "information_schema", "pg_catalog", "pg_temp_1", "pg_toast"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_schema_name(name)

    def test_limits_are_utf8_bytes(self):
        for name in ("", "abc\x00def", "я" * 32):
            with self.subTest(name=name), self.assertRaises(ValueError):
                quote_identifier(name)
        self.assertEqual(quote_identifier("я" * 31), '"' + "я" * 31 + '"')


class SQLitePreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "source.db"
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)

    def test_header_and_size(self):
        self.connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
        self.connection.commit()
        self.assertEqual(validate_sqlite_path(self.path, 1_000_000), self.path.resolve())
        with self.assertRaises(ValueError):
            validate_sqlite_path(self.path, 100)
        fake = self.path.with_name("fake.db")
        fake.write_bytes(b"x" * 200)
        with self.assertRaises(ValueError):
            validate_sqlite_path(fake, 1_000)

    def test_quoted_names_and_foreign_keys(self):
        self.connection.executescript('''
            CREATE TABLE "sales details" (id INTEGER PRIMARY KEY);
            CREATE TABLE child(id INTEGER PRIMARY KEY, parent INTEGER REFERENCES "sales details"(id));
            INSERT INTO "sales details" VALUES (1);
            INSERT INTO child VALUES (2, 1);
        ''')
        configure_sqlite(self.connection)
        self.assertEqual(validate_sqlite(self.connection), ["child", "sales details"])
        with self.assertRaises(sqlite3.OperationalError):
            self.connection.execute("DELETE FROM child")

    def test_broken_foreign_keys_fail_before_import(self):
        self.connection.executescript('''
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(parent INTEGER REFERENCES parent(id));
            INSERT INTO child VALUES(99);
        ''')
        with self.assertRaisesRegex(ValueError, "внешние ключи"):
            validate_sqlite(self.connection)

    def test_generated_columns_are_not_silently_lost(self):
        self.connection.execute("CREATE TABLE generated(x INTEGER, y INTEGER AS (x + 1))")
        with self.assertRaisesRegex(ValueError, "Вычисляемые"):
            validate_sqlite(self.connection)

    def test_expression_index_is_not_silently_lost(self):
        self.connection.executescript("CREATE TABLE words(word TEXT); CREATE UNIQUE INDEX folded ON words(lower(word));")
        with self.assertRaisesRegex(ValueError, "Индексы"):
            validate_sqlite(self.connection)

    def test_custom_unique_collation_is_not_silently_lost(self):
        self.connection.execute("CREATE TABLE words(word TEXT COLLATE NOCASE UNIQUE)")
        with self.assertRaisesRegex(ValueError, "COLLATE"):
            validate_sqlite(self.connection)

    def test_table_and_column_bounds(self):
        self.connection.executescript("CREATE TABLE a(x INTEGER, y TEXT); CREATE TABLE b(z INTEGER);")
        with self.assertRaises(ValueError):
            validate_sqlite(self.connection, max_tables=1)
        with self.assertRaises(ValueError):
            validate_sqlite(self.connection, max_columns=1)

    def test_empty_database_rejected(self):
        with self.assertRaises(ValueError):
            validate_sqlite(self.connection)


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy не установлен; установка не выполняется")
class DatabaseUnitTests(unittest.TestCase):
    def test_literal_sql_does_not_reinterpret_colons_or_semicolons(self):
        from sqlalchemy.dialects.postgresql import asyncpg
        from src.database.executor import _ValidatedSQL
        query = r"SELECT ':value', 'a;b', '\:value', 12::integer"
        compiled = _ValidatedSQL(query).compile(dialect=asyncpg.dialect())
        self.assertEqual(str(compiled), query)
        self.assertFalse(compiled.params)

    def test_metadata_clone_does_not_mutate_sqlite_source(self):
        from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
        from src.database.migration.mapping import clone_metadata
        source = MetaData()
        original = Table("parent", source, Column("id", Integer, primary_key=True))
        child = Table("child", source, Column("id", Integer, primary_key=True),
                      Column("parent", Integer, ForeignKey("parent.id")), Column("text", String(10)))
        target = clone_metadata(source, "report")
        self.assertIsNone(original.schema)
        self.assertIsNone(child.schema)
        self.assertEqual(str(original.c.id.type), "INTEGER")
        self.assertEqual(str(target.tables["report.parent"].c.id.type), "BIGINT")
        fk = next(iter(target.tables["report.child"].foreign_keys))
        self.assertIs(fk.column, target.tables["report.parent"].c.id)

    def test_sqlite_defaults_are_explicitly_translated(self):
        from src.database.migration.mapping import _default
        self.assertEqual(_default("(('it''s fine'))"), "'it''s fine'")
        self.assertEqual(_default("X'00ff'"), "'\\x00ff'::bytea")
        self.assertEqual(_default("CURRENT_TIMESTAMP"), "CURRENT_TIMESTAMP")
        with self.assertRaises(ValueError):
            _default("datetime('now')")

    def test_partial_index_keeps_predicate(self):
        from sqlalchemy import Column, Index, Integer, MetaData, Table, text
        from src.database.migration.mapping import clone_metadata
        source = MetaData()
        table = Table("items", source, Column("id", Integer), Column("active", Integer))
        Index("active_id", table.c.id, unique=True, sqlite_where=text("active = 1"))
        cloned = clone_metadata(source, "report")
        index = next(iter(cloned.tables["report.items"].indexes))
        self.assertEqual(str(index.dialect_options["postgresql"]["where"]), "active = 1")

    def test_error_classification(self):
        from sqlalchemy.exc import DBAPIError
        from src.database.executor import _query_error
        for code, retryable in [("42703", True), ("57014", False), ("08006", False), ("42501", False)]:
            original = Exception("server error")
            original.sqlstate = code
            result = _query_error(DBAPIError("SELECT", {}, original))
            self.assertEqual(result.retryable, retryable)


if __name__ == "__main__":
    unittest.main()
