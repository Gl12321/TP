from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from backend.app.analytics.models import Metric
from backend.app.analytics.service import overview, decimal, number, percent, total
from backend.app.collaboration.models import Measurement
from backend.app.infrastructure.errors import AppError
from backend.app.sources.service import get_source, require_source_access


def measurement_payload(item, initial=None):
    result = {
        "id": item.id,
        "metric": item.overview["metric"],
        "store_ids": item.store_ids,
        "date_from": item.date_from,
        "date_to": item.date_to,
        "created_at": item.created_at,
        "created_by": item.created_by,
        "overview": item.overview,
        "change_from_initial": None,
    }
    if initial and initial.id != item.id:
        original = {row["store_id"]: row for row in initial.overview["stores"]}
        comparable = [
            (row, original[row["store_id"]])
            for row in item.overview["stores"]
            if row["store_id"] in original
            and row["actual"] is not None
            and original[row["store_id"]]["actual"] is not None
        ]
        aggregation = item.overview["metric"]["aggregation"]
        current = total(
            [(decimal(row["actual"]), row.get("actual_weight", 0)) for row, _ in comparable],
            aggregation,
        )
        previous = total(
            [(decimal(row["actual"]), row.get("actual_weight", 0)) for _, row in comparable],
            aggregation,
        )
        delta = current - previous if current is not None and previous is not None else None
        result["change_from_initial"] = {
            "initial_measurement_id": initial.id,
            "current": number(current),
            "initial": number(previous),
            "delta": number(delta),
            "change_percent": number(percent(delta, previous)),
            "comparable_count": len(comparable),
        }
    return result


async def ensure_measurement_access(db, item, access):
    if item.workspace_id != access.workspace_id:
        raise AppError("not_found", "Измерение недоступно", 404)
    access.require_scope(item.store_ids)
    source = await get_source(db, item.source_id, access.workspace_id)
    require_source_access(source, access)
    if not source.enabled or source.policy_revision != item.policy_revision:
        raise AppError(
            "measurement_access_changed", "Доступ к сохранённому измерению изменился", 403
        )


async def capture_measurement(db, access, case, metric_id, start, end, key, settings, connections):
    existing = await db.scalar(
        select(Measurement).where(
            Measurement.case_id == case.id, Measurement.idempotency_key == key
        )
    )
    if existing:
        if (
            existing.metric_id != metric_id
            or existing.date_from != start
            or existing.date_to != end
        ):
            raise AppError(
                "idempotency_conflict", "Ключ уже использован для другого измерения", 409
            )
        await ensure_measurement_access(db, existing, access)
        return existing
    metric = await db.get(Metric, metric_id)
    if not metric or metric.workspace_id != access.workspace_id:
        raise AppError("not_found", "Показатель не найден", 404)
    source = await get_source(db, metric.source_id, access.workspace_id)
    policy_revision, catalog_version = source.policy_revision, source.catalog_version
    result = await overview(
        db, access, settings, start, end, metric_id, case.store_ids, connections
    )
    source = await get_source(db, metric.source_id, access.workspace_id, for_update=True)
    if (
        not source.enabled
        or source.policy_revision != policy_revision
        or source.catalog_version != catalog_version
    ):
        raise AppError(
            "source_changed", "Источник изменился во время измерения. Повторите действие", 409
        )
    item = Measurement(
        case_id=case.id,
        workspace_id=access.workspace_id,
        source_id=source.id,
        metric_id=metric_id,
        created_by=access.user_id,
        idempotency_key=key,
        date_from=start,
        date_to=end,
        store_ids=list(case.store_ids),
        policy_revision=policy_revision,
        catalog_version=catalog_version,
        overview=jsonable_encoder(result),
    )
    db.add(item)
    await db.flush()
    return item
