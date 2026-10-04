from datetime import date
import re
from typing import Literal

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from backend.app.access.dependencies import Db, WorkspaceAccess
from backend.app.analytics.models import Metric, Plan, Report, Store
from backend.app.analytics.schemas import (
    MetricCreate,
    PlanCreate,
    Refresh,
    ReportCreate,
    StoreCreate,
    StoreUpdate,
)
from backend.app.analytics.service import (
    latest,
    metric_payload,
    overview as build_overview,
    plan_payload,
    record_payload,
    store_analytics,
)
from backend.app.assistant.models import Conversation, QueryRun
from backend.app.assistant.schemas import QuestionCreate
from backend.app.assistant.service import create_run, get_run, run_payload
from backend.app.infrastructure.errors import AppError
from backend.app.sources.service import (
    authorized_catalog,
    ensure_ready,
    get_source,
    can_read_source,
    require_source_access,
)
from backend.app.sources.models import Source


router = APIRouter(prefix="/workspaces/{workspace_id}")


@router.get("/stores")
async def stores(access: WorkspaceAccess, db: Db):
    if not {"analytics:read", "members:manage", "sources:manage"} & access.capabilities:
        raise AppError("forbidden", "Нет доступа к точкам", 403)
    statement = select(Store).where(Store.workspace_id == access.workspace_id)
    if not access.all_stores and "members:manage" not in access.capabilities:
        statement = statement.where(Store.id.in_(access.store_ids))
    return [
        record_payload(store, "id name code city owner_name active")
        for store in (await db.scalars(statement.order_by(Store.name))).all()
    ]


@router.post("/stores", status_code=201)
async def create_store(body: StoreCreate, access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    store = Store(workspace_id=access.workspace_id, **body.model_dump())
    db.add(store)
    await db.flush()
    return record_payload(store, "id name code city owner_name active")


@router.get("/stores/{store_id}/analytics")
async def store_detail(
    store_id: str,
    metric_id: str,
    date_from: date,
    date_to: date,
    access: WorkspaceAccess,
    db: Db,
    request: Request,
    grain: Literal["day", "week"] = "day",
):
    return await store_analytics(
        db,
        access,
        request.app.state.settings,
        store_id,
        metric_id,
        date_from,
        date_to,
        grain,
        request.app.state.source_connections,
    )


@router.patch("/stores/{store_id}")
async def update_store(store_id: str, body: StoreUpdate, access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    store = await db.get(Store, store_id)
    if store is None or store.workspace_id != access.workspace_id:
        raise AppError("not_found", "Точка не найдена", 404)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(store, field, value)
    await db.flush()
    return record_payload(store, "id name code city owner_name active")


@router.get("/metrics")
async def metrics(access: WorkspaceAccess, db: Db):
    access.require("analytics:read")
    records = (
        await db.scalars(
            select(Metric).where(Metric.workspace_id == access.workspace_id).order_by(Metric.name)
        )
    ).all()
    readable = {
        source.id
        for source in (
            await db.scalars(select(Source).where(Source.workspace_id == access.workspace_id))
        ).all()
        if can_read_source(source, access)
    }
    return [
        metric_payload(metric)
        for metric in latest(records, lambda metric: metric.key)
        if metric.source_id in readable
    ]


@router.post("/metrics", status_code=201)
async def create_metric(body: MetricCreate, access: WorkspaceAccess, db: Db):
    access.require("metrics:write")
    source = await get_source(db, body.source_id, access.workspace_id)
    require_source_access(source, access)
    ensure_ready(source)
    table = next(
        (
            table
            for table in authorized_catalog(source)
            if table.ref.schema == body.table_schema and table.ref.name == body.table_name
        ),
        None,
    )
    columns = {body.date_column, body.store_column} | (
        {body.value_column} if body.value_column else set()
    )
    if table is None or not columns.issubset(table.column_names):
        raise AppError("invalid_metric", "Поля показателя должны входить в разрешённый каталог")
    column_types = {column.name: column.data_type.lower() for column in table.columns}
    if not re.fullmatch(
        r"date|timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?", column_types[body.date_column]
    ):
        raise AppError(
            "invalid_metric_date", "Выберите поле типа date или timestamp для даты показателя"
        )
    if body.aggregation in {"sum", "avg"} and not re.fullmatch(
        r"smallint|integer|bigint|real|double precision|numeric(?:\(\d+(?:,\d+)?\))?|decimal(?:\(\d+(?:,\d+)?\))?",
        column_types[body.value_column],
    ):
        raise AppError("invalid_metric_value", "Сумма и среднее требуют числового поля")
    policy = next(
        item
        for item in source.policies
        if item["schema"] == body.table_schema and item["name"] == body.table_name
    )
    if policy["store_column"] != body.store_column or policy["shared"]:
        raise AppError(
            "invalid_metric", "Показатель должен использовать настроенный ключ точки таблицы фактов"
        )
    version = (
        await db.scalar(
            select(func.max(Metric.version)).where(
                Metric.workspace_id == access.workspace_id, Metric.key == body.key
            )
        )
        or 0
    ) + 1
    metric = Metric(
        workspace_id=access.workspace_id,
        created_by=access.user_id,
        version=version,
        **body.model_dump(),
    )
    db.add(metric)
    await db.flush()
    return metric_payload(metric)


@router.get("/plans")
async def plans(access: WorkspaceAccess, db: Db):
    access.require("analytics:read")
    statement = select(Plan).where(Plan.workspace_id == access.workspace_id)
    if not access.all_stores:
        statement = statement.where(Plan.store_id.in_(access.store_ids))
    records = (await db.scalars(statement.order_by(Plan.period.desc()))).all()
    readable = {
        source.id
        for source in (
            await db.scalars(select(Source).where(Source.workspace_id == access.workspace_id))
        ).all()
        if can_read_source(source, access)
    }
    metric_ids = set(
        (
            await db.scalars(
                select(Metric.id).where(
                    Metric.workspace_id == access.workspace_id, Metric.source_id.in_(readable)
                )
            )
        ).all()
    )
    records = [record for record in records if record.metric_id in metric_ids]
    return [
        plan_payload(plan)
        for plan in latest(records, lambda item: (item.store_id, item.metric_id, item.period))
    ]


@router.post("/plans", status_code=201)
async def create_plan(body: PlanCreate, access: WorkspaceAccess, db: Db):
    access.require("plans:write")
    access.require_scope([body.store_id])
    store, metric = await db.get(Store, body.store_id), await db.get(Metric, body.metric_id)
    if (
        not store
        or not metric
        or store.workspace_id != access.workspace_id
        or metric.workspace_id != access.workspace_id
    ):
        raise AppError("not_found", "Точка или показатель не найдены", 404)
    require_source_access(await get_source(db, metric.source_id, access.workspace_id), access)
    version = (
        await db.scalar(
            select(func.max(Plan.version)).where(
                Plan.workspace_id == access.workspace_id,
                Plan.store_id == body.store_id,
                Plan.metric_id == body.metric_id,
                Plan.period == body.period,
            )
        )
        or 0
    ) + 1
    plan = Plan(
        workspace_id=access.workspace_id,
        created_by=access.user_id,
        version=version,
        **body.model_dump(),
    )
    db.add(plan)
    await db.flush()
    return plan_payload(plan)


@router.get("/overview")
async def overview(
    access: WorkspaceAccess,
    db: Db,
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    metric_id: str | None = None,
    store_ids: str = "",
):
    end = date_to or date.today()
    start = date_from or end.replace(day=1)
    return await build_overview(
        db,
        access,
        request.app.state.settings,
        start,
        end,
        metric_id,
        [item for item in store_ids.split(",") if item],
        request.app.state.source_connections,
    )


def report_payload(report):
    return record_payload(report, "id title description run_id created_at created_by")


@router.get("/reports")
async def reports(access: WorkspaceAccess, db: Db):
    access.require("analytics:read")
    records = (
        await db.scalars(
            select(Report)
            .where(Report.workspace_id == access.workspace_id)
            .order_by(Report.created_at.desc())
            .limit(200)
        )
    ).all()
    visible = []
    for report in records:
        try:
            await get_run(db, report.run_id, access, shared=True)
            visible.append(report_payload(report))
        except AppError:
            continue
    return visible


@router.post("/reports", status_code=201)
async def create_report(body: ReportCreate, access: WorkspaceAccess, db: Db):
    access.require("reports:write")
    run = await get_run(db, body.run_id, access)
    if run.status != "succeeded":
        raise AppError("run_not_ready", "Сохранить можно только завершённый результат", 409)
    report = Report(
        workspace_id=access.workspace_id, created_by=access.user_id, **body.model_dump()
    )
    db.add(report)
    await db.flush()
    return report_payload(report)


@router.get("/reports/{report_id}")
async def report(report_id: str, access: WorkspaceAccess, db: Db):
    record = await db.get(Report, report_id)
    if record is None or record.workspace_id != access.workspace_id:
        raise AppError("not_found", "Отчёт не найден", 404)
    run = await get_run(db, record.run_id, access, shared=True)
    history = (
        await db.scalars(
            select(QueryRun)
            .where(
                QueryRun.workspace_id == access.workspace_id,
                QueryRun.kind == "refresh",
                QueryRun.context["report_id"].as_string() == record.id,
            )
            .order_by(QueryRun.created_at)
        )
    ).all()
    refreshes, inaccessible = [], 0
    for item in history:
        try:
            refreshes.append(run_payload(await get_run(db, item.id, access, shared=True)))
        except AppError:
            inaccessible += 1
    return {
        **report_payload(record),
        "run": run_payload(run),
        "refreshes": refreshes,
        "inaccessible_refreshes": inaccessible,
    }


@router.post("/reports/{report_id}/refresh", status_code=202)
async def refresh_report(
    report_id: str, body: Refresh, access: WorkspaceAccess, db: Db, request: Request
):
    access.require("reports:write")
    report = await db.scalar(
        select(Report)
        .where(Report.id == report_id, Report.workspace_id == access.workspace_id)
        .with_for_update()
    )
    if not report or report.workspace_id != access.workspace_id:
        raise AppError("not_found", "Отчёт не найден", 404)
    original = await get_run(db, report.run_id, access, shared=True)
    if original.status != "succeeded" or not original.sql:
        raise AppError("invalid_report", "В отчёте отсутствует завершённый запрос", 409)
    from backend.app.assistant.models import QueryRun

    previous = await db.scalar(
        select(QueryRun).where(
            QueryRun.workspace_id == access.workspace_id,
            QueryRun.user_id == access.user_id,
            QueryRun.idempotency_key == body.idempotency_key,
        )
    )
    if previous:
        if previous.kind != "refresh" or previous.context.get("report_id") != report.id:
            raise AppError("idempotency_conflict", "Ключ уже использован", 409)
        return run_payload(await get_run(db, previous.id, access))
    conversation = Conversation(
        workspace_id=access.workspace_id, user_id=access.user_id, title=report.title
    )
    db.add(conversation)
    await db.flush()
    question = QuestionCreate(
        question=original.question,
        source_id=original.source_id,
        store_ids=original.store_ids,
        version=0,
        idempotency_key=body.idempotency_key,
        date_from=original.context.get("date_from"),
        date_to=original.context.get("date_to"),
        metric_id=original.context.get("metric_id"),
    )
    run = await create_run(
        db, access, conversation.id, question, request.app.state.settings, saved_sql=original.sql
    )
    run.context = {**run.context, "report_id": report.id}
    await db.flush()
    return run_payload(run)
