from email.parser import BytesParser
from email.policy import default
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
import sys
from threading import Event
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


transport = ModuleType("requests")
transport.RequestException = type("RequestException", (Exception,), {})
transport.Timeout = type("Timeout", (transport.RequestException,), {})
transport.post = Mock()
transport.request = Mock()
spec = spec_from_file_location("frontend_client_test_subject", Path(__file__).with_name("client.py"))
client = module_from_spec(spec)
with patch.dict(sys.modules, {"requests": transport}):
    spec.loader.exec_module(client)


def event(kind, content=None, request_id="abc123"):
    return (json.dumps({"event": kind, "request_id": request_id, "content": content or {}},
                       ensure_ascii=False) + "\n").encode("utf-8")


class Response:
    status_code = 200

    def __init__(self, chunks, actions=None):
        self.chunks, self.actions = chunks, actions if actions is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.actions.append("close")

    def iter_content(self, chunk_size):
        yield from self.chunks


class ClientTests(unittest.TestCase):
    def setUp(self):
        transport.post.reset_mock(side_effect=True, return_value=True)
        transport.request.reset_mock(side_effect=True, return_value=True)

    def test_protocol_handles_split_utf8_and_multiple_events(self):
        encoded = event("stage", {"message": "Готово"}) + event("heartbeat")
        chunks = [encoded[index:index + 1] for index in range(len(encoded))]
        events = list(client._events(Response(chunks), 1024))
        self.assertEqual(events[0]["content"]["message"], "Готово")
        self.assertEqual(events[1]["event"], "heartbeat")

    def test_partial_and_oversized_events_fail(self):
        for chunks, limit in [([b"x" * 30], 20), ([b'{"event":'], 100)]:
            with self.subTest(chunks=chunks), self.assertRaises(client.ApiError):
                list(client._events(Response(chunks), limit))

    def test_progress_cannot_evict_terminal(self):
        chunks = [event("accepted"), *[event("stage", {"message": str(index)}) for index in range(100)],
                  event("result", {"status": "success", "rows": [], "columns": []})]
        transport.post.return_value = Response(chunks)
        job = client.QueryJob(client.ApiClient("http://localhost", "token"), "question", ["sales"], progress_limit=3)
        self.assertTrue(job.done.wait(2))
        snapshot = job.snapshot()
        self.assertEqual([item["message"] for item in snapshot["progress"]], ["97", "98", "99"])
        self.assertEqual(snapshot["terminal"]["event"], "result")

    def test_eof_without_terminal_is_an_error(self):
        transport.post.return_value = Response([event("accepted")])
        job = client.QueryJob(client.ApiClient("http://localhost", "token"), "question", ["sales"])
        self.assertTrue(job.done.wait(2))
        self.assertEqual(job.snapshot()["terminal"]["event"], "error")

    def test_events_of_another_request_are_rejected(self):
        transport.post.return_value = Response([event("accepted"), event("result", request_id="another")])
        job = client.QueryJob(client.ApiClient("http://localhost", "token"), "question", ["sales"])
        self.assertTrue(job.done.wait(2))
        self.assertIn("другого запроса", job.snapshot()["terminal"]["content"]["message"])

    def _cancellable_job(self, *, lease_seconds=45):
        actions, stop = [], Event()

        def chunks():
            yield event("accepted")
            while not stop.wait(0.01):
                yield event("heartbeat")

        response = Response(chunks(), actions)
        transport.post.return_value = response
        api = client.ApiClient("http://localhost", "token")

        def cancel(request_id):
            actions.append("cancel")
            return True

        api.cancel = cancel
        job = client.QueryJob(api, "question", ["sales"], lease_seconds=lease_seconds)
        self.addCleanup(stop.set)
        self.addCleanup(job.cancel)
        return job, actions

    def test_cancel_notifies_server_before_closing_stream(self):
        job, actions = self._cancellable_job()
        job.cancel()
        self.assertTrue(job.done.wait(2))
        self.assertEqual(actions, ["cancel", "close"])
        self.assertEqual(job.snapshot()["terminal"]["content"]["code"], "cancelled")

    def test_abandoned_session_is_cancelled(self):
        job, actions = self._cancellable_job(lease_seconds=0.02)
        self.assertTrue(job.done.wait(2))
        self.assertEqual(actions, ["cancel", "close"])

    def _already_completed_job(self, following):
        watch_finished = Event()

        class CancelResponse(Response):
            status_code = 404

        class ObservedJob(client.QueryJob):
            def _watch(self):
                try:
                    super()._watch()
                finally:
                    watch_finished.set()

        def chunks():
            yield event("accepted")
            if not watch_finished.wait(2):
                raise AssertionError("Cancellation was not requested")
            if isinstance(following, Exception):
                raise following
            yield from following

        stream = Response(chunks())

        def post(url, **kwargs):
            return CancelResponse([]) if url.endswith("/cancel") else stream

        transport.post.side_effect = post
        job = ObservedJob(client.ApiClient("http://localhost", "token"), "question", ["sales"])
        job.cancel()
        self.assertTrue(job.done.wait(2))
        return job.snapshot()["terminal"]

    def test_completed_cancel_preserves_pending_success_result(self):
        payload = {"status": "success", "sql": "SELECT 1", "columns": ["value"], "rows": [[1]]}
        terminal = self._already_completed_job([event("result", payload)])
        self.assertEqual(terminal, {"event": "result", "request_id": "abc123", "content": payload})

    def test_completed_cancel_without_terminal_reports_connection_error(self):
        terminal = self._already_completed_job([])
        self.assertEqual(terminal["event"], "error")
        self.assertEqual(terminal["content"]["code"], "connection_error")
        self.assertIn("без результата", terminal["content"]["message"])

    def test_completed_cancel_still_obeys_network_timeout(self):
        terminal = self._already_completed_job(transport.Timeout("read timeout"))
        self.assertEqual(terminal["event"], "error")
        self.assertEqual(terminal["content"]["code"], "connection_error")

    def test_multipart_has_exact_length_and_payload(self):
        data = b"SQLite test\x00data" * 10000
        upload = BytesIO(data)
        upload.name = "данные.sqlite"
        body = client._MultipartFiles([upload], len(data))
        chunks = list(body)
        wire = b"".join(chunks)
        self.assertEqual(len(wire), len(body))
        self.assertLessEqual(max(map(len, chunks)), 64 * 1024)
        header = f"Content-Type: multipart/form-data; boundary={body.boundary}\r\n\r\n".encode()
        message = BytesParser(policy=default).parsebytes(header + wire)
        part = next(message.iter_parts())
        self.assertEqual(part.get_payload(decode=True), data)
        self.assertEqual(part.get_filename(), "данные.sqlite")

    def test_upload_limit_applies_to_all_files(self):
        uploads = [BytesIO(b"1234"), BytesIO(b"5678")]
        for index, upload in enumerate(uploads):
            upload.name = f"{index}.db"
        with self.assertRaises(client.ApiError):
            client._MultipartFiles(uploads, 7)

    def test_invalid_url_does_not_escape_as_value_error(self):
        for url in ("http://[bad", "http://localhost:bad", "file:///etc", "http://user:secret@localhost"):
            with self.subTest(url=url), self.assertRaises(client.ApiError):
                client.ApiClient(url, "token")


if __name__ == "__main__":
    unittest.main()
