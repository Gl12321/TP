from datetime import date
from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from backend.app.access.models import Membership, User, Workspace
from backend.app.analytics.models import Metric, Plan, Report, Store
from backend.app.assistant.models import Conversation, Message, QueryRun
from backend.app.collaboration.models import Case, Notification
from backend.app.infrastructure.database import Base, load_models
from backend.app.sources.models import Source


class WorkspaceConstraintTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "constraints.db"
        root = Path(__file__).resolve().parents[2]
        self.config = Config(str(root / "backend/alembic.ini"))
        self.config.set_main_option("script_location", str(root / "backend/migrations"))
        self.config.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///" + self.path.as_posix())
        command.upgrade(self.config, "0001_workspace")
        self.engine = create_engine("sqlite:///" + self.path.as_posix())

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        load_models()
        self.seed()

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def insert(self, model, **values):
        with self.engine.begin() as connection:
            connection.execute(model.__table__.insert().values(**values))

    def seed(self):
        for index in (1, 2, 3):
            self.insert(
                User,
                id=f"user{index}",
                email=f"user{index}@example.org",
                name=f"User{index}",
                password_hash="test",
            )
        for index in (1, 2):
            self.insert(Workspace, id=f"workspace{index}", name=f"Workspace{index}")
            self.insert(
                Membership,
                id=f"member{index}",
                workspace_id=f"workspace{index}",
                user_id=f"user{index}",
                role="director",
                all_stores=True,
            )
            self.insert(
                Source,
                id=f"source{index}",
                workspace_id=f"workspace{index}",
                name="Source",
                host="localhost",
                database="reporting",
                username="reader",
                encrypted_password="test",
                schemas=["reporting"],
            )
            self.insert(
                Store, id=f"store{index}", workspace_id=f"workspace{index}", name="Store", code="A"
            )
            self.insert(Metric, **self.metric(index))
            self.insert(
                Conversation,
                id=f"conversation{index}",
                workspace_id=f"workspace{index}",
                user_id=f"user{index}",
            )
            self.insert(QueryRun, **self.run_values(index))
            self.insert(
                Case,
                id=f"case{index}",
                workspace_id=f"workspace{index}",
                title="Case",
                store_ids=[f"store{index}"],
                run_id=f"run{index}",
                created_by=f"user{index}",
            )
        self.insert(
            Membership,
            id="member3",
            workspace_id="workspace1",
            user_id="user3",
            role="analyst",
            all_stores=True,
        )
        self.insert(Conversation, id="conversation3", workspace_id="workspace1", user_id="user3")
        self.insert(Conversation, id="conversation4", workspace_id="workspace1", user_id="user1")
        self.insert(
            QueryRun,
            **self.run_values(
                1, id="run3", conversation_id="conversation4", idempotency_key="run3"
            ),
        )
        self.insert(Plan, **self.plan())
        self.insert(
            Report,
            id="report1",
            workspace_id="workspace1",
            title="Report",
            run_id="run1",
            created_by="user1",
        )
        self.insert(
            Message,
            id="message1",
            conversation_id="conversation1",
            run_id="run1",
            role="user",
            content="Question",
        )

    def metric(self, index=1, **overrides):
        return {
            "id": f"metric{index}",
            "workspace_id": f"workspace{index}",
            "key": "revenue",
            "name": "Revenue",
            "description": "Revenue sum",
            "source_id": f"source{index}",
            "table_schema": "reporting",
            "table_name": "sales",
            "value_column": "amount",
            "date_column": "day",
            "store_column": "store",
            "aggregation": "sum",
            "created_by": f"user{index}",
            **overrides,
        }

    def run_values(self, index=1, **overrides):
        return {
            "id": f"run{index}",
            "workspace_id": f"workspace{index}",
            "user_id": f"user{index}",
            "conversation_id": f"conversation{index}",
            "source_id": f"source{index}",
            "question": "Revenue",
            "store_ids": [f"store{index}"],
            "idempotency_key": f"run{index}",
            "request_hash": "hash",
            "membership_revision": 1,
            "conversation_version": 1,
            **overrides,
        }

    def plan(self, **overrides):
        return {
            "id": "plan1",
            "workspace_id": "workspace1",
            "store_id": "store1",
            "metric_id": "metric1",
            "period": date(2026, 9, 1),
            "amount": 100,
            "created_by": "user1",
            **overrides,
        }

    def rejects(self, model, **values):
        with self.assertRaises(IntegrityError):
            self.insert(model, **values)

    def test_real_sqlite_upgrade_rejects_cross_workspace_references(self):
        command.upgrade(self.config, "head")
        self.rejects(Metric, **self.metric(id="forged-metric", key="other", source_id="source2"))
        self.rejects(Metric, **self.metric(id="forged-author", key="other", created_by="user2"))
        self.rejects(Plan, **self.plan(id="forged-plan", store_id="store2"))
        self.rejects(Plan, **self.plan(id="forged-metric-plan", metric_id="metric2"))
        self.rejects(
            Report, workspace_id="workspace1", title="Forged", run_id="run2", created_by="user1"
        )
        self.rejects(
            Case,
            workspace_id="workspace1",
            title="Forged",
            store_ids=["store1"],
            run_id="run2",
            created_by="user1",
        )
        self.rejects(
            Case,
            workspace_id="workspace1",
            title="Forged",
            store_ids=["store1"],
            created_by="user1",
            assignee_id="user2",
        )
        self.rejects(
            Notification,
            workspace_id="workspace1",
            user_id="user1",
            case_id="case2",
            kind="test",
            title="Forged",
        )
        self.rejects(
            Notification,
            workspace_id="workspace1",
            user_id="user2",
            case_id="case1",
            kind="test",
            title="Forged",
        )
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])

    def test_run_conversation_owner_and_continuation_are_database_invariants(self):
        command.upgrade(self.config, "head")
        self.rejects(
            QueryRun,
            **self.run_values(id="bad-source", idempotency_key="bad-source", source_id="source2"),
        )
        self.rejects(
            QueryRun,
            **self.run_values(
                id="bad-conversation",
                idempotency_key="bad-conversation",
                conversation_id="conversation2",
            ),
        )
        self.rejects(
            QueryRun,
            **self.run_values(
                id="bad-owner", idempotency_key="bad-owner", conversation_id="conversation3"
            ),
        )
        self.rejects(
            QueryRun,
            **self.run_values(id="bad-base", idempotency_key="bad-base", base_run_id="run2"),
        )
        self.rejects(
            QueryRun,
            **self.run_values(
                id="other-dialogue-base", idempotency_key="other-dialogue-base", base_run_id="run3"
            ),
        )
        self.rejects(
            Message,
            conversation_id="conversation2",
            run_id="run1",
            role="assistant",
            content="Wrong conversation",
        )
        self.rejects(Conversation, workspace_id="workspace1", user_id="user2")
        self.insert(
            QueryRun,
            **self.run_values(
                id="valid-followup", idempotency_key="valid-followup", base_run_id="run1"
            ),
        )

    def test_downgrade_and_reupgrade_preserve_rows_and_match_models(self):
        command.upgrade(self.config, "head")
        command.downgrade(self.config, "0001_workspace")
        command.upgrade(self.config, "head")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(Report.id)).scalars().all(), ["report1"])
            self.assertEqual(connection.execute(select(Message.id)).scalars().all(), ["message1"])
            self.assertEqual(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar(),
                "0004_source_readers",
            )
            self.assertEqual(
                compare_metadata(MigrationContext.configure(connection), Base.metadata), []
            )

    def test_inconsistent_legacy_rows_are_rejected_before_schema_changes(self):
        self.insert(
            Report,
            id="forged-existing",
            workspace_id="workspace1",
            title="Forged",
            run_id="run2",
            created_by="user1",
        )
        with self.assertRaisesRegex(RuntimeError, "fk_report_run_workspace"):
            command.upgrade(self.config, "head")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar(),
                "0001_workspace",
            )
            self.assertNotIn(
                "uq_source_workspace",
                {item["name"] for item in inspect(connection).get_unique_constraints("sources")},
            )
