from sqlalchemy import select

from backend.app.access.policy import get_access
from backend.app.assistant.service import get_run
from backend.app.collaboration.models import Case, Measurement, Notification
from backend.app.collaboration.measurements import ensure_measurement_access
from backend.app.infrastructure.errors import AppError


async def check_case(db, case, access):
    if case.workspace_id != access.workspace_id:
        raise AppError("not_found", "Разбор не найден", 404)
    access.require_scope(case.store_ids)
    if case.run_id:
        await get_run(db, case.run_id, access, shared=True)
    measurements = (
        await db.scalars(select(Measurement).where(Measurement.case_id == case.id))
    ).all()
    for measurement in measurements:
        await ensure_measurement_access(db, measurement, access)


async def get_case(db, case_id, access, *, for_update=False):
    statement = (
        select(Case)
        .where(Case.id == case_id, Case.workspace_id == access.workspace_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    case = await db.scalar(statement)
    if case is None:
        raise AppError("not_found", "Разбор не найден", 404)
    await check_case(db, case, access)
    return case


async def validate_recipient(db, user_id, case):
    try:
        access = await get_access(db, user_id, case.workspace_id)
        await check_case(db, case, access)
    except AppError as error:
        raise AppError(
            "recipient_scope", "Адресат не имеет доступа ко всей области разбора", 403
        ) from error


def notify(db, case, user_id, kind, title, body=""):
    db.add(
        Notification(
            workspace_id=case.workspace_id,
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            case_id=case.id,
        )
    )


def ensure_open(case):
    if case.status != "open":
        raise AppError("case_closed", "Разбор завершён. Его история доступна для чтения", 409)
