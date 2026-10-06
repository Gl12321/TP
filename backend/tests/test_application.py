import asyncio
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

import httpx

from backend.app.assistant.models import QueryRun
from backend.app.infrastructure.config import Settings
from backend.app.infrastructure.database import utcnow
from backend.app.jobs.models import Job, WorkerHeartbeat
from backend.app.jobs.service import claim_job, finish_job, renew_lease
from backend.app.main import create_app
from backend.app.sources.models import Source
from backend.app.worker import Worker
from sql_agent.contracts import AgentResult, QueryResult


class ResultEngine:
    model = "test-contract"

    async def run(self, run, source, stores, metrics, base, context, check_access):
        await check_access()
        return AgentResult(
            "success",
            result=QueryResult(
                'SELECT SUM(t."amount") FROM "reporting"."sales" AS t',
                ["total"],
                [["123456789012345.6790"]],
                False,
                ["numeric"],
            ),
        )


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_url="sqlite+aiosqlite:///" + str(Path(self.directory.name) / "app.db"),
            secret_key="test-secret-" * 5,
            bootstrap_token="setup-token",
            secure_cookies=False,
            source_hosts=("reporting.test",),
            job_lease_seconds=6,
        )
        self.app = create_app(self.settings)
        self.database = self.app.state.database
        await self.database.create_schema()
        self.clients = []
        self.client = self.new_client()
        session = await self.request(
            "post",
            "/api/v1/auth/bootstrap",
            201,
            {
                "email": "owner@example.org",
                "password": "a-good-password-123",
                "name": "Owner",
                "workspace_name": "Network",
                "bootstrap_token": "setup-token",
            },
        )
        self.client.headers["X-CSRF-Token"] = session["csrf_token"]
        self.user_id = session["user"]["id"]
        self.prefix = "/api/v1/workspaces/" + session["workspaces"][0]["id"]
        self.stores = [
            await self.request(
                "post",
                "/stores",
                201,
                {
                    "name": code,
                    "code": code,
                    "city": "City",
                },
            )
            for code in ("A", "B")
        ]
        source = await self.request(
            "post",
            "/sources",
            201,
            {
                "name": "Reporting",
                "host": "reporting.test",
                "database": "reporting",
                "username": "reader",
                "password": "not-a-password",
                "schemas": ["reporting"],
            },
        )
        self.source_id = source["id"]
        async with self.database.sessions.begin() as db:
            db.add(WorkerHeartbeat(id="worker", updated_at=utcnow(), status="ready"))
            record = await db.get(Source, self.source_id)
            record.status, record.catalog_version = "ready", 1
            record.catalog = [
                {
                    "schema": "reporting",
                    "name": "sales",
                    "columns": [
                        {"name": name, "data_type": kind, "nullable": False}
                        for name, kind in (("store_code", "text"), ("amount", "numeric"))
                    ],
                }
            ]
        await self.request(
            "put",
            "/sources/" + self.source_id + "/policies",
            200,
            {
                "tables": [
                    {
                        "schema": "reporting",
                        "name": "sales",
                        "columns": ["store_code", "amount"],
                        "store_column": "store_code",
                        "shared": False,
                    }
                ],
            },
        )

    def new_client(self):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.clients.append(client)
        return client

    async def request(self, method, path, status=200, body=None, client=None):
        path = path if path.startswith("/api/") or path == "/ready" else self.prefix + path
        response = await (client or self.client).request(method, path, json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json() if response.content else None

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        await self.database.close()
        self.directory.cleanup()

    async def member(self, email="manager@example.org", **overrides):
        body = {
            "email": email,
            "name": "Manager",
            "password": "member-password-123",
            "role": "store_manager",
            "all_stores": False,
            "store_ids": [self.stores[0]["id"]],
            "data_access": True,
            **overrides,
        }
        member = await self.request("post", "/members", 201, body)
        client = self.new_client()
        session = await self.request(
            "post",
            "/api/v1/auth/login",
            body={
                "email": email,
                "password": body["password"],
            },
            client=client,
        )
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        return client, member

    async def question(self, client=None):
        conversation = await self.request("post", "/conversations", 201, {}, client)
        path = "/conversations/" + conversation["id"] + "/messages"
        body = {
            "question": "Revenue",
            "source_id": self.source_id,
            "store_ids": [self.stores[0]["id"]],
            "version": 0,
            "idempotency_key": "request-" + conversation["id"],
        }
        return await self.request("post", path, 202, body, client), path, body

    async def test_session_csrf_and_workspace_store_source_boundaries(self):
        csrf = self.client.headers.pop("X-CSRF-Token")
        await self.request("post", "/conversations", 403, {})
        self.client.headers["X-CSRF-Token"] = csrf
        manager, member = await self.member()
        stores = await self.request("get", "/stores", client=manager)
        self.assertEqual([store["code"] for store in stores], ["A"])
        other = await self.request("post", "/api/v1/workspaces", 201, {"name": "Other"})
        await self.request(
            "get", "/api/v1/workspaces/" + other["id"] + "/stores", 404, client=manager
        )
        await self.request(
            "post",
            "/cases",
            403,
            {
                "title": "Outside scope",
                "store_ids": [self.stores[1]["id"]],
            },
            manager,
        )
        source = (await self.request("get", "/sources", client=manager))[0]
        self.assertTrue({"username", "password"}.isdisjoint(source))
        await self.request(
            "patch",
            "/sources/" + self.source_id,
            body={
                "reader_ids": [member["user_id"]],
            },
        )
        self.assertFalse((await self.request("get", "/sources"))[0]["can_read"])
        await self.request("patch", "/members/" + member["id"], body={"data_access": False})
        await self.request("post", "/conversations", 403, {}, manager)
        response = await self.client.get("/api/v1/auth/session")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        await self.request("post", "/api/v1/auth/logout")
        await self.request("get", "/api/v1/auth/session", 401)

    async def test_queue_idempotency_cancellation_and_expired_lease(self):
        run, path, body = await self.question()
        duplicate = await self.request("post", path, 202, body)
        self.assertEqual(duplicate["id"], run["id"])
        await self.request("post", path, 409, {**body, "question": "Changed"})
        async with self.database.sessions.begin() as db:
            job_id, token, _ = await claim_job(db, 6)
        await self.request("post", "/runs/" + run["id"] + "/cancel")
        async with self.database.sessions.begin() as db:
            self.assertTrue(
                await finish_job(
                    db,
                    job_id,
                    token,
                    {
                        "status": "succeeded",
                        "result": {"rows": [[999]]},
                    },
                )
            )
        cancelled = await self.request("get", "/runs/" + run["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["result"])
        run, _, _ = await self.question()
        async with self.database.sessions.begin() as db:
            job_id, token, _ = await claim_job(db, 6)
            (await db.get(Job, job_id)).lease_until = utcnow() - timedelta(seconds=1)
        async with self.database.sessions.begin() as db:
            self.assertIsNone(await renew_lease(db, job_id, token, 6))
            self.assertFalse(await finish_job(db, job_id, token, {"status": "succeeded"}))
            self.assertIsNone(await claim_job(db, 6))
            self.assertEqual((await db.get(QueryRun, run["id"])).status, "failed")

    async def test_report_snapshot_creation_retries_and_access_revocation(self):
        run, _, _ = await self.question()
        self.assertTrue(await Worker(self.database, self.settings, ResultEngine()).run_once())
        result = await self.request("get", "/runs/" + run["id"])
        self.assertEqual(result["result"]["rows"], [["123456789012345.6790"]])
        self.assertEqual(result["result"]["columns"], [{"name": "total", "type": "numeric"}])
        for path, values in (
            ("/reports", {"run_id": run["id"]}),
            ("/cases", {"store_ids": [self.stores[0]["id"]]}),
        ):
            body = {"title": "Once", "idempotency_key": "retry-" + path, **values}
            first, second = await asyncio.gather(
                self.request("post", path, 201, body), self.request("post", path, 201, body)
            )
            self.assertEqual(first["id"], second["id"])
            await self.request("post", path, 409, {**body, "title": "Changed"})
        report = (await self.request("get", "/reports"))[0]
        async with self.database.sessions.begin() as db:
            (await db.get(Source, self.source_id)).catalog_version += 1
        await self.request("get", "/reports/" + report["id"])
        manager, _ = await self.member()
        queued, _, _ = await self.question(manager)
        await self.request("patch", "/sources/" + self.source_id, body={"reader_ids": []})
        self.assertTrue(await Worker(self.database, self.settings, ResultEngine()).run_once())
        async with self.database.sessions() as db:
            stopped = await db.get(QueryRun, queued["id"])
            self.assertEqual(stopped.status, "rejected")
            self.assertIsNone(stopped.result)
        await self.request("get", "/reports/" + report["id"], 403)

    async def test_case_recipient_scope_answer_and_close(self):
        reader_client, reader = await self.member()
        blocked_client, blocked = await self.member(email="blocked@example.org")
        await self.request(
            "patch",
            "/sources/" + self.source_id,
            body={
                "reader_ids": [self.user_id, reader["user_id"]],
            },
        )
        run, _, _ = await self.question()
        self.assertTrue(await Worker(self.database, self.settings, ResultEngine()).run_once())
        body = {
            "title": "Investigate",
            "run_id": run["id"],
            "store_ids": [self.stores[0]["id"]],
            "assignee_id": blocked["user_id"],
            "idempotency_key": "case-recipient-retry",
        }
        rejected = await self.request("post", "/cases", 403, body)
        self.assertEqual(rejected["error"]["code"], "recipient_scope")
        case = await self.request("post", "/cases", 201, {**body, "assignee_id": reader["user_id"]})
        path = "/cases/" + case["id"]
        await self.request("get", path, client=reader_client)
        await self.request("get", path, 403, client=blocked_client)
        await self.request(
            "post",
            path + "/questions",
            403,
            {
                "body": "Explain",
                "assignee_id": blocked["user_id"],
            },
        )
        self.assertEqual(await self.request("get", "/notifications", client=blocked_client), [])
        question = await self.request(
            "post",
            path + "/questions",
            201,
            {
                "body": "Explain",
                "assignee_id": reader["user_id"],
            },
        )
        answer_path = path + "/questions/" + question["id"] + "/answer"
        await self.request("post", answer_path, 403, {"answer": "Wrong author"})
        answer = await self.request(
            "post", answer_path, body={"answer": "Checked"}, client=reader_client
        )
        self.assertEqual(answer["status"], "answered")
        self.assertTrue(await self.request("get", "/notifications", client=reader_client))
        closed = await self.request("post", path + "/close", body={"conclusion": "Resolved"})
        self.assertEqual(closed["status"], "closed")
        await self.request("post", path + "/comments", 409, {"body": "Late"}, reader_client)
