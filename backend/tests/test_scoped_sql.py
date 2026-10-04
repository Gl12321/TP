import json
import sqlite3
import unittest

import sqlglot
from sqlglot import exp

from backend.app.sources.service import scoped_sql
from sql_agent.contracts import Column, QueryError, TableRef, TableSchema


class ScopedSQLTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("ATTACH DATABASE ':memory:' AS reporting")
        self.connection.executescript(
            "CREATE TABLE reporting.sales (id INTEGER,store_code TEXT,amount NUMERIC,secret TEXT); CREATE TABLE reporting.notes (sale_id INTEGER,store_code TEXT,label TEXT); INSERT INTO reporting.sales VALUES (1,'A',10,'hidden A'),(2,'B',900,'hidden B'),(3,'A',20,'hidden A2'); INSERT INTO reporting.notes VALUES (1,'A','ok'),(1,'B','wrong'),(2,'B','other'),(3,'A','ok2');"
        )
        self.tables = (
            TableSchema(
                TableRef("reporting", "sales"),
                (
                    Column("id", "integer"),
                    Column("store_code", "text"),
                    Column("amount", "numeric"),
                ),
            ),
            TableSchema(
                TableRef("reporting", "notes"),
                (
                    Column("sale_id", "integer"),
                    Column("store_code", "text"),
                    Column("label", "text"),
                ),
            ),
        )
        self.policies = [
            {
                "schema": "reporting",
                "name": "sales",
                "columns": ["id", "store_code", "amount"],
                "store_column": "store_code",
                "shared": False,
            },
            {
                "schema": "reporting",
                "name": "notes",
                "columns": ["sale_id", "store_code", "label"],
                "store_column": "store_code",
                "shared": False,
            },
        ]

    def tearDown(self):
        self.connection.close()

    def execute(self, sql, scope=None):
        normalized, secured, needs_scope = scoped_sql(sql, self.tables, self.policies)
        tree = sqlglot.parse_one(secured, read="postgres")
        count = 0
        for equality in list(tree.find_all(exp.EQ)):
            if isinstance(equality.expression, exp.Any):
                count += 1
                selection = sqlglot.parse_one("SELECT value FROM json_each(?)", read="sqlite")
                equality.replace(exp.In(this=equality.this.copy(), query=selection.subquery()))
        values = [json.dumps(scope or ["A"])] * count
        return self.connection.execute(tree.sql(dialect="sqlite"), values).fetchall(), secured

    def test_or_does_not_escape_row_scope(self):
        rows, sql = self.execute(
            "SELECT t.id,t.amount FROM reporting.sales AS t WHERE t.store_code='B' OR 1=1 ORDER BY t.id"
        )
        self.assertEqual(rows, [(1, 10), (3, 20)])
        self.assertNotIn("hidden", sql)

    def test_join_scopes_each_table_before_aggregation(self):
        rows, _ = self.execute(
            "SELECT SUM(s.amount) AS total FROM reporting.sales AS s LEFT JOIN reporting.notes AS n ON s.id=n.sale_id"
        )
        self.assertEqual(rows, [(30,)])

    def test_wildcard_never_exposes_hidden_column(self):
        rows, sql = self.execute("SELECT * FROM reporting.sales ORDER BY reporting.sales.id")
        self.assertEqual(rows, [(1, "A", 10), (3, "A", 20)])
        self.assertNotIn('"secret"', sql)

    def test_fully_qualified_columns_survive_relation_wrapping(self):
        rows, _ = self.execute(
            "SELECT reporting.sales.amount FROM reporting.sales ORDER BY reporting.sales.id"
        )
        self.assertEqual(rows, [(10,), (20,)])

    def test_scope_values_are_bound_and_not_interpolated(self):
        rows, sql = self.execute("SELECT s.amount FROM reporting.sales AS s", ["A' OR 1=1 --"])
        self.assertEqual(rows, [])
        self.assertNotIn("OR 1=1 --", sql)

    def test_unsupported_or_hidden_sources_are_rejected(self):
        for sql in (
            "SELECT s.secret FROM reporting.sales AS s",
            "SELECT * FROM public.users",
            "SELECT * FROM sales",
            "SELECT * FROM reporting.sales; DELETE FROM reporting.sales",
            "SELECT * FROM reporting.sales WHERE id IN (SELECT id FROM reporting.sales)",
            "SELECT pg_sleep(10) FROM reporting.sales",
            "WITH x AS (SELECT * FROM reporting.sales) SELECT * FROM x",
        ):
            with self.subTest(sql=sql), self.assertRaises(QueryError):
                scoped_sql(sql, self.tables, self.policies)


if __name__ == "__main__":
    unittest.main()
