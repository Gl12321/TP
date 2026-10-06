import hashlib
import json

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.database import Base, Identity, new_id
from backend.app.infrastructure.errors import AppError


class CreationRequest(Identity, Base):
    __tablename__ = "creation_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "created_by", "key", name="uq_creation_request"),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_creation_request_member",
        ),
        CheckConstraint(
            "operation IN ('report', 'case', 'source_issue')", name="ck_creation_operation"
        ),
    )
    workspace_id: Mapped[str] = mapped_column(String(36))
    created_by: Mapped[str] = mapped_column(String(36))
    key: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(24))
    fingerprint: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(36))


async def reserve_creation(db, access, operation, body):
    if body.idempotency_key is None:
        return new_id(), False
    payload = body.model_dump(mode="json", exclude={"idempotency_key"})
    if "store_ids" in payload:
        payload["store_ids"] = sorted(set(payload["store_ids"]))
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else postgres_insert
    target_id = await db.scalar(
        insert(CreationRequest)
        .values(
            workspace_id=access.workspace_id,
            created_by=access.user_id,
            key=body.idempotency_key,
            operation=operation,
            fingerprint=fingerprint,
            target_id=new_id(),
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "created_by", "key"])
        .returning(CreationRequest.target_id)
    )
    if target_id is not None:
        return target_id, False
    previous = await db.scalar(
        select(CreationRequest).where(
            CreationRequest.workspace_id == access.workspace_id,
            CreationRequest.created_by == access.user_id,
            CreationRequest.key == body.idempotency_key,
        )
    )
    if previous.operation != operation or previous.fingerprint != fingerprint:
        raise AppError(
            "idempotency_conflict",
            "Этот запрос уже сохранён с другими условиями. Создайте новое сохранение",
            409,
        )
    return previous.target_id, True
