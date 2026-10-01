from importlib.util import find_spec
import unittest

from src.agent.graph import SQLAgentGraph
from src.domain.query import GenerationRefusal, QueryContext, QueryError
from tests.test_agent import (
    FakeContextBuilder, FakeExecutor, FakeGenerator, FakeReranker,
    FakeRetriever, FakeValidator, SQL,
)


@unittest.skipUnless(find_spec("langgraph"), "LangGraph not installed; no installation performed")
class GraphFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, *, max_corrections=3):
        class Generator(FakeGenerator):
            calls = 0

            async def generate(self, messages, grammar, context):
                self.calls += 1
                return SQL + f" LIMIT {self.calls}"

        self.generator, self.validator, self.executor = Generator(), FakeValidator(), FakeExecutor()
        return SQLAgentGraph(FakeRetriever(), FakeReranker(), FakeContextBuilder(),
                             self.generator, self.validator, self.executor,
                             max_corrections=max_corrections)

    async def test_success_reaches_terminal_result(self):
        agent = self.make_agent()
        result = await agent.run("Заказы", ("sales",), QueryContext())
        self.assertEqual((result.status, result.attempts, self.executor.calls), ("success", 1, 1))
        self.assertEqual(result.result.rows, [[1]])
        self.assertEqual(result.sql, result.result.sql)

    async def test_corrections_have_a_finite_budget_and_preserve_last_sql(self):
        agent = self.make_agent(max_corrections=3)
        self.validator.failure = QueryError("invalid_sql", "Bad expression", retryable=True)
        result = await agent.run("Заказы", ("sales",), QueryContext())
        self.assertEqual((result.status, result.error_code, result.attempts), ("error", "invalid_sql", 4))
        self.assertEqual(self.generator.calls, 4)
        self.assertEqual(self.executor.calls, 0)
        self.assertEqual(result.sql, SQL + " LIMIT 4")

    async def test_unrecoverable_failure_does_not_regenerate(self):
        agent = self.make_agent()
        self.executor.failure = QueryError("query_timeout", "Timeout")
        result = await agent.run("Заказы", ("sales",), QueryContext())
        self.assertEqual((result.error_code, self.generator.calls), ("query_timeout", 1))

    async def test_no_schemas_never_calls_generator(self):
        agent = self.make_agent()
        result = await agent.run("Заказы", (), QueryContext())
        self.assertEqual((result.status, self.generator.calls), ("not_found", 0))

    async def test_refusals_do_not_reach_validator_or_database(self):
        for refusal in GenerationRefusal:
            agent = self.make_agent()

            async def refuse(*args):
                return refusal.value

            self.generator.generate = refuse
            self.validator.failure = AssertionError("A refusal must not reach SQL validation")
            result = await agent.run("Невыполнимый вопрос", ("sales",), QueryContext())
            self.assertEqual((result.status, result.attempts), ("not_found", 1))
            self.assertEqual(result.error_code, refusal.value.lower())
            self.assertIsNone(result.sql)
            self.assertEqual(self.executor.calls, 0)


if __name__ == "__main__":
    unittest.main()
