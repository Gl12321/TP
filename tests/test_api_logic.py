import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from src.domain.query import AgentResult, QueryError, QueryResult

HAS_API = all(importlib.util.find_spec(name) for name in (
    "fastapi", "pydantic_settings", "httpx", "multipart",
))
if HAS_API:
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    from src.api.dependencies import Services
    from src.api.routes.queries import ask_sql
    from src.api.routes.schemas import upload_schema_name
    from src.core.concurrency import finish_task

TOKEN = "local-test-token-with-32-characters"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def resources():
    settings = SimpleNamespace(API_TOKEN=TOKEN, MAX_UPLOAD_BYTES=1024,
                               MAX_RESULT_BYTES=4096, EVENT_QUEUE_SIZE=2, SQLITE_PATH="unused")
    return Services(
        settings=settings, database=Mock(),
        schema_reader=SimpleNamespace(list_schemas=AsyncMock(return_value=["sales"])),
        importer=Mock(), catalog=SimpleNamespace(dirty_schemas=set()),
        agent=SimpleNamespace(run=AsyncMock(return_value=AgentResult(
            "success", QueryResult('SELECT 1', ["count"], [[1]]), attempts=1,
        ))), generator=Mock(),
    )


@unittest.skipUnless(HAS_API, "FastAPI/httpx/pydantic-settings/python-multipart not installed")
class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.services = resources()
        self.client = self.enterContext(TestClient(create_app(self.services)))

    def query(self, **payload):
        return self.client.post("/question/stream", headers=HEADERS,
                                json={"question": "Сколько продаж?", "schemas_for_search": "all", **payload})

    def test_token_is_required(self):
        self.assertEqual(self.client.get("/schema_show").status_code, 401)
        self.assertEqual(self.client.get("/schema_show", headers=HEADERS).json()["schemas"], ["sales"])

    def test_stream_contract_and_job_cleanup(self):
        response = self.query()
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.iter_lines() if line]
        self.assertEqual(events[0]["event"], "accepted")
        self.assertEqual(events[-1]["event"], "result")
        self.assertEqual(events[-1]["content"]["rows"], [[1]])
        self.assertEqual({event["request_id"] for event in events}, {events[0]["request_id"]})
        self.assertFalse(self.services.jobs)
        self.assertFalse(self.services.tasks)

    def test_unknown_schema_is_rejected_before_agent(self):
        events = [json.loads(line) for line in self.query(schemas_for_search=["other"]).iter_lines()]
        self.assertEqual(events[-1]["content"]["code"], "unknown_schema")
        self.services.agent.run.assert_not_awaited()

    def test_busy_does_not_queue_inference(self):
        class BusyGate:
            @asynccontextmanager
            async def enter(self):
                raise QueryError("busy", "Занято")
                yield
        self.services.gate = BusyGate()
        events = [json.loads(line) for line in self.query().iter_lines()]
        self.assertEqual(events[-1]["content"]["code"], "busy")
        self.services.agent.run.assert_not_awaited()

    def test_empty_success_preserves_columns(self):
        self.services.agent.run.return_value = AgentResult("success", QueryResult("SELECT 1", ["id"], []))
        result = json.loads(list(self.query().iter_lines())[-1])["content"]
        self.assertEqual((result["status"], result["columns"], result["rows"]), ("success", ["id"], []))

    def test_failed_query_keeps_sql_for_inspection(self):
        self.services.agent.run.return_value = AgentResult(
            "error", error="Bad expression", error_code="invalid_sql", attempts=4, sql="SELECT broken",
        )
        result = json.loads(list(self.query().iter_lines())[-1])["content"]
        self.assertEqual((result["sql"], result["attempts"], result["rows"]), ("SELECT broken", 4, []))

    def test_input_and_body_limits(self):
        self.assertEqual(self.query(question="   ").status_code, 422)
        self.assertEqual(self.query(schemas_for_search=[]).status_code, 422)
        self.assertEqual(self.query(question="x" * 70000).status_code, 413)

    def test_completed_request_cannot_be_cancelled(self):
        self.assertEqual(self.client.post("/queries/missing/cancel", headers=HEADERS).status_code, 404)

    def test_filename_validation_handles_both_path_flavours(self):
        for value in ["../sales.db", r"..\sales.db", r"C:\sales.db", "public.db", "pg_catalog.db", "a.txt"]:
            with self.subTest(value=value), self.assertRaises(QueryError):
                upload_schema_name(value)
        self.assertEqual(upload_schema_name("Продажи.sqlite"), "Продажи")

    def test_rebuild_preserves_pending_schemas(self):
        self.services.catalog.reset_store = AsyncMock()
        self.services.catalog.index_all_schemas = AsyncMock()
        response = self.client.post("/schemas/reindex", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.services.catalog.reset_store.assert_awaited_once_with(schemas_to_reindex=["sales"])
        self.services.catalog.index_all_schemas.assert_awaited_once()

    def test_invalid_upload_preserves_index_and_removes_temporary_file(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.services.settings.SQLITE_PATH = directory
        self.services.catalog.mark_dirty = AsyncMock()
        self.services.catalog.index_schema = AsyncMock()
        self.services.importer.migrate_db.side_effect = ValueError("Invalid SQLite")
        response = self.client.post("/load_schema", headers=HEADERS, files={
            "files": ("sales.db", b"invalid sqlite", "application/octet-stream"),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_sqlite")
        self.services.catalog.mark_dirty.assert_not_awaited()
        self.services.catalog.index_schema.assert_not_awaited()
        self.assertEqual(list(Path(directory).iterdir()), [])

    def test_partial_upload_reports_completed_schemas(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.services.settings.SQLITE_PATH = directory
        order = []

        async def invalidate(schema):
            order.append(("dirty", schema))

        async def index(schema):
            order.append(("index", schema))

        def migrate(schema, path, *, before_replace):
            self.assertTrue(Path(path).is_file())
            if schema == "broken":
                raise ValueError("Invalid SQLite")
            before_replace()
            order.append(("replace", schema))

        self.services.catalog.mark_dirty = invalidate
        self.services.catalog.index_schema = index
        self.services.importer.migrate_db = migrate
        response = self.client.post("/load_schema", headers=HEADERS, files=[
            ("files", ("first.db", b"first", "application/octet-stream")),
            ("files", ("broken.db", b"second", "application/octet-stream")),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["loaded_schemas"], ["first"])
        self.assertEqual(order, [("dirty", "first"), ("replace", "first"), ("index", "first")])
        self.assertEqual(list(Path(directory).iterdir()), [])


@unittest.skipUnless(HAS_API, "FastAPI dependencies not installed")
class StreamingCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_retains_gate_until_worker_exits(self):
        service = resources()
        started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def native_work():
            started.set()
            await release.wait()
            return AgentResult("cancelled")

        async def run(*_args):
            worker = asyncio.create_task(native_work())
            return await finish_task(worker, on_cancel=cancelled.set)

        service.agent.run = run
        response = await ask_sql(SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                                 SimpleNamespace(question="count", schemas_for_search="all"), service)
        iterator = response.body_iterator
        await anext(iterator)
        await started.wait()
        cleanup = asyncio.create_task(iterator.aclose())
        await cancelled.wait()
        with self.assertRaises(QueryError) as error:
            async with service.gate.enter():
                pass
        self.assertEqual(error.exception.code, "busy")
        release.set()
        await asyncio.wait_for(cleanup, 2)
        async with service.gate.enter():
            self.assertFalse(service.jobs)


if __name__ == "__main__":
    unittest.main()
