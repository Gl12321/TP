from fastapi import APIRouter, Request
from sqlalchemy import select, update

from backend.app.access.dependencies import Db, WorkspaceAccess
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import SecretStore
from backend.app.sources.models import Source
from backend.app.sources.api.schemas import PolicyUpdate, SourceCreate, SourceUpdate
from backend.app.sources.service import (
    authorized_catalog,
    get_source,
    inspect_source,
    public_source,
    can_read_source,
    require_source_access,
    validate_readers,
)


router = APIRouter(prefix="/workspaces/{workspace_id}")


@router.get("/sources")
async def sources(access: WorkspaceAccess, db: Db):
    if not ({"sources:manage", "analytics:read"} & access.capabilities):
        raise AppError("forbidden", "Нет доступа к источникам", 403)
    items = (
        await db.scalars(
            select(Source).where(Source.workspace_id == access.workspace_id).order_by(Source.name)
        )
    ).all()
    if "sources:manage" in access.capabilities:
        return [
            {**public_source(item), "can_read": can_read_source(item, access)} for item in items
        ]
    return [
        {
            "id": item.id,
            "name": item.name,
            "enabled": item.enabled,
            "status": item.status,
            "catalog_version": item.catalog_version,
            "permitted_table_count": len(item.policies),
            "last_checked_at": item.last_checked_at,
        }
        for item in items
        if can_read_source(item, access)
    ]


@router.post("/sources", status_code=201)
async def create_source(body: SourceCreate, access: WorkspaceAccess, db: Db, request: Request):
    access.require("sources:manage")
    await validate_readers(db, access.workspace_id, body.reader_ids)
    data = body.model_dump(exclude={"password"})
    source = Source(
        workspace_id=access.workspace_id,
        **data,
        encrypted_password=SecretStore(request.app.state.settings.secret_key).encrypt(
            body.password
        ),
    )
    db.add(source)
    await db.flush()
    return public_source(source)


@router.post("/sources/{source_id}/test")
async def test_source(source_id: str, access: WorkspaceAccess, db: Db, request: Request):
    access.require("sources:manage")
    source = await get_source(db, source_id, access.workspace_id)
    revision = source.policy_revision
    try:
        await inspect_source(
            source, request.app.state.settings, request.app.state.source_connections
        )
    except AppError as error:
        source.status = "error"
        source.error = error.message
    values = {
        key: getattr(source, key) for key in ("catalog", "status", "error", "last_checked_at")
    }
    db.expunge(source)
    values["catalog_version"] = Source.catalog_version + 1
    changed = await db.execute(
        update(Source)
        .where(Source.id == source_id, Source.policy_revision == revision)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise AppError(
            "source_changed",
            "Настройки подключения изменились во время проверки. Повторите проверку",
            409,
        )
    return public_source(await get_source(db, source_id, access.workspace_id))


@router.patch("/sources/{source_id}")
async def update_source(
    source_id: str, body: SourceUpdate, access: WorkspaceAccess, db: Db, request: Request
):
    access.require("sources:manage")
    source = await get_source(db, source_id, access.workspace_id, for_update=True)
    changes = body.model_dump(exclude_none=True)
    if "reader_ids" in body.model_fields_set:
        await validate_readers(db, access.workspace_id, body.reader_ids)
        changes["reader_ids"] = (
            sorted(set(body.reader_ids)) if body.reader_ids is not None else None
        )
    if "password" in changes:
        source.encrypted_password = SecretStore(request.app.state.settings.secret_key).encrypt(
            changes.pop("password")
        )
    for key, value in changes.items():
        setattr(source, key, value)
    if set(body.model_fields_set) - {"name"}:
        source.policy_revision = Source.policy_revision + 1
        source.catalog_version = Source.catalog_version + 1
        if set(body.model_fields_set) - {"name", "reader_ids"}:
            source.status = "unchecked"
            source.error = None
    await db.flush()
    await db.refresh(source)
    return public_source(source)


@router.get("/sources/{source_id}/catalog")
async def catalog(source_id: str, access: WorkspaceAccess, db: Db):
    if not {"sources:manage", "metrics:write"} & access.capabilities:
        raise AppError("forbidden", "Нет доступа к каталогу", 403)
    source = await get_source(db, source_id, access.workspace_id)
    policies = {(policy["schema"], policy["name"]): policy for policy in source.policies}
    if "sources:manage" not in access.capabilities:
        require_source_access(source, access)
        return [
            {
                "schema": table.ref.schema,
                "name": table.ref.name,
                "columns": [
                    {"name": column.name, "data_type": column.data_type} for column in table.columns
                ],
                "policy": policies[(table.ref.schema, table.ref.name)],
            }
            for table in authorized_catalog(source)
        ]
    return [
        {**table, "policy": policies.get((table["schema"], table["name"]))}
        for table in source.catalog
    ]


@router.put("/sources/{source_id}/policies")
async def policies(source_id: str, body: PolicyUpdate, access: WorkspaceAccess, db: Db):
    access.require("sources:manage")
    source = await get_source(db, source_id, access.workspace_id, for_update=True)
    catalog = {
        (table["schema"], table["name"]): {column["name"] for column in table["columns"]}
        for table in source.catalog
    }
    seen = set()
    for policy in body.tables:
        key = (policy.schema_name, policy.name)
        if key in seen or key not in catalog or not set(policy.columns).issubset(catalog[key]):
            raise AppError("invalid_policy", "Проверьте таблицы и разрешённые поля")
        seen.add(key)
    source.policies = [table.model_dump(by_alias=True) for table in body.tables]
    source.catalog_version = Source.catalog_version + 1
    source.policy_revision = Source.policy_revision + 1
    await db.flush()
    await db.refresh(source)
    return public_source(source)
