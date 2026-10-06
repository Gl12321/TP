from typing import Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.access.dependencies import Db, WorkspaceAccess
from backend.app.access.models import User
from backend.app.analytics.service import record_payload
from backend.app.assistant.service import get_run, get_report_run, resolve_scope, run_payload
from backend.app.collaboration.models import (
    AssignedQuestion,
    Case,
    Comment,
    Measurement,
    Notification,
)
from backend.app.collaboration.measurements import capture_measurement, measurement_payload
from backend.app.collaboration.api.schemas import (
    Answer,
    CaseCreate,
    Close,
    CommentCreate,
    QuestionCreate,
    MeasurementCreate,
)
from backend.app.collaboration.service import (
    check_case,
    ensure_open,
    get_case,
    notify,
    validate_recipient,
)
from backend.app.infrastructure.database import utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.idempotency import reserve_creation
from backend.app.infrastructure.pagination import Page, paginate
from backend.app.sources.issues import get_issue


router = APIRouter(prefix="/workspaces/{workspace_id}")


def case_payload(case):
    return record_payload(
        case,
        "id title description status store_ids run_id created_by assignee_id conclusion created_at updated_at",
    )


def question_payload(question):
    return record_payload(question, "id body assignee_id status answer created_at created_by")


@router.get("/cases")
async def cases(
    access: WorkspaceAccess,
    db: Db,
    page: Page,
    response: Response,
    filter: Literal["all", "open", "closed", "mine", "waiting"] = "all",
    store_id: str | None = None,
):
    access.require("analytics:read")
    statement = select(Case).where(Case.workspace_id == access.workspace_id)
    if page.q:
        statement = statement.where(page.search(Case.title, Case.description))
    if filter in {"open", "closed"}:
        statement = statement.where(Case.status == filter)
    elif filter == "mine":
        statement = statement.where(
            or_(Case.created_by == access.user_id, Case.assignee_id == access.user_id)
        )
    elif filter == "waiting":
        statement = statement.where(
            select(AssignedQuestion.id)
            .where(
                AssignedQuestion.case_id == Case.id,
                AssignedQuestion.assignee_id == access.user_id,
                AssignedQuestion.status == "open",
            )
            .exists()
        )
    if store_id:
        access.require_scope([store_id])
        if db.bind.dialect.name == "postgresql":
            statement = statement.where(cast(Case.store_ids, JSONB).contains([store_id]))
        else:
            stores = func.json_each(Case.store_ids).table_valued("value")
            statement = statement.where(
                select(stores.c.value).where(stores.c.value == store_id).exists()
            )

    async def render(row):
        case = row[0]
        try:
            await check_case(db, case, access)
        except AppError:
            return None
        pending = (
            await db.scalars(
                select(AssignedQuestion.assignee_id).where(
                    AssignedQuestion.case_id == case.id, AssignedQuestion.status == "open"
                )
            )
        ).all()
        return {
            **case_payload(case),
            "pending_for_me": access.user_id in pending,
            "pending_assignee_ids": sorted(set(pending)),
        }

    return await paginate(db, statement, Case, Case.updated_at, page, response, render)


@router.post("/cases", status_code=201)
async def create_case(body: CaseCreate, access: WorkspaceAccess, db: Db, request: Request):
    access.require("cases:write")
    case_id, replayed = await reserve_creation(db, access, "case", body)
    if replayed:
        return case_payload(await get_case(db, case_id, access))
    scope = await resolve_scope(db, access, body.store_ids)
    if body.report_id:
        await get_report_run(db, body.run_id, access, body.report_id)
    if body.run_id:
        run = await get_run(db, body.run_id, access, shared=bool(body.report_id))
        if run.status != "succeeded" or not set(run.store_ids).issubset(scope):
            raise AppError(
                "result_scope", "Область разбора должна включать весь прикреплённый результат", 409
            )
    case = Case(
        id=case_id,
        workspace_id=access.workspace_id,
        created_by=access.user_id,
        **body.model_dump(exclude={"store_ids", "measurement", "report_id", "idempotency_key"}),
        store_ids=scope,
    )
    if case.assignee_id:
        await validate_recipient(db, case.assignee_id, case)
    db.add(case)
    await db.flush()
    if body.measurement:
        await capture_measurement(
            db,
            access,
            case,
            body.measurement.metric_id,
            body.measurement.date_from,
            body.measurement.date_to,
            "initial-measurement",
            request.app.state.settings,
            request.app.state.source_connections,
        )
        if case.assignee_id:
            await validate_recipient(db, case.assignee_id, case)
    if case.assignee_id and case.assignee_id != access.user_id:
        notify(db, case, case.assignee_id, "case_assigned", "Вам назначен разбор", case.title)
    return case_payload(case)


@router.get("/cases/{case_id}")
async def case(case_id: str, access: WorkspaceAccess, db: Db):
    item = await get_case(db, case_id, access)
    comments = (
        await db.execute(
            select(Comment, User)
            .join(User, User.id == Comment.author_id)
            .where(Comment.case_id == item.id)
            .order_by(Comment.created_at)
        )
    ).all()
    questions = (
        await db.scalars(
            select(AssignedQuestion)
            .where(AssignedQuestion.case_id == item.id)
            .order_by(AssignedQuestion.created_at)
        )
    ).all()
    measurements = (
        await db.scalars(
            select(Measurement)
            .where(Measurement.case_id == item.id)
            .order_by(Measurement.created_at)
        )
    ).all()
    result = {
        **case_payload(item),
        "comments": [
            {**record_payload(comment, "id author_id body created_at"), "author_name": author.name}
            for comment, author in comments
        ],
        "questions": [question_payload(question) for question in questions],
        "run": None,
        "measurements": [
            measurement_payload(measurement, measurements[0]) for measurement in measurements
        ],
    }
    if item.run_id:
        result["run"] = run_payload(await get_run(db, item.run_id, access, shared=True))
    return result


@router.post("/cases/{case_id}/measurements", status_code=201)
async def remeasure(
    case_id: str, body: MeasurementCreate, access: WorkspaceAccess, db: Db, request: Request
):
    access.require("cases:write")
    case = await get_case(db, case_id, access, for_update=True)
    if case.status == "closed" and case.created_by != access.user_id:
        raise AppError(
            "forbidden", "После завершения повторное измерение добавляет автор разбора", 403
        )
    basis = await db.scalar(
        select(Measurement)
        .where(Measurement.case_id == case.id)
        .order_by(Measurement.created_at)
        .limit(1)
    )
    if basis is None:
        raise AppError(
            "measurement_basis_missing", "У разбора нет штатного измерения для повтора", 409
        )
    measurement = await capture_measurement(
        db,
        access,
        case,
        basis.metric_id,
        body.date_from,
        body.date_to,
        body.idempotency_key,
        request.app.state.settings,
        request.app.state.source_connections,
    )
    case.updated_at = utcnow()
    return measurement_payload(measurement, basis)


@router.post("/cases/{case_id}/comments", status_code=201)
async def comment(case_id: str, body: CommentCreate, access: WorkspaceAccess, db: Db):
    access.require("cases:write")
    case = await get_case(db, case_id, access, for_update=True)
    ensure_open(case)
    comment = Comment(case_id=case.id, author_id=access.user_id, body=body.body)
    db.add(comment)
    case.updated_at = utcnow()
    await db.flush()
    for user_id in {case.created_by, case.assignee_id} - {None, access.user_id}:
        notify(db, case, user_id, "comment", "Новый комментарий", case.title)
    author = await db.get(User, access.user_id)
    return {**record_payload(comment, "id author_id body created_at"), "author_name": author.name}


@router.post("/cases/{case_id}/questions", status_code=201)
async def ask(case_id: str, body: QuestionCreate, access: WorkspaceAccess, db: Db):
    access.require("cases:write")
    case = await get_case(db, case_id, access, for_update=True)
    ensure_open(case)
    await validate_recipient(db, body.assignee_id, case)
    question = AssignedQuestion(
        case_id=case.id, created_by=access.user_id, body=body.body, assignee_id=body.assignee_id
    )
    db.add(question)
    case.updated_at = utcnow()
    notify(db, case, body.assignee_id, "question", "Вам задан вопрос", case.title)
    await db.flush()
    return question_payload(question)


@router.post("/cases/{case_id}/questions/{question_id}/answer")
async def answer(case_id: str, question_id: str, body: Answer, access: WorkspaceAccess, db: Db):
    access.require("cases:write")
    case = await get_case(db, case_id, access, for_update=True)
    ensure_open(case)
    question = await db.get(AssignedQuestion, question_id)
    if question is None or question.case_id != case.id:
        raise AppError("not_found", "Вопрос не найден", 404)
    if question.assignee_id != access.user_id:
        raise AppError("forbidden", "Ответить может назначенный адресат", 403)
    if question.status != "open":
        raise AppError("already_answered", "Ответ уже сохранён", 409)
    question.answer = body.answer
    question.status = "answered"
    case.updated_at = utcnow()
    notify(db, case, question.created_by, "answer", "Получен ответ на вопрос", case.title)
    await db.flush()
    return question_payload(question)


@router.post("/cases/{case_id}/close")
async def close(case_id: str, body: Close, access: WorkspaceAccess, db: Db):
    access.require("cases:write")
    case = await get_case(db, case_id, access, for_update=True)
    ensure_open(case)
    if access.user_id not in {case.created_by, case.assignee_id} and access.role not in {
        "director",
        "regional_manager",
    }:
        raise AppError(
            "forbidden", "Зафиксировать итог может автор, ответственный или руководитель", 403
        )
    case.status = "closed"
    case.conclusion = body.conclusion
    case.updated_at = utcnow()
    for user_id in {case.created_by, case.assignee_id} - {None, access.user_id}:
        notify(db, case, user_id, "case_closed", "Зафиксирован итог разбора", case.title)
    await db.flush()
    return case_payload(case)


@router.get("/notifications")
async def notifications(access: WorkspaceAccess, db: Db):
    records = (
        await db.scalars(
            select(Notification)
            .where(
                Notification.workspace_id == access.workspace_id,
                Notification.user_id == access.user_id,
            )
            .order_by(Notification.created_at.desc())
            .limit(100)
        )
    ).all()
    visible = []
    for item in records:
        try:
            if item.case_id:
                await get_case(db, item.case_id, access)
            else:
                await get_issue(db, item.source_issue_id, access)
            visible.append(
                record_payload(
                    item, "id kind title body case_id source_issue_id read_at created_at"
                )
            )
        except AppError:
            continue
    return visible


@router.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: str, access: WorkspaceAccess, db: Db):
    item = await db.get(Notification, notification_id)
    if not item or item.workspace_id != access.workspace_id or item.user_id != access.user_id:
        raise AppError("not_found", "Уведомление не найдено", 404)
    if item.case_id:
        await get_case(db, item.case_id, access)
    else:
        await get_issue(db, item.source_issue_id, access)
    item.read_at = utcnow()
    return {"ok": True}
