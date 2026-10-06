import unittest

from sql_agent.query import GenerationRefusal
from sql_agent.schema import Column, TableRef, TableSchema
from sql_agent.sql.grammar import SQLGrammarBuilder
from sql_agent.sql.grammar.builder import quote_identifier
from sql_agent.sql.validation import SQLValidator
from .gbnf_support import GrammarRecognizer


TABLES = (
    TableSchema(TableRef("sales", "customers"), (Column("id", "integer"), Column("name", "text"))),
    TableSchema(
        TableRef("sales", "orders"),
        tuple(
            Column(name, kind)
            for name, kind in (
                ("id", "integer"),
                ("customer_id", "integer"),
                ("amount", "numeric"),
                ("created_at", "timestamp"),
                ("status", "text"),
            )
        ),
    ),
)
CUSTOMERS = '"sales"."customers" AS t0'
ORDERS = '"sales"."orders" AS t1'
ANALYTICS = (
    f"SELECT * FROM {ORDERS}",
    f'SELECT DISTINCT t1."status" FROM {ORDERS};',
    f"SELECT t1.* FROM {ORDERS} LIMIT 100 OFFSET 10",
    f'SELECT t1."id" FROM {ORDERS} WHERE NOT (t1."amount" < -12.5 OR t1."status" IS NULL) AND t1."id" IN (1, 2, -3)',
    f'SELECT t1."id" FROM {ORDERS} WHERE t1."amount" NOT BETWEEN 1 AND 10 OR t1."status" NOT IN (\'paid\', \'failed\')',
    f'SELECT t0."id" FROM {CUSTOMERS} LEFT JOIN {ORDERS} ON t0."id" = t1."customer_id" WHERE t1."id" IS NULL',
    f'SELECT t0."name", SUM(t1."amount") AS "total" FROM {CUSTOMERS} INNER JOIN {ORDERS} ON t0."id" = t1."customer_id" GROUP BY t0."name" HAVING SUM(t1."amount") > 0 ORDER BY "total" DESC, t0."name" ASC NULLS LAST',
    f'SELECT COUNT(DISTINCT t1."customer_id"), COUNT(*), AVG(t1."amount"), MIN(t1."amount"), MAX(t1."amount") FROM {ORDERS}',
    f'SELECT SUM(CASE WHEN t1."status" = \'paid\' THEN t1."amount" ELSE 0 END) / NULLIF(COUNT(*), 0) FROM {ORDERS}',
    f'SELECT CASE WHEN SUM(t1."amount") > 0 THEN COALESCE(SUM(t1."amount"), 0) ELSE 0 END FROM {ORDERS}',
    f"SELECT DATE_TRUNC('month', t1.\"created_at\"), COUNT(*) FROM {ORDERS} WHERE t1.\"created_at\" >= DATE '2025-01-01' GROUP BY DATE_TRUNC('month', t1.\"created_at\")",
    f'SELECT EXTRACT(YEAR FROM t1."created_at") FROM {ORDERS} WHERE t1."created_at" < CURRENT_DATE - INTERVAL \'7 days\'',
    f"SELECT t0.\"id\" FROM {CUSTOMERS} WHERE t0.\"name\" ILIKE 'O''Brien%;-- literal'",
    f'SELECT (t1."amount" + 2) * 3 / 4 % 5 FROM {ORDERS}',
    f'SELECT t1."status", COUNT(*) FROM {ORDERS} GROUP BY 1 ORDER BY 2 DESC',
)


class GrammarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grammar = SQLGrammarBuilder.build(TABLES)
        cls.recognizer = GrammarRecognizer(cls.grammar)

    def test_analytics_survive_grammar_validation_and_normalization(self):
        validator = SQLValidator()
        for sql in ANALYTICS:
            with self.subTest(sql=sql):
                self.assertTrue(self.recognizer.accepts(sql))
                normalized = validator.validate(sql, TABLES)
                self.assertEqual(validator.validate(normalized, TABLES), normalized)

    def test_grammar_excludes_unsafe_and_malformed_queries(self):
        examples = (
            f"DELETE FROM {ORDERS}",
            f"SELECT * FROM {ORDERS}; SELECT * FROM {ORDERS}",
            f"SELECT * FROM {ORDERS} CROSS JOIN {CUSTOMERS}",
            f"SELECT pg_sleep(1) FROM {ORDERS}",
            f"SELECT SUM(COUNT(*)) FROM {ORDERS}",
            f"SELECT SUM(*) FROM {ORDERS}",
            f"SELECT COUNT(DISTINCT *) FROM {ORDERS}",
            f'SELECT t1."id" FROM {ORDERS} WHERE COUNT(*) > 0',
            f'SELECT t1."id" FROM {ORDERS} GROUP BY SUM(t1."amount")',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."id" IN ()',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."id" IN (SELECT * FROM {ORDERS})',
            f'SELECT t1."id" FROM {ORDERS} LIMIT -1',
            f'SELECT t1."missing" FROM {ORDERS}',
            'SELECT * FROM "pg_catalog"."pg_class" AS t1',
            f"SELECT --1 FROM {ORDERS}",
        )
        for sql in examples:
            with self.subTest(sql=sql):
                self.assertFalse(self.recognizer.accepts(sql))

    def test_catalog_aliases_and_identifier_escaping(self):
        self.assertEqual(SQLGrammarBuilder.build(tuple(reversed(TABLES))), self.grammar)
        tables = (
            TableSchema(TableRef('a"b', "таблица-\\bar\n\b\f"), (Column('x"; --', "text"),)),
            TableSchema(TableRef("ab", "table"), (Column("y", "text"),)),
        )
        recognizer = GrammarRecognizer(SQLGrammarBuilder.build(tables))
        for table, alias in SQLGrammarBuilder.aliases(tables).items():
            column = next(item.columns[0].name for item in tables if item.ref == table)
            sql = f"SELECT {alias}.{quote_identifier(column)} FROM {quote_identifier(table.schema)}.{quote_identifier(table.name)} AS {alias}"
            self.assertTrue(recognizer.accepts(sql))
        for invalid in ((), TABLES + (TABLES[0],), (TableSchema(TableRef("x", "y"), ()),)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SQLGrammarBuilder.build(invalid)

    def test_refusal_is_a_separate_optional_protocol(self):
        recognizer = GrammarRecognizer(SQLGrammarBuilder.build(TABLES, allow_refusal=True))
        for refusal in GenerationRefusal:
            self.assertTrue(recognizer.accepts(refusal.value))
            self.assertFalse(self.recognizer.accepts(refusal.value))
            self.assertFalse(recognizer.accepts(refusal.value + "; SELECT * FROM " + ORDERS))
