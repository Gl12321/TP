from sqlalchemy import select

from backend.app.access.models import Membership, User
from backend.app.access.policy import from_membership, get_access
from backend.app.collaboration.models import Notification
from backend.app.infrastructure.errors import AppError
from backend.app.sources.models import SourceIssue
from backend.app.sources.service import get_source, require_source_access


async def get_issue(db, issue_id, access, *, for_update=False):
    statement = (
        select(SourceIssue)
        .where(SourceIssue.id == issue_id, SourceIssue.workspace_id == access.workspace_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    issue = await db.scalar(statement)
    if issue is None:
        raise AppError("not_found", "Техническое обращение не найдено", 404)
    if "sources:manage" not in access.capabilities:
        require_source_access(await get_source(db, issue.source_id, access.workspace_id), access)
    return issue


async def source_administrators(db, access):
    records = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == access.workspace_id,
                Membership.active.is_(True),
                User.active.is_(True),
            )
            .order_by(User.name)
        )
    ).all()
    return [
        {"id": user.id, "name": user.name}
        for member, user in records
        if "sources:manage" in from_membership(member).capabilities
    ]


async def validate_assignee(db, user_id, access):
    if user_id is not None:
        target = await get_access(db, user_id, access.workspace_id)
        if "sources:manage" not in target.capabilities:
            raise AppError(
                "invalid_assignee", "Выберите участника с правом обслуживать источники", 403
            )


async def notify_issue(db, issue, access, title):
    targets = {issue.created_by}
    administrators = {item["id"] for item in await source_administrators(db, access)}
    if issue.assignee_id in administrators:
        targets.add(issue.assignee_id)
    else:
        targets.update(administrators)
    for user_id in targets - {access.user_id}:
        db.add(
            Notification(
                workspace_id=issue.workspace_id,
                user_id=user_id,
                kind="source_issue",
                title=title,
                body=issue.title,
                source_issue_id=issue.id,
            )
        )
