import asyncio
import json
from threading import Event
import unittest

from src.agent.correction import CorrectionPolicy
from src.agent.graph import SQLAgentGraph
from src.domain.query import GenerationRefusal, QueryContext, QueryError, QueryResult
from src.domain.schema import Column, RetrievedTable, TableRef, TableSchema
from src.llm.client import LLMClient
from src.llm.prompts import build_messages


TABLE = TableSchema(TableRef("sales", "orders"), (Column("id", "INTEGER"),),
                    ddl='CREATE TABLE "sales"."orders" ("id" INTEGER);')
SQL = 'SELECT t0."id" FROM "sales"."orders" AS t0'


class CorrectionTests(unittest.TestCase):
    def test_three_corrections_mean_four_generation_attempts(self):
        policy = CorrectionPolicy(max_corrections=3)
        self.assertEqual([policy.should_retry(attempts=attempt, retryable=True)
                          for attempt in (1, 2, 3, 4)], [True, True, True, False])
        self.assertFalse(policy.should_retry(attempts=1, retryable=False))
        self.assertFalse(CorrectionPolicy(0).should_retry(attempts=1, retryable=True))

    def test_repeat_detection_preserves_whitespace_inside_string_literals(self):
        policy = CorrectionPolicy()
        self.assertTrue(policy.repeated(" \n" + SQL + "\n", [SQL]))
        self.assertFalse(policy.repeated("SELECT 'a b'", ["SELECT 'a  b'"]))
        self.assertFalse(policy.repeated("SELECT 'ABC'", ["SELECT 'abc'"]))

    def test_correction_prompt_contains_exact_previous_sql_and_error(self):
        previous = "SELECT '; -- quoted value' FROM missing"
        messages = build_messages("Заказы за месяц", [TABLE], previous_sql=previous,
                                  error="Table missing does not exist")
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(previous, messages[2]["content"])
        self.assertEqual(json.loads(messages[3]["content"])["error"]["message"], "Table missing does not exist")
        self.assertEqual(json.loads(messages[1]["content"])["tables"][0]["columns"][0]["name"], "id")
        self.assertNotIn("<|start_header_id|>", str(messages))

    def test_aliases_are_stable_and_sql_identifiers_are_escaped(self):
        unusual = TableSchema(TableRef('A"B', "C"), (Column("id", "INTEGER"),), ddl="DDL")
        messages = build_messages("question", [TABLE, unusual])
        sources = [item["source"] for item in json.loads(messages[1]["content"])["tables"]]
        self.assertEqual(sources, ['"A""B"."C" AS t0', '"sales"."orders" AS t1'])
        self.assertEqual(messages, build_messages("question", [unusual, TABLE]))


class FakeRetriever:
    async def retrieve(self, question, schemas, context):
        context.check_cancelled()
        return [RetrievedTable(TABLE, 0.9)]


class FakeReranker:
    async def rerank(self, question, documents, context):
        return documents


class FakeContextBuilder:
    async def build(self, ranked, schemas, context):
        return [item.table for item in ranked]


class FakeGenerator:
    max_tokens = 20
    context_size = 100
    token_count = 40
    next_sql = SQL

    def count_tokens(self, messages):
        return self.token_count

    async def generate(self, messages, grammar, context):
        context.check_cancelled()
        return self.next_sql


class FakeValidator:
    failure = None

    def validate(self, sql, tables):
        if self.failure is not None:
            raise self.failure
        return sql + ";"


class FakeExecutor:
    failure = None
    calls = 0

    async def execute(self, sql, context):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return QueryResult(sql, ["id"], [[1]])


class NodeGraph(SQLAgentGraph):
    def _build_graph(self):

        return None


class AgentNodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.generator = FakeGenerator()
        self.validator = FakeValidator()
        self.executor = FakeExecutor()
        self.agent = NodeGraph(FakeRetriever(), FakeReranker(), FakeContextBuilder(),
                               self.generator, self.validator, self.executor)
        self.state = {"question": "Заказы", "schemas": ("sales",), "context": QueryContext(),
                      "attempts": 0, "failed_sql": [], "status": "pending"}

    async def prepare(self):
        self.state.update(await self.agent._retrieve(self.state))
        self.state.update(await self.agent._context(self.state))
        self.state.update(await self.agent._generate(self.state))

    async def test_success_passes_validated_sql_to_executor_and_result(self):
        await self.prepare()
        self.state.update(await self.agent._execute(self.state))
        self.assertEqual(self.state["status"], "success")
        self.assertEqual(self.state["result"].sql, SQL + ";")
        self.assertEqual(self.state["result"].rows, [[1]])
        self.assertEqual(self.agent._after_execute(self.state), "stop")
        self.assertEqual(self.executor.calls, 1)

    async def test_invalid_sql_retries_with_previous_statement_and_reason(self):
        await self.prepare()
        self.validator.failure = QueryError("invalid_sql", "Unknown column", retryable=True)
        self.state.update(await self.agent._execute(self.state))
        self.assertEqual(self.executor.calls, 0)
        self.assertEqual(self.state["failed_sql"], [SQL])
        self.assertEqual(self.agent._after_execute(self.state), "correct")
        self.state.update(await self.agent._correct(self.state))
        self.assertEqual(SQL, self.state["messages"][2]["content"])
        self.assertEqual(json.loads(self.state["messages"][3]["content"])["error"]["message"], "Unknown column")
        self.state.update(await self.agent._generate(self.state))
        self.assertEqual(self.state["error_code"], "repeated_sql")
        self.assertEqual(self.state["attempts"], 2)

    async def test_non_retryable_database_failure_stops_correction(self):
        await self.prepare()
        self.executor.failure = QueryError("query_timeout", "Timed out")
        self.state.update(await self.agent._execute(self.state))
        self.assertEqual(self.agent._after_execute(self.state), "stop")
        self.assertEqual(self.state["error_code"], "query_timeout")

    async def test_cancellation_propagates_without_becoming_sql_error(self):
        await self.prepare()
        self.executor.failure = QueryError("cancelled", "Cancelled")
        with self.assertRaises(QueryError) as caught:
            await self.agent._execute(self.state)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(self.state["failed_sql"], [])

    async def test_empty_search_context_does_not_generate(self):
        self.state["documents"] = []
        result = await self.agent._context(self.state)
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("messages", result)

    async def test_refusal_is_a_terminal_outcome_not_executable_sql(self):
        self.state.update(await self.agent._retrieve(self.state))
        self.state.update(await self.agent._context(self.state))
        for refusal in GenerationRefusal:
            self.generator.next_sql = refusal.value
            result = await self.agent._generate(self.state)
            self.assertEqual(result["status"], "not_found")
            self.assertIsNone(result["sql"])
            self.assertEqual(result["error_code"], refusal.value.lower())
        self.assertEqual(self.executor.calls, 0)

    async def test_initial_and_correction_prompts_reserve_output_budget(self):
        self.state.update(await self.agent._retrieve(self.state))
        self.generator.token_count = 49
        with self.assertRaises(QueryError) as caught:
            await self.agent._context(self.state)
        self.assertEqual(caught.exception.code, "context_limit")
        self.generator.token_count = 40
        self.state.update(await self.agent._context(self.state))
        self.state.update({"sql": SQL, "error": "Long error", "attempts": 1})
        self.generator.token_count = 49
        with self.assertRaises(QueryError) as caught:
            await self.agent._correct(self.state)
        self.assertEqual(caught.exception.code, "context_limit")

    async def test_missing_context_refreshes_once_and_rebuilds_prompt_and_grammar(self):
        await self.prepare()
        extra = TableSchema(TableRef("sales", "products"), (Column("name", "TEXT"),),
                            ddl='CREATE TABLE "sales"."products" ("name" TEXT);')
        searches = []

        class AdditionalRetriever:
            async def retrieve(self, question, schemas, context):
                searches.append((question, schemas, context))
                return [RetrievedTable(TABLE, 0.8), RetrievedTable(extra, 0.7)]

        self.agent.retriever = AdditionalRetriever()
        old_grammar = self.state["grammar"]
        self.state.update({"error_code": "missing_context", "error": "Table products is missing",
                           "failed_sql": [SQL]})
        self.state.update(await self.agent._correct(self.state))
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0][1], ("sales",))
        self.assertIn(self.state["question"], searches[0][0])
        self.assertIn(SQL, searches[0][0])
        self.assertIn("Table products is missing", searches[0][0])
        self.assertEqual(len(self.state["documents"]), 2)
        self.assertEqual({item.ref for item in self.state["tables"]}, {TABLE.ref, extra.ref})
        self.assertTrue(self.state["context_refreshed"])
        self.assertNotEqual(self.state["grammar"], old_grammar)
        self.assertEqual(json.loads(self.state["messages"][1]["content"])["tables"][1]["columns"][0]["name"], "name")
        self.assertEqual(SQL, self.state["messages"][2]["content"])
        self.assertTrue(json.loads(self.state["messages"][3]["content"])["context_changed"])
        self.state.update(await self.agent._correct(self.state))
        self.assertEqual(len(searches), 1)

    async def test_changed_context_allows_revalidation_of_previous_sql(self):
        await self.prepare()
        extra = TableSchema(TableRef("sales", "products"), (Column("id", "INTEGER"),), ddl="DDL")

        class AdditionalRetriever:
            async def retrieve(self, question, schemas, context):
                return [RetrievedTable(extra)]

        self.agent.retriever = AdditionalRetriever()
        previous = 'SELECT t1."id" FROM "sales"."products" AS t1'
        self.state.update({"sql": previous, "failed_sql": [previous], "error_code": "missing_context",
                           "error": "Table products is missing"})
        self.state.update(await self.agent._correct(self.state))
        self.generator.next_sql = previous
        self.state.update(await self.agent._generate(self.state))
        self.assertEqual(self.state["status"], "pending")
        self.state.update(await self.agent._execute(self.state))
        self.assertEqual(self.state["status"], "success")

    async def test_unchanged_context_still_rejects_repeated_failed_sql(self):
        await self.prepare()
        self.state.update({"failed_sql": [SQL], "error_code": "missing_context",
                           "error": "Missing column"})
        self.state.update(await self.agent._correct(self.state))
        self.assertTrue(self.state["context_refreshed"])
        self.assertEqual(self.state["failed_sql"], [SQL])
        self.assertFalse(json.loads(self.state["messages"][3]["content"])["context_changed"])
        self.state.update(await self.agent._generate(self.state))
        self.assertEqual(self.state["error_code"], "repeated_sql")


class LLMCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_keeps_model_locked_until_native_work_finishes(self):
        started, release = Event(), Event()
        calls = []
        client = LLMClient.__new__(LLMClient)
        client._gate = asyncio.Lock()

        def generate(messages, grammar, context):
            calls.append(messages)
            if len(calls) == 1:
                started.set()
                if not release.wait(3):
                    raise RuntimeError("Test worker was not released")
            return SQL

        client._generate = generate
        first = asyncio.create_task(client.generate([], "grammar", QueryContext()))
        second = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            second = asyncio.create_task(client.generate([], "grammar", QueryContext()))
            await asyncio.sleep(0.02)
            self.assertEqual(len(calls), 1)
            self.assertFalse(second.done())
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
        self.assertTrue(first.cancelled())
        self.assertEqual(second.result(), SQL)


if __name__ == "__main__":
    unittest.main()
