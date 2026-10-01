import ctypes
import os
from pathlib import Path
import unittest

from src.domain.schema import Column, TableRef, TableSchema
from src.domain.query import GenerationRefusal
from src.sql.grammar import SQLGrammarBuilder
from src.sql.grammar.builder import quote_identifier
from tests.gbnf_support import GrammarRecognizer


TABLES = (
    TableSchema(TableRef("sales", "customers"), (Column("id", "integer"), Column("name", "text"))),
    TableSchema(TableRef("sales", "orders"), (
        Column("id", "integer"), Column("customer_id", "integer"),
        Column("amount", "numeric"), Column("created_at", "timestamp"),
        Column("status", "text"),
    )),
)
CUSTOMERS = '"sales"."customers" AS t0'
ORDERS = '"sales"."orders" AS t1'
_NATIVE_LIBRARIES = sorted((Path(__file__).resolve().parents[1] / ".tools").glob("llama-*/llama.dll")) if os.name == "nt" else []


class GrammarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grammar = SQLGrammarBuilder.build(TABLES)
        cls.recognizer = GrammarRecognizer(cls.grammar)

    def test_basic_analytics(self):
        examples = [
            f'SELECT * FROM {ORDERS}',
            f'SELECT DISTINCT t1."status" FROM {ORDERS};',
            f'SELECT t1.* FROM {ORDERS} LIMIT 100 OFFSET 10',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."amount" >= -12.50',
            f'SELECT t1."id" FROM {ORDERS} WHERE NOT (t1."amount" < 10 OR t1."status" IS NULL) AND t1."id" IN (1, 2, -3)',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."amount" NOT BETWEEN 1 AND 10 OR t1."status" NOT IN (\'paid\', \'failed\')',
            f'SELECT t0."id" FROM {CUSTOMERS} LEFT JOIN {ORDERS} ON t0."id" = t1."customer_id" WHERE t1."id" IS NULL',
            f'SELECT t0."name", SUM(t1."amount") AS "total" FROM {CUSTOMERS} INNER JOIN {ORDERS} ON t0."id" = t1."customer_id" GROUP BY t0."name" HAVING SUM(t1."amount") > 0 ORDER BY "total" DESC, t0."name" ASC NULLS LAST',
            f'SELECT COUNT(DISTINCT t1."customer_id"), COUNT(*), AVG(t1."amount"), MIN(t1."amount"), MAX(t1."amount") FROM {ORDERS}',
            f'SELECT SUM(CASE WHEN t1."status" = \'paid\' THEN t1."amount" ELSE 0 END) / NULLIF(COUNT(*), 0) AS "average" FROM {ORDERS}',
            f'SELECT CASE WHEN SUM(t1."amount") > 0 THEN COALESCE(SUM(t1."amount"), 0) ELSE 0 END FROM {ORDERS}',
            f'SELECT DATE_TRUNC(\'month\', t1."created_at"), COUNT(*) FROM {ORDERS} WHERE t1."created_at" >= DATE \'2025-01-01\' GROUP BY DATE_TRUNC(\'month\', t1."created_at")',
            f'SELECT EXTRACT(YEAR FROM t1."created_at") FROM {ORDERS} WHERE t1."created_at" < CURRENT_DATE - INTERVAL \'7 days\'',
            f'SELECT t0."id" FROM {CUSTOMERS} WHERE t0."name" ILIKE \'O\'\'Brien%;-- literal\'',
            f'SELECT (t1."amount" + 2) * 3 / 4 % 5 FROM {ORDERS}',
            f'\nSELECT\nCOUNT(*)\nFROM\n{ORDERS}\n;\n',
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                self.assertTrue(self.recognizer.accepts(sql), sql)

    def test_forbidden_or_malformed_sql(self):
        examples = [
            f'DELETE FROM {ORDERS}',
            f'SELECT SUM(*) FROM {ORDERS}',
            f'SELECT SUM(COUNT(*)) FROM {ORDERS}',
            f'SELECT COUNT(DISTINCT *) FROM {ORDERS}',
            f'SELECT t1."id" FROM {ORDERS} WHERE COUNT(*) > 0',
            f'SELECT t1."id" FROM {ORDERS} GROUP BY SUM(t1."amount")',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."id" IN ()',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."id" IN (SELECT t1."id" FROM {ORDERS})',
            f'SELECT t1."id" FROM {ORDERS} WHERE t1."amount" BETWEEN 1',
            f'SELECT t1."id" FROM {ORDERS} LIMIT -1',
            f'SELECT t1."id" FROM {ORDERS} LIMIT 1.5',
            f'SELECT t1."missing" FROM {ORDERS}',
            'SELECT * FROM "pg_catalog"."pg_class" AS t1',
            f'SELECT * FROM {ORDERS}; SELECT * FROM {ORDERS}',
            f'SELECT * FROM {ORDERS} CROSS JOIN {CUSTOMERS}',
            f'SELECT pg_sleep(1) FROM {ORDERS}',
            f'SELECT --1 FROM {ORDERS}',
            f'SELECT t1."amount" FROM {ORDERS} WHERE t1."amount" = 1 AND',
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                self.assertFalse(self.recognizer.accepts(sql), sql)

    def test_reordering_metadata_does_not_change_aliases_or_grammar(self):
        self.assertEqual(SQLGrammarBuilder.build(tuple(reversed(TABLES))), self.grammar)
        self.assertEqual(SQLGrammarBuilder.aliases(TABLES)[TABLES[0].ref], "t0")

    def test_identifier_quoting_and_gbnf_escaping_are_independent(self):
        table = TableSchema(TableRef('A"B', "таблица-\\bar\\foo\n\b\f"), (Column('сумма"; DROP TABLE x; --', "text"),))
        grammar = GrammarRecognizer(SQLGrammarBuilder.build((table,)))
        sql = f'SELECT t0.{quote_identifier(table.columns[0].name)} FROM {quote_identifier(table.ref.schema)}.{quote_identifier(table.ref.name)} AS t0'
        self.assertTrue(grammar.accepts(sql))

    def test_similar_sanitized_names_do_not_collide(self):
        tables = (
            TableSchema(TableRef("a-b", "c"), (Column("x", "int"),)),
            TableSchema(TableRef("ab", "c"), (Column("y", "int"),)),
        )
        grammar = GrammarRecognizer(SQLGrammarBuilder.build(tables))
        self.assertTrue(grammar.accepts('SELECT t0."x" FROM "a-b"."c" AS t0'))
        self.assertTrue(grammar.accepts('SELECT t1."y" FROM "ab"."c" AS t1'))

    def test_invalid_catalog_is_rejected(self):
        for tables in ((), TABLES + (TABLES[0],), (TableSchema(TableRef("x", "y"), ()),)):
            with self.subTest(tables=tables), self.assertRaises(ValueError):
                SQLGrammarBuilder.build(tables)

    def test_generation_can_refuse_without_expanding_the_sql_dialect(self):
        recognizer = GrammarRecognizer(SQLGrammarBuilder.build(TABLES, allow_refusal=True))
        for refusal in GenerationRefusal:
            self.assertTrue(recognizer.accepts(refusal.value))
            self.assertFalse(self.recognizer.accepts(refusal.value))
            self.assertFalse(recognizer.accepts(refusal.value + '; SELECT * FROM ' + ORDERS))
            self.assertFalse(recognizer.accepts(refusal.value + ': explanation'))
        self.assertTrue(recognizer.accepts(f'SELECT COUNT(*) FROM {ORDERS}'))
        self.assertFalse(recognizer.accepts(f'DELETE FROM {ORDERS}'))

    @unittest.skipUnless(_NATIVE_LIBRARIES, "No local standalone llama.cpp library; native compiler test skipped")
    def test_native_gbnf_compilation_without_loading_a_model(self):


        library_path = _NATIVE_LIBRARIES[0]
        with os.add_dll_directory(str(library_path.parent)):
            library = ctypes.CDLL(str(library_path))
            compile_grammar = library.llama_sampler_init_grammar
            compile_grammar.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
            compile_grammar.restype = ctypes.c_void_p
            free_grammar = library.llama_sampler_free
            free_grammar.argtypes = [ctypes.c_void_p]
            free_grammar.restype = None
            unusual = TableSchema(
                TableRef('схема"', "table\\bar\b\f"),
                (Column("odd\\field\n", "text"),),
            )
            for grammar in (self.grammar, SQLGrammarBuilder.build((unusual,)),
                            SQLGrammarBuilder.build(TABLES, allow_refusal=True)):
                sampler = compile_grammar(None, grammar.encode("utf-8"), b"root")
                self.assertTrue(sampler, "llama.cpp rejected the generated GBNF")
                if sampler:
                    free_grammar(sampler)


if __name__ == "__main__":
    unittest.main()
