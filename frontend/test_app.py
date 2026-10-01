from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


AVAILABLE = all(importlib.util.find_spec(name) for name in ("streamlit", "pandas", "requests", "yaml"))


@unittest.skipUnless(AVAILABLE, "Нужны зависимости Streamlit")
class AppTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(sys, "path", [str(Path(__file__).parent), *sys.path]))
        from streamlit.testing.v1 import AppTest
        import client

        self.client = Mock()
        self.client.schemas.return_value = {"schemas": ["sales"], "unindexed_schemas": []}
        self.stack.enter_context(patch.object(client, "ApiClient", return_value=self.client))
        self.stack.enter_context(patch.dict(os.environ, {"API_TOKEN": "test-token-" * 4, "API_URL": "http://localhost:8000"}))
        self.app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=60)
        self.app.run()
        self.assertFalse(self.app.exception)

    def show(self, content):
        done = Event()
        done.set()
        snapshot = {"progress": [], "request_id": "query1", "elapsed": 1, "cancel_note": None,
                    "terminal": {"event": "result", "content": content}}
        job = SimpleNamespace(done=done, local_id="job1", question="Выручка по месяцам", schemas=("sales",),
                              touch=Mock(), snapshot=lambda: snapshot)
        self.app.session_state["job"] = job
        self.app.session_state["completed_job"] = job.local_id
        self.app.run()
        self.assertFalse(self.app.exception)

    def test_table_precedes_collapsed_sql_block(self):
        sql = 'SELECT 125000 AS "revenue"'
        self.show({"status": "success", "sql": sql, "columns": ["revenue"], "rows": [[125000]]})
        self.assertEqual(self.app.dataframe[0].value.iloc[0, 0], 125000)
        self.assertEqual(self.app.code[-1].value, sql)
        block = next(item for item in self.app.expander if item.label == "SQL этого результата")
        self.assertFalse(block.proto.expanded)
        elements = list(self.app.main)
        table_index = next(index for index, item in enumerate(elements) if item.type == "arrow_data_frame")
        sql_index = next(index for index, item in enumerate(elements) if item.type == "expander" and item.label == block.label)
        self.assertLess(table_index, sql_index)

    def test_failed_sql_is_labeled_as_unconfirmed(self):
        self.show({"status": "error", "sql": "SELECT bad", "error": "Не удалось выполнить запрос."})
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertTrue(any(item.label == "Последний сформированный SQL" for item in self.app.expander))
        self.assertFalse(any(item.label == "SQL этого результата" for item in self.app.expander))
        self.assertTrue(self.app.error)

    def test_empty_result_keeps_sql_available(self):
        self.show({"status": "empty", "sql": "SELECT 1 WHERE FALSE", "columns": ["value"], "rows": []})
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertTrue(any("Подходящих строк нет" in item.value for item in self.app.info))
        self.assertTrue(any(item.label == "SQL этого результата" for item in self.app.expander))

    def test_refusal_does_not_display_a_fictitious_query(self):
        self.show({"status": "not_found", "sql": None, "error": "Не хватает данных для ответа."})
        self.assertEqual(len(self.app.code), 0)
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertTrue(any("Не хватает данных" in item.value for item in self.app.info))

    def test_uploader_uses_configured_limit_instead_of_streamlit_default(self):
        with patch.dict(os.environ, {"MAX_UPLOAD_BYTES": str(2 * 1024 ** 2 + 1)}):
            self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.get("file_uploader")[0].proto.max_upload_size_mb, 3)
