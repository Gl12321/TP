import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import json
import sqlite3
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select, update

from backend.app.access.models import Session
from backend.app.assistant.models import QueryRun
from backend.app.infrastructure.config import Settings
from backend.app.infrastructure.database import utcnow
from backend.app.infrastructure.security import token_hash
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
        context.emit("executing", "Выполняем запрос")
        return AgentResult(
            "success",
            result=QueryResult(
                'SELECT SUM(t."amount") FROM "reporting"."sales" AS t',
                ["total"],
                [["42.50"]],
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
            secure_cookies=False,
            bootstrap_token="setup-token",
            source_hosts=("reporting.test",),
            job_lease_seconds=6,
        )
        self.app = create_app(self.settings)
        self.database = self.app.state.database
        await self.database.create_schema()
        self.clients = []
        self.client = self.new_client()
        response = await self.client.post(
            "/api/v1/auth/bootstrap",
            json={
                "email": "owner@example.org",
                "password": "a-good-password-123",
                "name": "Owner",
                "workspace_name": "Network",
                "bootstrap_token": "setup-token",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        session = response.json()
        self.client.headers["X-CSRF-Token"] = session["csrf_token"]
        self.user_id = session["user"]["id"]
        self.workspace_id = session["workspaces"][0]["id"]
        self.prefix = "/api/v1/workspaces/" + self.workspace_id
        self.stores = []
        for code in ("A", "B"):
            response = await self.client.post(
                self.prefix + "/stores", json={"name": code, "code": code, "city": "City"}
            )
            self.assertEqual(response.status_code, 201, response.text)
            self.stores.append(response.json())
        response = await self.client.post(
            self.prefix + "/sources",
            json={
                "name": "Reporting",
                "host": "reporting.test",
                "database": "reporting",
                "username": "reader",
                "password": "not-a-real-password",
                "schemas": ["reporting"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.source_id = response.json()["id"]
        async with self.database.sessions.begin() as db:
            db.add(
                WorkerHeartbeat(
                    id="test-worker", updated_at=utcnow(), status="ready", model="test-contract"
                )
            )
            source = await db.get(Source, self.source_id)
            source.status = "ready"
            source.catalog_version = 1
            source.catalog = [
                {
                    "schema": "reporting",
                    "name": "sales",
                    "columns": [
                        {"name": "store_code", "data_type": "text", "nullable": False},
                        {"name": "day", "data_type": "date", "nullable": False},
                        {"name": "amount", "data_type": "numeric", "nullable": False},
                        {"name": "secret", "data_type": "text", "nullable": True},
                    ],
                }
            ]
        response = await self.client.put(
            self.prefix + "/sources/" + self.source_id + "/policies",
            json={
                "tables": [
                    {
                        "schema": "reporting",
                        "name": "sales",
                        "columns": ["store_code", "day", "amount"],
                        "store_column": "store_code",
                        "shared": False,
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def new_client(self):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        await self.database.close()
        self.directory.cleanup()

    async def member(
        self, email="manager@example.org", role="store_manager", scope=None, data_access=True
    ):
        response = await self.client.post(
            self.prefix + "/members",
            json={
                "email": email,
                "name": "Member",
                "password": "member-password-123",
                "role": role,
                "all_stores": False,
                "store_ids": scope if scope is not None else [self.stores[0]["id"]],
                "data_access": data_access,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        member = response.json()
        client = self.new_client()
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "member-password-123"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client, member

    async def question(self, client=None, scope=None, **overrides):
        async with self.database.sessions.begin() as db:
            await db.execute(
                update(WorkerHeartbeat)
                .where(WorkerHeartbeat.id == "test-worker")
                .values(updated_at=utcnow())
            )
        client = client or self.client
        conversation = (await client.post(self.prefix + "/conversations", json={})).json()
        body = {
            "question": "Revenue by store",
            "source_id": self.source_id,
            "store_ids": scope if scope is not None else [self.stores[0]["id"]],
            "version": 0,
            "idempotency_key": "request-" + conversation["id"],
        }
        body.update(overrides)
        response = await client.post(
            self.prefix + "/conversations/" + conversation["id"] + "/messages", json=body
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json(), conversation, body

    async def finish(self):
        worker = Worker(self.database, self.settings, ResultEngine())
        self.assertTrue(await worker.run_once())

    async def metric(self):
        response = await self.client.post(
            self.prefix + "/metrics",
            json={
                "key": "revenue",
                "name": "Revenue",
                "description": "Revenue by business day",
                "source_id": self.source_id,
                "table_schema": "reporting",
                "table_name": "sales",
                "value_column": "amount",
                "date_column": "day",
                "store_column": "store_code",
                "aggregation": "sum",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_measurements_preserve_fact_plan_definition_and_recheck_access(self):
        metric = await self.metric()
        manager, _ = await self.member()
        plan = {
            "store_id": self.stores[0]["id"],
            "metric_id": metric["id"],
            "period": "2026-09-01",
            "amount": "100",
        }
        await self.client.post(self.prefix + "/plans", json=plan)
        initial = QueryResult("initial SQL", [], [["A", "100", 1, 1, "80", 1, 1]])
        repeat = QueryResult("repeat SQL", [], [["A", "120", 1, 1, "100", 1, 1]])
        with patch(
            "backend.app.analytics.service.ScopedExecutor.execute",
            new=AsyncMock(side_effect=[initial, repeat]),
        ) as execute:
            response = await self.client.post(
                self.prefix + "/cases",
                json={
                    "title": "September result",
                    "store_ids": [self.stores[0]["id"]],
                    "measurement": {
                        "metric_id": metric["id"],
                        "date_from": "2026-09-01",
                        "date_to": "2026-09-30",
                    },
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            case_id = response.json()["id"]
            await self.client.post(
                self.prefix + "/cases/" + case_id + "/close",
                json={"conclusion": "Check after the decision"},
            )
            await self.client.post(self.prefix + "/plans", json={**plan, "amount": "200"})
            body = {
                "date_from": "2026-10-01",
                "date_to": "2026-10-31",
                "idempotency_key": "repeat-measurement",
            }
            response = await self.client.post(
                self.prefix + "/cases/" + case_id + "/measurements", json=body
            )
            self.assertEqual(response.status_code, 201, response.text)
            repeated_id = response.json()["id"]
            duplicate = await self.client.post(
                self.prefix + "/cases/" + case_id + "/measurements", json=body
            )
            self.assertEqual(duplicate.json()["id"], repeated_id)
            self.assertEqual(execute.await_count, 2)
        detail = (await manager.get(self.prefix + "/cases/" + case_id)).json()
        self.assertEqual(len(detail["measurements"]), 2)
        self.assertEqual(detail["status"], "closed")
        self.assertEqual(detail["conclusion"], "Check after the decision")
        self.assertEqual(detail["measurements"][1]["change_from_initial"]["delta"], "20")
        self.assertEqual(
            detail["measurements"][1]["change_from_initial"]["change_percent"], "20.00"
        )
        self.assertEqual(detail["measurements"][0]["overview"]["totals"]["actual"], "100")
        self.assertEqual(detail["measurements"][0]["overview"]["totals"]["plan"], "100.0000")
        self.assertEqual(
            detail["measurements"][0]["overview"]["stores"][0]["plan_versions"][0]["version"], 1
        )
        self.assertEqual(detail["measurements"][1]["overview"]["totals"]["actual"], "120")
        async with self.database.sessions.begin() as db:
            source = await db.get(Source, self.source_id)
            source.catalog_version += 1
        self.assertEqual((await manager.get(self.prefix + "/cases/" + case_id)).status_code, 200)
        async with self.database.sessions.begin() as db:
            source = await db.get(Source, self.source_id)
            source.policy_revision += 1
        self.assertEqual((await manager.get(self.prefix + "/cases/" + case_id)).status_code, 403)

    async def test_report_refresh_history_is_shared_without_private_conversation(self):
        run, _, _ = await self.question()
        await self.finish()
        report = (
            await self.client.post(
                self.prefix + "/reports", json={"title": "Shared report", "run_id": run["id"]}
            )
        ).json()
        manager, _ = await self.member()
        response = await manager.post(
            self.prefix + "/reports/" + report["id"] + "/refresh",
            json={"idempotency_key": "manager-refresh"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        refresh = response.json()
        await self.finish()
        detail = (await self.client.get(self.prefix + "/reports/" + report["id"])).json()
        self.assertEqual([item["id"] for item in detail["refreshes"]], [refresh["id"]])
        self.assertEqual(detail["refreshes"][0]["status"], "succeeded")
        self.assertEqual(
            (await self.client.get(self.prefix + "/runs/" + refresh["id"])).status_code, 200
        )
        self.assertEqual(
            (
                await self.client.post(self.prefix + "/runs/" + refresh["id"] + "/cancel")
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.get(self.prefix + "/conversations/" + refresh["conversation_id"])
            ).status_code,
            404,
        )
        case = await manager.post(
            self.prefix + "/cases",
            json={
                "title": "Check report",
                "store_ids": [self.stores[0]["id"]],
                "report_id": report["id"],
                "run_id": run["id"],
            },
        )
        self.assertEqual(case.status_code, 201, case.text)
        case = await self.client.post(
            self.prefix + "/cases",
            json={
                "title": "Check refreshed report",
                "store_ids": [self.stores[0]["id"]],
                "report_id": report["id"],
                "run_id": refresh["id"],
            },
        )
        self.assertEqual(case.status_code, 201, case.text)

    async def test_queue_quota_and_restored_backup_never_resume_old_jobs_or_sessions(self):
        self.app.state.settings = replace(self.settings, max_queued_jobs_per_user=1)
        run, _, _ = await self.question()
        conversation = (await self.client.post(self.prefix + "/conversations", json={})).json()
        response = await self.client.post(
            self.prefix + "/conversations/" + conversation["id"] + "/messages",
            json={
                "question": "Another question",
                "source_id": self.source_id,
                "store_ids": [self.stores[0]["id"]],
                "version": 0,
                "idempotency_key": "quota-second-question",
            },
        )
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["error"]["code"], "user_queue_full")
        async with self.database.sessions.begin() as db:
            orphan = QueryRun(
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                conversation_id=conversation["id"],
                source_id=self.source_id,
                question="Interrupted orphan",
                store_ids=[self.stores[0]["id"]],
                context={},
                idempotency_key="orphan-restoration",
                request_hash="0" * 64,
                membership_revision=1,
                conversation_version=0,
                status="running",
            )
            db.add(orphan)
            await db.flush()
            orphan_id = orphan.id
        from backend.app.bootstrap import restore_state

        restored = await restore_state(self.database)
        self.assertEqual(restored["interrupted_jobs"], 1)
        self.assertEqual(restored["interrupted_runs"], 2)
        self.assertEqual((await self.client.get("/api/v1/auth/session")).status_code, 401)
        async with self.database.sessions() as db:
            record = await db.get(QueryRun, run["id"])
            self.assertEqual(record.status, "failed")
            self.assertEqual(record.error["code"], "backup_restored")
            self.assertIsNone(await db.scalar(select(WorkerHeartbeat.id)))
            self.assertEqual((await db.get(QueryRun, orphan_id)).status, "failed")

    async def test_source_profiles_hide_metrics_history_and_running_results_without_owner_bypass(
        self,
    ):
        metric = await self.metric()
        run, _, _ = await self.question()
        await self.finish()
        report = (
            await self.client.post(
                self.prefix + "/reports", json={"title": "Sensitive report", "run_id": run["id"]}
            )
        ).json()
        manager, member = await self.member()
        response = await self.client.patch(
            self.prefix + "/sources/" + self.source_id, json={"reader_ids": [member["user_id"]]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual((await self.client.get(self.prefix + "/metrics")).json(), [])
        self.assertFalse((await self.client.get(self.prefix + "/sources")).json()[0]["can_read"])
        self.assertEqual(
            (await self.client.get(self.prefix + "/reports/" + report["id"])).status_code, 403
        )
        self.assertEqual(
            (
                await self.client.get(self.prefix + "/overview", params={"metric_id": metric["id"]})
            ).status_code,
            403,
        )
        self.assertEqual(len((await manager.get(self.prefix + "/metrics")).json()), 1)
        queued, _, _ = await self.question(client=manager)
        response = await self.client.patch(
            self.prefix + "/sources/" + self.source_id, json={"reader_ids": []}
        )
        self.assertEqual(response.status_code, 200, response.text)
        await self.finish()
        self.assertEqual((await manager.get(self.prefix + "/sources")).json(), [])
        self.assertEqual(
            (await manager.get(self.prefix + "/runs/" + queued["id"])).status_code, 403
        )
        async with self.database.sessions() as db:
            stopped = await db.get(QueryRun, queued["id"])
            self.assertEqual(stopped.status, "rejected")
            self.assertIsNone(stopped.result)
        response = await self.client.patch(
            self.prefix + "/sources/" + self.source_id, json={"reader_ids": None}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue((await self.client.get(self.prefix + "/sources")).json()[0]["can_read"])

    async def test_continuation_inherits_or_explicitly_clears_metric_and_period(self):
        response = await self.client.post(
            self.prefix + "/metrics",
            json={
                "key": "revenue",
                "name": "Revenue",
                "source_id": self.source_id,
                "description": "Sum of amount by business day and store",
                "table_schema": "reporting",
                "table_name": "sales",
                "date_column": "day",
                "store_column": "store_code",
                "value_column": "amount",
                "aggregation": "sum",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        metric_id = response.json()["id"]
        original, conversation, _ = await self.question(
            date_from="2026-09-01", date_to="2026-09-30", metric_id=metric_id
        )
        await self.finish()
        url = self.prefix + "/conversations/" + conversation["id"] + "/messages"
        body = {
            "question": "Now group by store",
            "source_id": self.source_id,
            "store_ids": original["store_ids"],
            "base_run_id": original["id"],
            "version": 1,
            "idempotency_key": "continuation-inherit",
        }
        inherited = await self.client.post(url, json=body)
        self.assertEqual(inherited.status_code, 202, inherited.text)
        self.assertEqual(inherited.json()["context"]["date_from"], "2026-09-01")
        self.assertEqual(inherited.json()["context"]["date_to"], "2026-09-30")
        self.assertEqual(inherited.json()["context"]["metric_id"], metric_id)
        self.assertEqual(inherited.json()["context"]["metric_version"], 1)
        await self.finish()
        body.update(version=2, idempotency_key="continuation-clear", date_from=None)
        self.assertEqual((await self.client.post(url, json=body)).status_code, 422)
        body.update(date_to=None, metric_id=None)
        cleared = await self.client.post(url, json=body)
        self.assertEqual(cleared.status_code, 202, cleared.text)
        self.assertIsNone(cleared.json()["context"]["date_from"])
        self.assertIsNone(cleared.json()["context"]["date_to"])
        self.assertIsNone(cleared.json()["context"]["metric_id"])
        self.assertNotIn("metric_version", cleared.json()["context"])

    async def test_bootstrap_session_csrf_logout_and_headers(self):
        self.assertFalse(
            (await self.client.get("/api/v1/auth/status")).json()["bootstrap_required"]
        )
        response = await self.client.post(
            "/api/v1/auth/bootstrap",
            json={
                "email": "second@example.org",
                "password": "second-password-123",
                "name": "X",
                "workspace_name": "X",
                "bootstrap_token": "setup-token",
            },
        )
        self.assertEqual(response.status_code, 409)
        csrf = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(
            (await self.client.post(self.prefix + "/conversations", json={})).status_code, 403
        )
        self.client.headers["X-CSRF-Token"] = csrf
        self.assertEqual((await self.client.get("/ready")).status_code, 200)
        self.assertEqual(
            (await self.client.get("/api/v1/auth/session")).headers["Cache-Control"], "no-store"
        )
        self.assertEqual((await self.client.post("/api/v1/auth/logout")).status_code, 200)
        self.assertEqual((await self.client.get("/api/v1/auth/session")).status_code, 401)

    async def test_cross_workspace_and_scoped_sources(self):
        manager, member = await self.member()
        self.assertEqual(
            [store["code"] for store in (await manager.get(self.prefix + "/stores")).json()], ["A"]
        )
        result = await manager.post(
            self.prefix + "/plans",
            json={
                "store_id": self.stores[1]["id"],
                "metric_id": "unknown",
                "period": "2026-09-01",
                "amount": "10",
            },
        )
        self.assertEqual(result.status_code, 403)
        second = (await self.client.post("/api/v1/workspaces", json={"name": "Other"})).json()
        self.assertEqual(
            (await manager.get("/api/v1/workspaces/" + second["id"] + "/stores")).status_code, 404
        )
        source = (await manager.get(self.prefix + "/sources")).json()[0]
        self.assertNotIn("username", source)
        self.assertNotIn("password", source)
        self.assertEqual(
            (
                await manager.get(self.prefix + "/sources/" + self.source_id + "/catalog")
            ).status_code,
            403,
        )
        analyst, _ = await self.member("analyst@example.org", role="analyst")
        columns = (
            await analyst.get(self.prefix + "/sources/" + self.source_id + "/catalog")
        ).json()[0]["columns"]
        self.assertNotIn("secret", [column["name"] for column in columns])

    async def test_idempotency_conversation_version_and_cancel(self):
        run, conversation, body = await self.question()
        response = await self.client.post(
            self.prefix + "/conversations/" + conversation["id"] + "/messages", json=body
        )
        self.assertEqual(response.json()["id"], run["id"])
        response = await self.client.post(
            self.prefix + "/conversations/" + conversation["id"] + "/messages",
            json={**body, "question": "Other"},
        )
        self.assertEqual(response.status_code, 409)
        response = await self.client.post(self.prefix + "/runs/" + run["id"] + "/cancel")
        self.assertEqual(response.json()["status"], "cancelled")
        response = await self.client.post(
            self.prefix + "/conversations/" + conversation["id"] + "/messages",
            json={**body, "idempotency_key": "different-key-123", "version": 0},
        )
        self.assertEqual(response.status_code, 409)
        response = await self.client.get(self.prefix + "/runs/" + run["id"] + "/events")
        self.assertIn('"status": "cancelled"', response.text)

    async def test_worker_report_and_snapshot_survives_catalog_refresh(self):
        run, _, _ = await self.question()
        await self.finish()
        result = (await self.client.get(self.prefix + "/runs/" + run["id"])).json()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["columns"], [{"name": "total", "type": "numeric"}])
        report = (
            await self.client.post(
                self.prefix + "/reports", json={"title": "Revenue", "run_id": run["id"]}
            )
        ).json()
        async with self.database.sessions.begin() as db:
            source = await db.get(Source, self.source_id)
            source.catalog_version += 1
        self.assertEqual(
            (await self.client.get(self.prefix + "/reports/" + report["id"])).status_code, 200
        )
        async with self.database.sessions.begin() as db:
            source = await db.get(Source, self.source_id)
            source.policy_revision += 1
        self.assertEqual(
            (await self.client.get(self.prefix + "/reports/" + report["id"])).status_code, 403
        )

    async def test_private_chat_and_report_sharing_only_matching_scope(self):
        manager, _ = await self.member()
        run, conversation, _ = await self.question(scope=[store["id"] for store in self.stores])
        await self.finish()
        report = (
            await self.client.post(
                self.prefix + "/reports", json={"title": "Network", "run_id": run["id"]}
            )
        ).json()
        self.assertEqual(
            (await manager.get(self.prefix + "/conversations/" + conversation["id"])).status_code,
            404,
        )
        self.assertEqual(
            (await manager.get(self.prefix + "/reports/" + report["id"])).status_code, 403
        )
        self.assertEqual((await manager.get(self.prefix + "/reports")).json(), [])
        response = await self.client.post(
            self.prefix + "/cases",
            json={"title": "Narrow leak", "run_id": run["id"], "store_ids": [self.stores[0]["id"]]},
        )
        self.assertEqual(response.status_code, 409)

    async def test_assigned_question_answer_notification_and_close(self):
        manager, member = await self.member()
        case = (
            await self.client.post(
                self.prefix + "/cases",
                json={
                    "title": "Revenue change",
                    "store_ids": [self.stores[0]["id"]],
                    "assignee_id": member["user_id"],
                },
            )
        ).json()
        question = (
            await self.client.post(
                self.prefix + "/cases/" + case["id"] + "/questions",
                json={"body": "Were there closed days?", "assignee_id": member["user_id"]},
            )
        ).json()
        path = self.prefix + "/cases/" + case["id"] + "/questions/" + question["id"] + "/answer"
        self.assertEqual(
            (await self.client.post(path, json={"answer": "Wrong author"})).status_code, 403
        )
        self.assertEqual(
            (await manager.post(path, json={"answer": "Two days"})).json()["status"], "answered"
        )
        self.assertTrue((await manager.get(self.prefix + "/notifications")).json())
        self.assertEqual(
            (
                await self.client.post(
                    self.prefix + "/cases/" + case["id"] + "/close",
                    json={"conclusion": "Check next month"},
                )
            ).json()["status"],
            "closed",
        )
        self.assertEqual(
            (
                await manager.post(
                    self.prefix + "/cases/" + case["id"] + "/comments", json={"body": "Late"}
                )
            ).status_code,
            409,
        )

    async def test_access_revocation_before_worker_blocks_publish(self):
        manager, member = await self.member()
        run, _, _ = await self.question(manager)
        await self.client.patch(
            self.prefix + "/members/" + member["id"], json={"data_access": False}
        )
        await self.finish()
        async with self.database.sessions() as db:
            record = await db.get(QueryRun, run["id"])
            self.assertEqual(record.status, "rejected")
            self.assertIsNone(record.result)
        self.assertEqual((await manager.get(self.prefix + "/runs/" + run["id"])).status_code, 403)

    async def test_expired_lease_cannot_renew_or_publish(self):
        run, _, _ = await self.question()
        async with self.database.sessions.begin() as db:
            job_id, token, _ = await claim_job(db, 6)
        async with self.database.sessions.begin() as db:
            job = await db.get(Job, job_id)
            job.lease_until = utcnow() - timedelta(seconds=1)
        async with self.database.sessions.begin() as db:
            self.assertIsNone(await renew_lease(db, job_id, token, 6))
            self.assertFalse(
                await finish_job(
                    db, job_id, token, {"status": "succeeded", "result": {"rows": [[999]]}}
                )
            )
        async with self.database.sessions.begin() as db:
            self.assertIsNone(await claim_job(db, 6))
            self.assertEqual((await db.get(QueryRun, run["id"])).status, "failed")

    async def test_invitation_single_use_password_change_and_session_revocation(self):
        response = await self.client.post(
            self.prefix + "/invitations",
            json={
                "email": "invited@example.org",
                "role": "store_manager",
                "store_ids": [self.stores[0]["id"]],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        token = response.json()["token"]
        invited = self.new_client()
        response = await invited.post(
            "/api/v1/auth/invitations/accept",
            json={"token": token, "name": "Invited", "password": "invited-password-123"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        invited.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        self.assertEqual(
            (
                await invited.post("/api/v1/auth/invitations/accept", json={"token": token})
            ).status_code,
            400,
        )
        second = self.new_client()
        response = await second.post(
            "/api/v1/auth/login",
            json={"email": "invited@example.org", "password": "invited-password-123"},
        )
        self.assertEqual(response.status_code, 200)
        response = await invited.post(
            "/api/v1/auth/password",
            json={
                "current_password": "invited-password-123",
                "new_password": "changed-password-123",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await second.get("/api/v1/auth/session")).status_code, 401)
        self.assertEqual((await invited.get("/api/v1/auth/session")).status_code, 200)

    async def test_existing_account_can_join_second_workspace_by_invitation(self):
        manager, member = await self.member()
        second = (await self.client.post("/api/v1/workspaces", json={"name": "Second"})).json()
        invite = (
            await self.client.post(
                "/api/v1/workspaces/" + second["id"] + "/invitations",
                json={"email": member["email"], "role": "analyst", "all_stores": True},
            )
        ).json()
        response = await manager.post(
            "/api/v1/auth/invitations/accept", json={"token": invite["token"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["workspaces"]), 2)

    async def test_metrics_plan_version_and_empty_overview(self):
        response = await self.client.get(self.prefix + "/overview")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["totals"]["actual"])
        metric = {
            "key": "revenue",
            "name": "Revenue",
            "description": "Revenue after returns by business date",
            "unit": "RUB",
            "source_id": self.source_id,
            "table_schema": "reporting",
            "table_name": "sales",
            "value_column": "amount",
            "date_column": "day",
            "store_column": "store_code",
            "aggregation": "sum",
        }
        response = await self.client.post(self.prefix + "/metrics", json=metric)
        self.assertEqual(response.status_code, 201, response.text)
        metric_id = response.json()["id"]
        body = {
            "store_id": self.stores[0]["id"],
            "metric_id": metric_id,
            "period": "2026-09-01",
            "amount": "1000.50",
        }
        self.assertEqual(
            (await self.client.post(self.prefix + "/plans", json=body)).json()["version"], 1
        )
        self.assertEqual(
            (
                await self.client.post(self.prefix + "/plans", json={**body, "amount": "1200.50"})
            ).json()["version"],
            2,
        )
        self.assertEqual(len((await self.client.get(self.prefix + "/plans")).json()), 1)

    async def test_request_body_limits_and_origin(self):
        response = await self.client.post(
            "/api/v1/auth/login",
            content=b"x" * (2097152 + 1),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

        async def chunks():
            yield b" " * 1048576
            yield b" " * 1048577

        response = await self.client.post(
            "/api/v1/auth/login", content=chunks(), headers={"content-type": "application/json"}
        )
        self.assertEqual(response.status_code, 413)
        response = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.org", "password": "a-good-password-123"},
            headers={"Origin": "https://attacker.test"},
        )
        self.assertEqual(response.status_code, 403)

    async def test_calendar_comparison_uses_complete_months_and_preserves_custom_ranges(self):
        response = await self.client.post(
            self.prefix + "/metrics",
            json={
                "key": "revenue",
                "name": "Revenue",
                "description": "Revenue by business date",
                "source_id": self.source_id,
                "table_schema": "reporting",
                "table_name": "sales",
                "value_column": "amount",
                "date_column": "day",
                "store_column": "store_code",
                "aggregation": "sum",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        metric_id = response.json()["id"]
        source = sqlite3.connect(":memory:")
        source.execute("ATTACH DATABASE ':memory:' AS reporting")
        source.execute("CREATE TABLE reporting.sales (store_code TEXT, day TEXT, amount INTEGER)")
        source.executemany(
            "INSERT INTO reporting.sales VALUES ('A', ?, ?)",
            [
                ("2026-07-01", 1),
                ("2026-08-31", 999),
                ("2026-09-01", 20),
                ("2026-09-30", 30),
                ("2026-10-01", 100),
                ("2026-10-10", 5),
                ("2026-10-20", 7),
                ("2026-10-31", 200),
                ("2025-11-30", 1000),
                ("2025-12-01", 3),
                ("2025-12-31", 7),
                ("2026-01-01", 11),
                ("2026-01-31", 19),
                ("2024-01-31", 900),
                ("2024-02-01", 4),
                ("2024-02-29", 6),
                ("2024-03-01", 10),
                ("2024-03-31", 20),
                ("2025-01-01", 11),
                ("2025-01-31", 19),
                ("2025-02-01", 10),
                ("2025-02-28", 40),
            ],
        )

        async def execute(sql, context):
            cursor = source.execute(sql)
            return QueryResult(
                sql,
                [item[0] for item in cursor.description],
                [list(row) for row in cursor.fetchall()],
            )

        cases = [
            ("2026-10-01", "2026-10-31", "2026-09-01", "2026-09-30", "312", "50"),
            ("2026-01-01", "2026-01-31", "2025-12-01", "2025-12-31", "30", "10"),
            ("2024-03-01", "2024-03-31", "2024-02-01", "2024-02-29", "30", "10"),
            ("2025-02-01", "2025-02-28", "2025-01-01", "2025-01-31", "50", "30"),
            ("2026-09-01", "2026-10-31", "2026-07-01", "2026-08-31", "362", "1000"),
            ("2026-10-10", "2026-10-20", "2026-09-29", "2026-10-09", "12", "130"),
        ]
        try:
            with patch(
                "backend.app.analytics.service.ScopedExecutor.execute",
                new=AsyncMock(side_effect=execute),
            ):
                for start, end, previous_start, previous_end, actual, previous in cases:
                    with self.subTest(start=start, end=end):
                        response = await self.client.get(
                            self.prefix + "/overview",
                            params={"date_from": start, "date_to": end, "metric_id": metric_id},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        overview = response.json()
                        self.assertEqual(overview["totals"]["actual"], actual)
                        self.assertEqual(overview["comparison"]["previous"], previous)
                        self.assertEqual(overview["comparison"]["date_from"], previous_start)
                        self.assertEqual(overview["comparison"]["date_to"], previous_end)
        finally:
            source.close()

    async def test_overview_comparison_uses_same_stores_and_missing_data_is_not_zero(self):
        body = {
            "key": "revenue",
            "name": "Revenue",
            "description": "Net revenue by business date",
            "source_id": self.source_id,
            "table_schema": "reporting",
            "table_name": "sales",
            "value_column": "amount",
            "date_column": "day",
            "store_column": "store_code",
            "aggregation": "sum",
        }
        metric = (await self.client.post(self.prefix + "/metrics", json=body)).json()
        for store in self.stores:
            response = await self.client.post(
                self.prefix + "/plans",
                json={
                    "store_id": store["id"],
                    "metric_id": metric["id"],
                    "period": "2026-09-01",
                    "amount": "100",
                },
            )
            self.assertEqual(response.status_code, 201)
        calculation = QueryResult(
            "comparison", [], [["A", "100", 1, 1, "80", 1, 1], ["B", None, 0, 0, "200", 1, 1]]
        )
        with patch(
            "backend.app.analytics.service.ScopedExecutor.execute",
            new=AsyncMock(return_value=calculation),
        ) as execute:
            response = await self.client.get(
                self.prefix + "/overview",
                params={
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-30",
                    "metric_id": metric["id"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        overview = response.json()
        self.assertEqual(overview["totals"]["actual"], "100")
        self.assertEqual(overview["totals"]["attainment"], "100.00")
        self.assertEqual(overview["comparison"]["previous"], "80")
        self.assertEqual(overview["comparison"]["change_percent"], "25.00")
        self.assertEqual(overview["comparison"]["comparable_count"], 1)
        self.assertEqual(overview["coverage"], {"available": 1, "total": 2})
        missing = next(store for store in overview["stores"] if store["name"] == "B")
        self.assertIsNone(missing["actual"])
        self.assertEqual(missing["status"], "no_data")
        self.assertIn("2026-10-01", execute.call_args_list[0].args[0])

    async def test_average_is_weighted_and_monthly_average_plans_are_not_summed(self):
        body = {
            "key": "average",
            "name": "Average",
            "description": "Average value per transaction",
            "source_id": self.source_id,
            "table_schema": "reporting",
            "table_name": "sales",
            "value_column": "amount",
            "date_column": "day",
            "store_column": "store_code",
            "aggregation": "avg",
        }
        metric = (await self.client.post(self.prefix + "/metrics", json=body)).json()
        for period in ("2026-08-01", "2026-09-01"):
            for store in self.stores:
                await self.client.post(
                    self.prefix + "/plans",
                    json={
                        "store_id": store["id"],
                        "metric_id": metric["id"],
                        "period": period,
                        "amount": "20",
                    },
                )
        calculation = QueryResult(
            "comparison", [], [["A", "10", 1, 1, "10", 2, 2], ["B", "20", 9, 9, "10", 2, 2]]
        )
        with patch(
            "backend.app.analytics.service.ScopedExecutor.execute",
            new=AsyncMock(return_value=calculation),
        ):
            response = await self.client.get(
                self.prefix + "/overview",
                params={
                    "date_from": "2026-08-01",
                    "date_to": "2026-09-30",
                    "metric_id": metric["id"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        overview = response.json()
        self.assertEqual(overview["totals"]["actual"], "19")
        self.assertIsNone(overview["totals"]["plan"])
        self.assertTrue(all(store["plan"] is None for store in overview["stores"]))

    async def test_running_cancel_wins_over_late_success(self):
        run, _, _ = await self.question()
        started = asyncio.Event()
        finished = asyncio.Event()

        class LateEngine(ResultEngine):
            async def run(self, *args):
                started.set()
                await finished.wait()
                return await super().run(*args)

        worker = Worker(self.database, self.settings, LateEngine())
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), timeout=10)
        response = await self.client.post(self.prefix + "/runs/" + run["id"] + "/cancel")
        self.assertEqual(response.json()["status"], "cancel_requested")
        finished.set()
        await asyncio.wait_for(task, timeout=10)
        response = await self.client.get(self.prefix + "/runs/" + run["id"])
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertIsNone(response.json()["result"])

    async def test_worker_unavailable_does_not_create_a_hanging_job(self):
        conversation = (await self.client.post(self.prefix + "/conversations", json={})).json()
        async with self.database.sessions.begin() as db:
            await db.execute(
                update(WorkerHeartbeat).values(updated_at=utcnow() - timedelta(hours=1))
            )
        response = await self.client.post(
            self.prefix + "/conversations/" + conversation["id"] + "/messages",
            json={
                "question": "Revenue",
                "source_id": self.source_id,
                "store_ids": [self.stores[0]["id"]],
                "version": 0,
                "idempotency_key": "unavailable-worker",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "worker_unavailable")
        self.assertEqual(
            (await self.client.get(self.prefix + "/conversations/" + conversation["id"])).json()[
                "runs"
            ],
            [],
        )

    async def test_simultaneous_duplicate_submission_creates_one_run(self):
        conversation = (await self.client.post(self.prefix + "/conversations", json={})).json()
        body = {
            "question": "Revenue",
            "source_id": self.source_id,
            "store_ids": [self.stores[0]["id"]],
            "version": 0,
            "idempotency_key": "simultaneous-request",
        }
        path = self.prefix + "/conversations/" + conversation["id"] + "/messages"
        first, second = await asyncio.gather(
            self.client.post(path, json=body), self.client.post(path, json=body)
        )
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        response = await self.client.get(self.prefix + "/conversations/" + conversation["id"])
        self.assertEqual(len(response.json()["runs"]), 1)

    async def test_session_is_committed_before_http_response_headers(self):
        body = json.dumps(
            {"email": "owner@example.org", "password": "a-good-password-123"}
        ).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/login",
            "raw_path": b"/api/v1/auth/login",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"test"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 5000),
            "server": ("test", 80),
        }
        received = False
        committed = []

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.start":
                self.assertEqual(message["status"], 200)
                cookies = SimpleCookie()
                for key, value in message["headers"]:
                    if key.lower() == b"set-cookie":
                        cookies.load(value.decode())
                token = cookies["razbor_session"].value
                async with self.database.sessions() as db:
                    session = await db.scalar(
                        select(Session).where(Session.token_hash == token_hash(token))
                    )
                    committed.append(session is not None)

        await asyncio.wait_for(self.app(scope, receive, send), timeout=10)
        self.assertEqual(committed, [True])


if __name__ == "__main__":
    unittest.main()
