import unittest

from sql_agent.query import QueryError
from sql_agent.schema import Column, TableRef, TableSchema
from sql_agent.sql.validation import SQLValidator
from .test_sql_grammar import TABLES


class SQLValidationTests(unittest.TestCase):
    def test_ast_rejects_unsafe_sources_expressions_and_scopes(self):
        queries = (
            "DELETE FROM sales.orders RETURNING *",
            "SELECT * INTO TEMP x FROM sales.orders",
            "SELECT * FROM sales.orders; DELETE FROM sales.orders",
            "WITH x AS (SELECT * FROM sales.orders) SELECT * FROM x",
            "SELECT * FROM sales.orders UNION SELECT * FROM sales.orders",
            "SELECT * FROM (SELECT * FROM sales.orders) AS x",
            "SELECT (SELECT 1) FROM sales.orders",
            "SELECT * FROM sales.orders FOR UPDATE",
            "SELECT * FROM orders",
            "SELECT * FROM other.sales.orders",
            "SELECT id FROM sales.orders o JOIN sales.customers c ON o.customer_id=c.id",
            "SELECT * FROM sales.orders o JOIN sales.orders x ON o.id=x.id",
            "SELECT set_config('search_path', 'evil', false) FROM sales.orders",
            "SELECT pg_catalog.pg_sleep(1) FROM sales.orders",
            "SELECT id::text FROM sales.orders",
            "SELECT SUM(amount) OVER () FROM sales.orders",
            "SELECT SUM(COUNT(*)) FROM sales.orders",
            "SELECT id, SUM(amount) FROM sales.orders",
            "SELECT id FROM sales.orders WHERE SUM(amount)>0",
            "SELECT COUNT(*) FROM sales.orders HAVING id>0",
            "SELECT amount AS x FROM sales.orders WHERE x>0",
            "SELECT id FROM sales.orders LIMIT 2147483648",
        )
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(QueryError):
                SQLValidator().validate(sql, TABLES)

    def test_projection_is_limited_to_authorized_columns_and_names(self):
        visible = (TableSchema(TableRef("sales", "customers"), (Column("id", "integer"),)),)
        self.assertEqual(
            SQLValidator().validate("SELECT * FROM sales.customers", visible),
            'SELECT "customers"."id" FROM "sales"."customers"',
        )
        for sql in ("SELECT customers.name FROM sales.customers", "SELECT * FROM private.accounts"):
            with self.subTest(sql=sql), self.assertRaises(QueryError) as caught:
                SQLValidator().validate(sql, visible)
            self.assertEqual(caught.exception.code, "missing_context")
        system = (TableSchema(TableRef("pg_catalog", "pg_class"), (Column("relname", "text"),)),)
        with self.assertRaises(QueryError):
            SQLValidator().validate("SELECT * FROM pg_catalog.pg_class", system)
