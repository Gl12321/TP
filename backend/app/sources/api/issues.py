from typing import Literal

from fastapi import APIRouter, Response
from sqlalchemy import or_, select

from backend.app.access.dependencies import Db, WorkspaceAccess
from backend.app.access.models import User
from backend.app.analytics.service import record_payload
from backend.app.infrastructure.database import utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.idempotency import reserve_creation
from backend.app.infrastructure.pagination import Page, paginate
from backend.app.sources.models import IssueComment, Source, SourceIssue
from backend.app.sources.api.schemas import IssueCommentCreate, IssueCreate, IssueUpdate
from backend.app.sources.issues import (
    get_issue,
    notify_issue,
    source_administrators,
    validate_assignee,
)
from backend.app.sources.service import can_read_source, get_source, require_source_access


router = APIRouter(prefix="/workspaces/{workspace_id}/source-issues")


def issue_payload(issue, source):
    return {
        **record_payload(
            issue,
            "id source_id title body status created_by assignee_id resolution source_status source_error last_checked_at created_at updated_at",
        ),
        "source_name": source.name,
        "current_source_status": source.status,
    }


@router.get("/participants")
async def participants(access: WorkspaceAccess, db: Db):
    if not {"analytics:read", "sources:manage"} & access.capabilities:
        raise AppError("forbidden", "Нет доступа к техническим обращениям", 403)
    return await source_administrators(db, access)


@router.get("")
async def issues(
    access: WorkspaceAccess,
    db: Db,
    page: Page,
    response: Response,
    filter: Literal["all", "active", "mine", "resolved"] = "all",
):
    if not {"analytics:read", "sources:manage"} & access.capabilities:
        raise AppError("forbidden", "Нет доступа к техническим обращениям", 403)
    statement = (
        select(SourceIssue, Source)
        .join(Source, Source.id == SourceIssue.source_id)
        .where(SourceIssue.workspace_id == access.workspace_id)
    )
    if page.q:
        statement = statement.where(page.search(SourceIssue.title, SourceIssue.body, Source.name))
    if filter == "active":
        statement = statement.where(SourceIssue.status != "resolved")
    elif filter == "resolved":
        statement = statement.where(SourceIssue.status == "resolved")
    elif filter == "mine":
        statement = statement.where(
            or_(SourceIssue.created_by == access.user_id, SourceIssue.assignee_id == access.user_id)
        )
    if "sources:manage" not in access.capabilities:
        sources = await db.scalars(select(Source).where(Source.workspace_id == access.workspace_id))
        statement = statement.where(
            SourceIssue.source_id.in_(
                source.id for source in sources if can_read_source(source, access)
            )
        )

    async def render(row):
        return issue_payload(*row)

    return await paginate(
        db, statement, SourceIssue, SourceIssue.updated_at, page, response, render
    )


@router.post("", status_code=201)
async def create_issue(body: IssueCreate, access: WorkspaceAccess, db: Db):
    source = await get_source(db, body.source_id, access.workspace_id)
    if "sources:manage" not in access.capabilities:
        require_source_access(source, access)
    issue_id, replayed = await reserve_creation(db, access, "source_issue", body)
    if replayed:
        return issue_payload(await get_issue(db, issue_id, access), source)
    await validate_assignee(db, body.assignee_id, access)
    issue = SourceIssue(
        id=issue_id,
        workspace_id=access.workspace_id,
        created_by=access.user_id,
        source_status=source.status,
        source_error=source.error,
        last_checked_at=source.last_checked_at,
        **body.model_dump(exclude={"idempotency_key"}),
    )
    db.add(issue)
    await db.flush()
    await notify_issue(db, issue, access, "Нужна проверка источника")
    return issue_payload(issue, source)


@router.get("/{issue_id}")
async def issue_detail(issue_id: str, access: WorkspaceAccess, db: Db):
    issue = await get_issue(db, issue_id, access)
    comments = (
        await db.execute(
            select(IssueComment, User)
            .join(User, User.id == IssueComment.author_id)
            .where(IssueComment.issue_id == issue.id)
            .order_by(IssueComment.created_at)
        )
    ).all()
    return {
        **issue_payload(issue, await db.get(Source, issue.source_id)),
        "comments": [
            {**record_payload(comment, "id author_id body created_at"), "author_name": author.name}
            for comment, author in comments
        ],
    }


@router.post("/{issue_id}/comments", status_code=201)
async def comment(issue_id: str, body: IssueCommentCreate, access: WorkspaceAccess, db: Db):
    issue = await get_issue(db, issue_id, access, for_update=True)
    comment = IssueComment(
        workspace_id=access.workspace_id,
        issue_id=issue.id,
        author_id=access.user_id,
        body=body.body,
    )
    db.add(comment)
    issue.updated_at = utcnow()
    await notify_issue(db, issue, access, "Ответ по источнику")
    await db.flush()
    author = await db.get(User, access.user_id)
    return {**record_payload(comment, "id author_id body created_at"), "author_name": author.name}


@router.patch("/{issue_id}")
async def update_issue(issue_id: str, body: IssueUpdate, access: WorkspaceAccess, db: Db):
    access.require("sources:manage")
    issue = await get_issue(db, issue_id, access, for_update=True)
    changes = []
    if "assignee_id" in body.model_fields_set and issue.assignee_id != body.assignee_id:
        await validate_assignee(db, body.assignee_id, access)
        assignee = await db.get(User, body.assignee_id) if body.assignee_id else None
        changes.append("Ответственный: " + (assignee.name if assignee else "не назначен"))
        issue.assignee_id = body.assignee_id
    status = body.status or issue.status
    resolution = (body.resolution or issue.resolution) if status == "resolved" else None
    if status == "resolved" and not resolution:
        raise AppError("resolution_required", "Укажите, что исправлено и как проверен источник")
    if body.resolution and status != "resolved":
        raise AppError("invalid_resolution", "Решение сохраняется при завершении обращения")
    if status != issue.status or resolution != issue.resolution:
        labels = {"open": "Открыто", "in_progress": "В работе", "resolved": "Решено"}
        changes.append("Статус: " + labels[status] + (". " + resolution if resolution else ""))
    if changes:
        db.add(
            IssueComment(
                workspace_id=access.workspace_id,
                issue_id=issue.id,
                author_id=access.user_id,
                body="\n".join(changes),
            )
        )
        issue.status, issue.resolution, issue.updated_at = status, resolution, utcnow()
        await notify_issue(db, issue, access, "Обращение по источнику обновлено")
    await db.flush()
    return issue_payload(issue, await db.get(Source, issue.source_id))
