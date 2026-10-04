import importlib.util
import unittest

from sql_agent.query import QueryError
from sql_agent.schema import Column, TableRef, TableSchema
from sql_agent.sql.grammar import SQLGrammarBuilder
from sql_agent.sql.validation import SQLValidator
from .gbnf_support import GrammarRecognizer
from .test_sql_grammar import TABLES, CUSTOMERS, ORDERS


@unittest.skipUnless(
    importlib.util.find_spec("sqlglot"),
    "sqlglot is not installed; AST checks require that dependency",
)
class SQLValidationTests(unittest.TestCase):
    def setUp(self):
        self.validator = SQLValidator()

    def reject(self, sql, tables=TABLES, *, code="invalid_sql"):
        with self.assertRaises(QueryError, msg=sql) as error:
            self.validator.validate(sql, tables)
        self.assertEqual(error.exception.code, code)
        self.assertTrue(error.exception.retryable)

    def test_analytics_and_aggregates(self):
        examples = [
            f"SELECT * FROM {ORDERS}",
            f'SELECT DISTINCT t1."status" FROM {ORDERS}',
            f"SELECT t1.* FROM {ORDERS} LIMIT 0 OFFSET 0",
            f'SELECT COUNT(*), COUNT(DISTINCT t1."customer_id"), SUM(t1."amount"), AVG(t1."amount"), MIN(t1."amount"), MAX(t1."amount") FROM {ORDERS}',
            f'SELECT t0."name", SUM(t1."amount") AS "total" FROM {CUSTOMERS} LEFT JOIN {ORDERS} ON t0."id" = t1."customer_id" GROUP BY t0."name" HAVING SUM(t1."amount") > 0 ORDER BY "total" DESC, t0."name" ASC',
            f'SELECT SUM(CASE WHEN t1."status" = \'paid\' THEN t1."amount" ELSE 0 END) / NULLIF(COUNT(*), 0) FROM {ORDERS}',
            f'SELECT CASE WHEN COUNT(*) > 0 THEN COALESCE(SUM(t1."amount"), 0) ELSE 0 END FROM {ORDERS}',
            f'SELECT t1."id" FROM {ORDERS} WHERE NOT (t1."amount" <= -1.5 OR t1."status" IS NULL) AND t1."id" IN (1, 2, -3)',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."amount" BETWEEN 1 AND 10 AND t1."status" NOT ILIKE \'failed%\'',
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                normalized = self.validator.validate(sql, TABLES)
                self.assertTrue(normalized.startswith("SELECT "))
                self.assertEqual(self.validator.validate(normalized, TABLES), normalized)

    def test_dates_and_intervals(self):
        examples = [
            f"SELECT DATE_TRUNC('month', t1.\"created_at\"), COUNT(*) FROM {ORDERS} GROUP BY DATE_TRUNC('month', t1.\"created_at\")",
            f'SELECT EXTRACT(YEAR FROM t1."created_at") FROM {ORDERS} WHERE t1."created_at" >= DATE \'2025-01-01\'',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."created_at" < CURRENT_DATE - INTERVAL \'7 days\'',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."created_at" >= TIMESTAMP \'2025-01-01 12:30:00\'',
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                self.assertTrue(self.validator.validate(sql, TABLES))

    def test_projection_stars_expand_only_authorized_columns(self):
        visible = TableSchema(TableRef("sales", "customers"), (Column("id", "integer"),))
        for sql in (
            'SELECT * FROM "sales"."customers" AS t0',
            'SELECT t0.* FROM "sales"."customers" AS t0',
        ):
            normalized = self.validator.validate(sql, (visible,))
            self.assertEqual(normalized, 'SELECT "t0"."id" FROM "sales"."customers" AS "t0"')
        self.assertIn(
            "COUNT(*)",
            self.validator.validate('SELECT COUNT(*) FROM "sales"."customers" AS t0', (visible,)),
        )

    def test_grammar_and_ast_accept_the_same_supported_analytics(self):
        recognizer = GrammarRecognizer(SQLGrammarBuilder.build(TABLES))
        examples = [
            f"SELECT t1.* FROM {ORDERS} LIMIT 20 OFFSET 5",
            f'SELECT t1."status", COUNT(*), COUNT(DISTINCT t1."customer_id") FROM {ORDERS} GROUP BY t1."status" HAVING COUNT(*) > 1 ORDER BY t1."status" DESC',
            f'SELECT t0."name", SUM(t1."amount") AS "total" FROM {CUSTOMERS} INNER JOIN {ORDERS} ON t0."id" = t1."customer_id" GROUP BY t0."name" ORDER BY "total" DESC, t0."name" ASC',
            f'SELECT t0."id" FROM {CUSTOMERS} LEFT JOIN {ORDERS} ON t0."id" = t1."customer_id" WHERE t1."id" IS NULL',
            f'SELECT SUM(CASE WHEN t1."status" = \'paid\' THEN t1."amount" ELSE 0 END) / NULLIF(COUNT(*), 0) FROM {ORDERS}',
            f"SELECT DATE_TRUNC('month', t1.\"created_at\"), COUNT(*) FROM {ORDERS} GROUP BY DATE_TRUNC('month', t1.\"created_at\")",
            f'SELECT EXTRACT(YEAR FROM t1."created_at") FROM {ORDERS} WHERE t1."created_at" >= CURRENT_DATE - INTERVAL \'7 days\'',
            f"SELECT t1.\"id\" FROM {ORDERS} WHERE t1.\"created_at\" BETWEEN DATE '2025-01-01' AND TIMESTAMP '2025-01-31 23:59:59'",
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."amount" >= -12.5 AND t1."id" IN (1, 2, -3)',
            f"SELECT t0.\"id\" FROM {CUSTOMERS} WHERE t0.\"name\" = 'O''Brien; -- quoted value'",
            f'SELECT t1."status", COUNT(*) FROM {ORDERS} GROUP BY 1 ORDER BY 2 DESC',
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                self.assertTrue(
                    recognizer.accepts(sql), "Fixture must belong to the generated GBNF language"
                )
                normalized = self.validator.validate(sql, TABLES)

                self.assertEqual(self.validator.validate(normalized, TABLES), normalized)

    def test_semicolons_and_quotes_inside_literal_survive(self):
        sql = f"SELECT t0.\"id\" FROM {CUSTOMERS} WHERE t0.\"name\" = 'O''Brien; -- DROP TABLE sales.orders'"
        normalized = self.validator.validate(sql + ";", TABLES)
        self.assertIn("O''Brien; -- DROP TABLE sales.orders", normalized)

    def test_postgres_case_folding_and_unqualified_column_binding(self):
        normalized = self.validator.validate("SELECT ID FROM SALES.CUSTOMERS", TABLES)
        self.assertEqual(normalized, 'SELECT "customers"."id" FROM "sales"."customers"')
        self.reject('SELECT "ID" FROM sales.customers')

    def test_only_one_select(self):
        for sql in (
            "",
            " ",
            ";",
            "DROP TABLE sales.orders",
            "DELETE FROM sales.orders RETURNING *",
            "EXPLAIN SELECT * FROM sales.orders",
            "SELECT * INTO TEMP x FROM sales.orders",
            "SELECT * FROM sales.orders; DELETE FROM sales.orders",
            "SELECT * FROM sales.orders;;",
            "WITH x AS (SELECT * FROM sales.orders) SELECT * FROM x",
            "SELECT * FROM sales.orders UNION SELECT * FROM sales.orders",
            "SELECT * FROM (SELECT * FROM sales.orders) AS x",
            "SELECT (SELECT 1) FROM sales.orders",
            "SELECT * FROM sales.orders FOR UPDATE",
        ):
            with self.subTest(sql=sql):
                self.reject(sql)

    def test_unknown_or_unscoped_names(self):
        for sql in (
            "SELECT * FROM orders",
            "SELECT * FROM other.sales.orders",
            f'SELECT t0."id" FROM {ORDERS}',
            f'SELECT id FROM {CUSTOMERS} JOIN {ORDERS} ON t0."id" = t1."customer_id"',
            f'SELECT * FROM {CUSTOMERS} JOIN {ORDERS} ON future.id = t1."customer_id"',
            f'SELECT * FROM {CUSTOMERS} JOIN "sales"."orders" AS t0 ON t0."id" = t0."customer_id"',
            f'SELECT * FROM {ORDERS} JOIN "sales"."orders" AS x ON x."id" = t1."id"',
            "SELECT * FROM sales.orders AS o (x, y)",
        ):
            with self.subTest(sql=sql):
                self.reject(sql)
        self.reject("SELECT * FROM unknown.orders", code="missing_context")
        self.reject(f'SELECT t1."missing" FROM {ORDERS}', code="missing_context")

    def test_join_cannot_reference_later_source(self):
        extra = TableSchema(TableRef("sales", "items"), (Column("order_id", "integer"),))
        self.reject(
            f'SELECT * FROM {CUSTOMERS} JOIN {ORDERS} ON z.order_id = t1."id" JOIN sales.items AS z ON z.order_id = t1."id"',
            TABLES + (extra,),
        )

    def test_system_schema_rejected_even_if_catalog_contains_it(self):
        system = TableSchema(TableRef("pg_catalog", "pg_class"), (Column("relname", "text"),))
        self.reject("SELECT * FROM pg_catalog.pg_class", (system,))

    def test_unknown_functions_casts_and_operators_rejected(self):
        for expression in (
            "pg_sleep(1)",
            "set_config('search_path', 'evil', false)",
            "evil.sum(t1.amount)",
            "pg_catalog.pg_sleep(1)",
            "t1.id::text",
            "'sales.orders'::regclass",
            "SUM(t1.amount) OVER ()",
            "COUNT(*) FILTER (WHERE t1.amount > 0)",
            "generate_series(1, 1000000)",
            "DATE_TRUNC('bogus', t1.created_at)",
            "EXTRACT(TIMEZONE FROM t1.created_at)",
            "CURRENT_DATE('UTC')",
            "DATE_TRUNC('day', t1.created_at, 'UTC')",
            "t1.id OPERATOR(evil.+) 1",
        ):
            with self.subTest(expression=expression):
                self.reject(f"SELECT {expression} FROM {ORDERS}")

    def test_aggregate_scope_and_stars(self):
        for sql in (
            f"SELECT SUM(*) FROM {ORDERS}",
            f"SELECT COUNT(DISTINCT *) FROM {ORDERS}",
            f"SELECT SUM(COUNT(*)) FROM {ORDERS}",
            f"SELECT COUNT(t1.id, t1.amount) FROM {ORDERS}",
            f"SELECT t1.id, SUM(t1.amount) FROM {ORDERS}",
            f"SELECT *, COUNT(*) FROM {ORDERS}",
            f"SELECT t1.id FROM {ORDERS} WHERE SUM(t1.amount) > 0",
            f"SELECT COUNT(*) FROM {ORDERS} GROUP BY SUM(t1.amount)",
            f"SELECT COUNT(*) FROM {ORDERS} HAVING t1.id > 0",
            f"SELECT COUNT(*) FROM {ORDERS} ORDER BY t1.id",
            f"SELECT * FROM {CUSTOMERS} JOIN {ORDERS} ON SUM(t1.amount) > 0",
            f"SELECT SUM(DISTINCT t1.amount) FROM {ORDERS}",
        ):
            with self.subTest(sql=sql):
                self.reject(sql)

    def test_grouped_expression_and_positions(self):
        examples = [
            f"SELECT t1.amount + 1, COUNT(*) FROM {ORDERS} GROUP BY t1.amount",
            f"SELECT t1.amount + 1, COUNT(*) FROM {ORDERS} GROUP BY t1.amount + 1",
            f"SELECT t1.status, COUNT(*) FROM {ORDERS} GROUP BY 1 ORDER BY 2 DESC",
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                self.assertTrue(self.validator.validate(sql, TABLES))
        self.reject(f"SELECT t1.amount, COUNT(*) FROM {ORDERS} GROUP BY t1.amount + 1")
        self.reject(f"SELECT COUNT(*) FROM {ORDERS} GROUP BY 1")
        self.reject(f"SELECT * FROM {ORDERS} ORDER BY 1")
        self.reject(f"SELECT *, t1.amount FROM {ORDERS} ORDER BY 2")
        self.reject(f"SELECT t1.*, t1.amount FROM {ORDERS} ORDER BY 2")
        self.reject(f"SELECT t1.id FROM {ORDERS} ORDER BY 2")

    def test_order_position_can_refer_to_count_star_without_expanding_projection(self):
        sql = f"SELECT t1.status, COUNT(*) FROM {ORDERS} GROUP BY t1.status ORDER BY 2 DESC"
        normalized = self.validator.validate(sql, TABLES)
        self.assertIn("ORDER BY COUNT(*) DESC", normalized)
        self.assertEqual(self.validator.validate(normalized, TABLES), normalized)

    def test_output_alias_scope(self):
        self.assertTrue(
            self.validator.validate(
                f'SELECT SUM(t1.amount) AS "total" FROM {ORDERS} ORDER BY "total" DESC', TABLES
            )
        )
        self.reject(f'SELECT SUM(t1.amount) AS "total" FROM {ORDERS} ORDER BY "total" + 1')
        self.reject(f'SELECT t1.amount AS "a", t1.id AS "a" FROM {ORDERS}')
        self.reject(f'SELECT t1.amount AS "x" FROM {ORDERS} WHERE x > 0')

    def test_limits_and_literal_lists(self):
        for suffix in (
            "LIMIT -1",
            "LIMIT 1.2",
            "LIMIT 2147483648",
            "LIMIT ALL",
            "OFFSET -1",
            "WHERE t1.id IN ()",
            "WHERE t1.id IN (t1.customer_id)",
        ):
            with self.subTest(suffix=suffix):
                self.reject(f"SELECT t1.id FROM {ORDERS} {suffix}")

    def test_size_and_depth_limits(self):
        with self.assertRaises(QueryError):
            SQLValidator(max_sql_chars=10).validate(f"SELECT * FROM {ORDERS}", TABLES)
        with self.assertRaises(QueryError):
            SQLValidator(max_ast_depth=3).validate(f"SELECT (((t1.id + 1))) FROM {ORDERS}", TABLES)


if __name__ == "__main__":
    unittest.main()
