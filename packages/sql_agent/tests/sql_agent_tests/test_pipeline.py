import asyncio
from dataclasses import replace
import json
import sqlite3
import unittest

from sql_agent import SQLAgent
from sql_agent.contracts import (
    AgentRequest,
    AgentSettings,
    CatalogSnapshot,
    Column,
    ConversationContext,
    ForeignKey,
    MetricDefinition,
    QueryContext,
    QueryError,
    QueryResult,
    RetrievedTable,
    TableRef,
    TableSchema,
)


STORES = TableSchema(
    TableRef("sales", "stores"), (Column("id", "integer", False), Column("city", "text")), ("id",)
)
ORDERS = TableSchema(
    TableRef("sales", "orders"),
    (Column("id", "integer", False), Column("store_id", "integer"), Column("amount", "numeric")),
    ("id",),
    (ForeignKey(("store_id",), STORES.ref, ("id",)),),
)
SQL = 'SELECT t1."city", SUM(t0."amount") AS "total" FROM "sales"."orders" AS t0 INNER JOIN "sales"."stores" AS t1 ON t0."store_id" = t1."id" GROUP BY t1."city" ORDER BY "total" DESC'


class Catalog:
    def __init__(self, tables=(ORDERS, STORES)):
        self.value = CatalogSnapshot("tenant/source/profile", "1", tables)

    async def snapshot(self, context):
        context.check_cancelled()
        return self.value


class Generator:
    context_size = 8192
    max_tokens = 512

    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def count_tokens(self, messages):
        return sum(len(message["content"]) // 4 for message in messages)

    async def generate(self, messages, grammar, context):
        context.check_cancelled()
        self.calls.append(messages)
        return next(self.answers)


class Executor:
    def __init__(self):
        self.calls = []
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript("""
            ATTACH DATABASE ':memory:' AS sales;
            CREATE TABLE sales.stores(id INTEGER PRIMARY KEY, city TEXT);
            CREATE TABLE sales.orders(id INTEGER PRIMARY KEY, store_id INTEGER, amount NUMERIC);
            INSERT INTO sales.stores VALUES (1, 'Moscow'), (2, 'Kazan');
            INSERT INTO sales.orders VALUES (1, 1, 100), (2, 1, 50), (3, 2, 80);
            PRAGMA query_only=ON;
        """)

    async def execute(self, sql, context):
        context.check_cancelled()
        self.calls.append(sql)
        cursor = self.connection.execute(sql)
        return QueryResult(
            sql, [item[0] for item in cursor.description], [list(row) for row in cursor.fetchall()]
        )


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.executor = Executor()
        self.addCleanup(self.executor.connection.close)

    def agent(self, answers, **kwargs):
        generator = Generator(answers)
        return SQLAgent(self.catalog, generator, self.executor, **kwargs), generator

    async def test_joins_return_expected_business_table_and_trace(self):
        events = []
        agent, _ = self.agent([SQL])
        result = await agent.run(
            AgentRequest("Выручка по городам"), QueryContext(on_event=events.append)
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.result.rows, [["Moscow", 150], ["Kazan", 80]])
        self.assertEqual(result.catalog_version, "1")
        self.assertEqual(set(result.tables), {"sales.orders", "sales.stores"})
        self.assertEqual([item.content["stage"] for item in events][-2:], ["validate", "execute"])

    async def test_clarification_and_refusal_never_reach_executor(self):
        for answer, status in (
            ("CLARIFY_METRIC", "clarification"),
            ("CLARIFY_PERIOD", "clarification"),
            ("UNSUPPORTED_QUERY", "not_found"),
            ("INSUFFICIENT_CONTEXT", "not_found"),
        ):
            with self.subTest(answer=answer):
                agent, _ = self.agent([answer])
                result = await agent.run(AgentRequest("Сравни прибыль"))
                self.assertEqual(result.status, status)
                self.assertEqual(result.attempts, 1)
                self.assertIsNone(result.sql)
        self.assertEqual(self.executor.calls, [])

    async def test_invalid_sql_is_corrected_before_read_only_execution(self):
        agent, generator = self.agent(['DELETE FROM "sales"."orders"', SQL])
        result = await agent.run(AgentRequest("Выручка по городам"))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(generator.calls[1][2]["role"], "assistant")

    async def test_repeated_failed_sql_stops_with_attempt_count(self):
        agent, _ = self.agent(["DELETE FROM sales.orders"] * 4)
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.error_code, "repeated_sql")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(self.executor.calls, [])

    async def test_correction_budget_is_bounded(self):
        agent, _ = self.agent(
            [f"DELETE FROM sales.orders WHERE id={index}" for index in range(10)],
            settings=AgentSettings(max_corrections=2),
        )
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.status, "error")

    async def test_conversation_base_is_preserved_separately_from_failed_attempt(self):
        agent, generator = self.agent(["DELETE FROM sales.orders", SQL])
        base = ConversationContext("Выручка по городам", SQL, {"period": "2026-09"})
        metric = MetricDefinition(
            "revenue",
            "Выручка",
            "Сумма orders.amount",
            "RUB",
            tables=(ORDERS.ref,),
            calculation={"aggregation": "sum", "value_column": "amount"},
        )
        result = await agent.run(AgentRequest("А теперь по убыванию", base, (metric,)))
        self.assertEqual(result.status, "success")
        payload = json.loads(generator.calls[1][1]["content"])
        self.assertEqual(payload["continuation_base"]["sql"], SQL)
        self.assertEqual(payload["metric_definitions"][0]["key"], "revenue")
        self.assertEqual(payload["metric_definitions"][0]["calculation"], metric.calculation)
        self.assertEqual(generator.calls[1][2]["content"], "DELETE FROM sales.orders")

    async def test_first_question_filters_are_structured_and_survive_correction(self):
        agent, generator = self.agent(["DELETE FROM sales.orders", SQL])
        filters = {"date_from": "2026-09-01", "date_to": "2026-10-01", "metric": "revenue"}
        result = await agent.run(AgentRequest("Выручка по городам", filters=filters))
        self.assertEqual(result.status, "success")
        for messages in generator.calls:
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["question"], "Выручка по городам")
            self.assertEqual(payload["current_filters"], filters)
            self.assertIn("conflicts with the selected reporting period", messages[0]["content"])

    async def test_unquoted_uppercase_base_identifiers_use_postgres_case_folding(self):
        agent, _ = self.agent([SQL])
        result = await agent.run(
            AgentRequest(
                "А теперь по городам",
                ConversationContext("Выручка", "SELECT SUM(AMOUNT) FROM SALES.ORDERS"),
            )
        )
        self.assertEqual(result.status, "success")

    async def test_out_of_scope_retrieval_is_rejected_before_reranker(self):
        class ForeignRetriever:
            async def retrieve(self, question, snapshot, context):
                from sql_agent.contracts import RetrievedTable

                return [
                    RetrievedTable(
                        TableSchema(TableRef("private", "credentials"), (Column("secret", "text"),))
                    )
                ]

        class Reranker:
            async def rerank(self, *args):
                raise AssertionError("Foreign metadata reached reranking")

        agent, _ = self.agent([SQL], retriever=ForeignRetriever(), reranker=Reranker())
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.error_code, "index_outdated")

    async def test_invalid_filters_do_not_reach_generation(self):
        for filters in ([1], {"value": float("nan")}, {"value": object()}):
            agent, generator = self.agent([SQL])
            result = await agent.run(AgentRequest("Выручка", filters=filters))
            self.assertEqual(result.error_code, "invalid_context")
            self.assertEqual(generator.calls, [])

    async def test_rights_change_during_generation_blocks_execution(self):
        agent, generator = self.agent([SQL])

        async def changed(messages, grammar, context):
            self.catalog.value = replace(self.catalog.value, namespace="tenant/revoked", tables=())
            return SQL

        generator.generate = changed
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.error_code, "catalog_changed")
        self.assertEqual(self.executor.calls, [])

    async def test_unavailable_base_or_metric_fails_without_generation(self):
        for request in (
            AgentRequest("Продолжи", ConversationContext("Все", "SELECT * FROM secret.accounts")),
            AgentRequest(
                "Выручка",
                metrics=(
                    MetricDefinition("x", "X", "hidden", tables=(TableRef("secret", "accounts"),)),
                ),
            ),
        ):
            agent, generator = self.agent([SQL])
            result = await agent.run(request)
            self.assertEqual(result.status, "error")
            self.assertEqual(generator.calls, [])

    async def test_total_deadline_covers_generator_and_returns_timeout(self):
        agent, generator = self.agent([], settings=AgentSettings(timeout_seconds=0.01))

        async def slow(*args):
            await asyncio.sleep(1)

        generator.generate = slow
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.error_code, "query_timeout")
        self.assertEqual(self.executor.calls, [])

    async def test_cancellation_prevents_catalog_and_generation_work(self):
        agent, generator = self.agent([SQL])
        context = QueryContext()
        context.cancelled.set()
        result = await agent.run(AgentRequest("Выручка"), context)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(generator.calls, [])

    async def test_executor_failure_is_corrected_inside_pipeline(self):
        calls = 0
        original = self.executor.execute

        async def flaky(sql, context):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise QueryError("invalid_sql", "Transient invalid column", retryable=True)
            return await original(sql, context)

        self.executor.execute = flaky
        agent, _ = self.agent([SQL, SQL + " LIMIT 10"])
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.attempts, 2)

    async def test_missing_context_refreshes_once_and_allows_rechecking_same_sql(self):
        class Retriever:
            calls = 0

            async def retrieve(self, question, snapshot, context):
                self.calls += 1
                return (
                    [RetrievedTable(ORDERS, 1)]
                    if self.calls == 1
                    else [RetrievedTable(ORDERS, 1), RetrievedTable(STORES, 1)]
                )

        retriever = Retriever()
        agent, generator = self.agent([SQL, SQL], retriever=retriever)
        result = await agent.run(AgentRequest("Выручка по городам"))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(retriever.calls, 2)
        self.assertTrue(json.loads(generator.calls[1][-1]["content"])["context_changed"])

    async def test_unchanged_refresh_does_not_allow_repeating_failed_sql(self):
        class Retriever:
            calls = 0

            async def retrieve(self, question, snapshot, context):
                self.calls += 1
                return [RetrievedTable(ORDERS, 1)]

        retriever = Retriever()
        agent, _ = self.agent([SQL, SQL], retriever=retriever)
        result = await agent.run(AgentRequest("Выручка по городам"))
        self.assertEqual(result.error_code, "repeated_sql")
        self.assertEqual(retriever.calls, 2)
        self.assertEqual(self.executor.calls, [])

    async def test_unrecoverable_database_error_does_not_regenerate(self):
        async def failed(sql, context):
            raise QueryError("source_unavailable", "Источник недоступен")

        self.executor.execute = failed
        agent, generator = self.agent([SQL])
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.error_code, "source_unavailable")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(generator.calls), 1)

    async def test_empty_catalog_does_not_call_models_or_database(self):
        self.catalog.value = replace(self.catalog.value, tables=())
        agent, generator = self.agent([SQL])
        result = await agent.run(AgentRequest("Выручка"))
        self.assertEqual(result.status, "not_found")
        self.assertEqual(generator.calls, [])
        self.assertEqual(self.executor.calls, [])

    async def test_selected_metric_pins_its_table_even_when_retrieval_misses_it(self):
        searches = []

        class Retriever:
            async def retrieve(self, question, snapshot, context):
                searches.append(question)
                return [RetrievedTable(STORES, 1)]

        metric = MetricDefinition(
            "revenue", "Revenue", "Sum of sales.orders.amount", tables=(ORDERS.ref,)
        )
        agent, generator = self.agent(
            ['SELECT SUM(t0."amount") FROM "sales"."orders" AS t0'],
            retriever=Retriever(),
            settings=AgentSettings(max_tables=1),
        )
        result = await agent.run(
            AgentRequest(
                "Compare",
                metrics=(metric,),
                filters={"metric_key": "revenue", "date_from": "2026-09-01"},
            )
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.tables, ("sales.orders",))
        self.assertEqual(result.result.rows, [[230]])
        self.assertIn("Sum of sales.orders.amount", searches[0])
        self.assertIn("2026-09-01", searches[0])
        self.assertEqual(len(generator.calls), 1)

    async def test_invalid_base_or_unknown_selected_metric_stops_before_retrieval(self):
        class Retriever:
            async def retrieve(self, *args):
                raise AssertionError("Unauthorized conversation metadata reached retrieval")

        for request in (
            AgentRequest(
                "Continue", ConversationContext("Private", "SELECT * FROM secret.accounts")
            ),
            AgentRequest("Compare", filters={"metric_key": "missing"}),
        ):
            with self.subTest(request=request):
                agent, generator = self.agent([SQL], retriever=Retriever())
                result = await agent.run(request)
                self.assertEqual(result.status, "error")
                self.assertEqual(generator.calls, [])
