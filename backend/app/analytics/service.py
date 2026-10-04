from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlglot import exp

from backend.app.access.policy import get_access
from backend.app.analytics.models import Metric, Plan, Store
from backend.app.assistant.service import resolve_scope
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.database import utcnow
from backend.app.sources.service import (
    ScopedExecutor,
    ensure_ready,
    get_source,
    can_read_source,
    require_source_access,
)


def record_payload(record, fields):
    return {field: getattr(record, field) for field in fields.split()}


def metric_payload(metric):
    return record_payload(
        metric,
        "id key name description unit source_id table_schema table_name value_column date_column store_column aggregation version",
    )


def plan_payload(plan):
    result = record_payload(plan, "id store_id metric_id period version")
    result["amount"] = str(plan.amount)
    return result


def latest(records, key):
    selected = {}
    for record in records:
        identity = key(record)
        if identity not in selected or record.version > selected[identity].version:
            selected[identity] = record
    return list(selected.values())


def aggregate_sql(metric, start, end):
    def column(name):
        return exp.column(name, table="t", quoted=True)

    value = column(metric.value_column) if metric.value_column else exp.Star()
    aggregate = {"sum": exp.Sum, "count": exp.Count, "avg": exp.Avg}[metric.aggregation](
        this=value.copy()
    )
    statement = exp.select(
        column(metric.store_column).as_("store_code", quoted=True),
        aggregate.as_("actual", quoted=True),
        exp.Count(this=value.copy()).as_("weight", quoted=True),
    ).from_(
        exp.Table(
            this=exp.to_identifier(metric.table_name, quoted=True),
            db=exp.to_identifier(metric.table_schema, quoted=True),
            alias=exp.TableAlias(this=exp.to_identifier("t", quoted=True)),
        )
    )
    statement = statement.where(
        exp.and_(
            exp.GTE(
                this=column(metric.date_column), expression=exp.Literal.string(start.isoformat())
            ),
            exp.LT(
                this=column(metric.date_column),
                expression=exp.Literal.string((end + timedelta(days=1)).isoformat()),
            ),
        )
    ).group_by(column(metric.store_column))
    return statement.sql(dialect="postgres")


def comparison_sql(metric, start, end, previous_start, previous_end):
    statement = exp.select(exp.column(metric.store_column, table="t", quoted=True)).from_(
        exp.Table(
            this=exp.to_identifier(metric.table_name, quoted=True),
            db=exp.to_identifier(metric.table_schema, quoted=True),
            alias=exp.TableAlias(this=exp.to_identifier("t", quoted=True)),
        )
    )
    columns = []
    for first, last in ((start, end), (previous_start, previous_end)):
        condition = exp.and_(
            exp.GTE(
                this=exp.column(metric.date_column, table="t", quoted=True),
                expression=exp.Literal.string(first.isoformat()),
            ),
            exp.LT(
                this=exp.column(metric.date_column, table="t", quoted=True),
                expression=exp.Literal.string((last + timedelta(days=1)).isoformat()),
            ),
        )
        value = (
            exp.column(metric.value_column, table="t", quoted=True)
            if metric.value_column
            else exp.Literal.number(1)
        )
        selected = exp.Case().when(condition.copy(), value)
        aggregate = {"sum": exp.Sum, "count": exp.Count, "avg": exp.Avg}[metric.aggregation]
        columns.extend(
            (
                aggregate(this=selected.copy()),
                exp.Count(this=selected),
                exp.Count(this=exp.Case().when(condition, exp.Literal.number(1))),
            )
        )
    return (
        statement.select(*columns)
        .where(
            exp.and_(
                exp.GTE(
                    this=exp.column(metric.date_column, table="t", quoted=True),
                    expression=exp.Literal.string(previous_start.isoformat()),
                ),
                exp.LT(
                    this=exp.column(metric.date_column, table="t", quoted=True),
                    expression=exp.Literal.string((end + timedelta(days=1)).isoformat()),
                ),
            )
        )
        .group_by(exp.column(metric.store_column, table="t", quoted=True))
        .sql(dialect="postgres")
    )


def decimal(value):
    result = Decimal(str(value)) if value is not None else None
    return result if result is None or result.is_finite() else None


def number(value):
    return str(value) if value is not None else None


def percent(numerator, denominator):
    return (
        (numerator / denominator * 100).quantize(Decimal("0.01"))
        if numerator is not None and denominator
        else None
    )


def total(values, aggregation):
    values = [(value, weight) for value, weight in values if value is not None]
    if not values:
        return None
    if aggregation == "avg":
        weight = sum(count for _, count in values)
        return sum(value * count for value, count in values) / weight if weight else None
    return sum(value for value, _ in values)


def full_months(start, end):
    if start.day != 1 or (end + timedelta(days=1)).day != 1:
        return []
    result, current = [], start
    while current <= end:
        result.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return result


def comparison_period(start, end, months):
    previous_end = start - timedelta(days=1)
    if not months:
        return previous_end - (end - start), previous_end
    previous_start = start
    for _ in months:
        previous_start = (previous_start - timedelta(days=1)).replace(day=1)
    return previous_start, previous_end


async def overview(db, access, settings, start, end, metric_id, requested, connections):
    access.require("analytics:read")
    if start > end or (end - start).days > 730 or end == date.max:
        raise AppError("invalid_period", "Выберите период продолжительностью не более двух лет")
    months = full_months(start, end)
    try:
        previous_start, previous_end = comparison_period(start, end, months)
    except (ValueError, OverflowError) as error:
        raise AppError(
            "invalid_period", "Для выбранного периода недоступен предыдущий интервал"
        ) from error
    scope = (
        await resolve_scope(db, access, requested)
        if (
            requested
            or await db.scalar(
                select(Store.id).where(Store.workspace_id == access.workspace_id).limit(1)
            )
        )
        else []
    )
    stores = (await db.scalars(select(Store).where(Store.id.in_(scope)).order_by(Store.name))).all()
    metrics = (
        await db.scalars(
            select(Metric)
            .where(Metric.workspace_id == access.workspace_id)
            .order_by(Metric.created_at.desc())
        )
    ).all()
    if not metric_id:
        allowed_metrics = []
        for candidate in metrics:
            candidate_source = await get_source(db, candidate.source_id, access.workspace_id)
            if can_read_source(candidate_source, access):
                allowed_metrics.append(candidate)
        metrics = allowed_metrics
    metric = (
        next((item for item in metrics if item.id == metric_id), None)
        if metric_id
        else (metrics[0] if metrics else None)
    )
    if metric_id and metric is None:
        raise AppError("not_found", "Показатель не найден", 404)
    response = {
        "date_from": start,
        "date_to": end,
        "metric": metric_payload(metric) if metric else None,
        "totals": {"actual": None, "plan": None, "attainment": None},
        "stores": [],
        "coverage": {"available": 0, "total": len(stores)},
        "warnings": [],
        "comparison": None,
    }
    if not metric or not stores:
        response["warnings"].append(
            "Добавьте точки, подключите источник и определите показатель для обзора"
        )
        return response
    source = await get_source(db, metric.source_id, access.workspace_id)
    require_source_access(source, access)
    ensure_ready(source)
    source_version = source.catalog_version

    async def recheck():
        current = await get_access(db, access.user_id, access.workspace_id)
        current.require_scope(scope)
        await db.refresh(source)
        require_source_access(source, current)
        if source.catalog_version != source_version or not source.enabled:
            raise AppError("access_changed", "Доступ к источнику изменился", 403)

    from sql_agent.contracts import QueryContext, QueryError

    executor = ScopedExecutor(
        source, [store.code for store in stores], settings, recheck, connections
    )
    try:
        calculation = await executor.execute(
            comparison_sql(metric, start, end, previous_start, previous_end), QueryContext()
        )
    except QueryError as error:
        raise AppError(error.code, str(error), 503) from error
    if calculation.truncated:
        raise AppError(
            "overview_truncated", "Слишком много точек для полного обзора. Сузьте область", 422
        )
    by_code = {str(row[0]): (decimal(row[1]), int(row[2])) for row in calculation.rows if row[3]}
    previous_by_code = {
        str(row[0]): (decimal(row[4]), int(row[5])) for row in calculation.rows if row[6]
    }
    plans = (
        latest(
            (
                await db.scalars(
                    select(Plan).where(
                        Plan.workspace_id == access.workspace_id,
                        Plan.metric_id == metric.id,
                        Plan.store_id.in_(scope),
                        Plan.period.in_(months),
                    )
                )
            ).all(),
            lambda item: (item.store_id, item.period),
        )
        if months
        else []
    )
    plans_by_store = {}
    for plan in plans:
        plans_by_store.setdefault(plan.store_id, []).append(plan)
    matched_actuals, matched_plans, comparable_current, comparable_previous = [], [], [], []
    for store in stores:
        actual, weight = by_code.get(store.code, (None, 0))
        previous_actual, previous_weight = previous_by_code.get(store.code, (None, 0))
        store_plans = plans_by_store.get(store.id, [])
        plan = (
            sum(item.amount for item in store_plans)
            if months
            and len(store_plans) == len(months)
            and not (metric.aggregation == "avg" and len(months) > 1)
            else None
        )
        if actual is not None and plan is not None:
            matched_actuals.append((actual, weight))
            matched_plans.append(plan)
        if actual is not None and previous_actual is not None:
            comparable_current.append((actual, weight))
            comparable_previous.append((previous_actual, previous_weight))
        response["stores"].append(
            {
                "store_id": store.id,
                "name": store.name,
                "city": store.city,
                "actual": number(actual),
                "actual_weight": weight,
                "plan": number(plan),
                "plan_versions": [plan_payload(item) for item in store_plans]
                if plan is not None
                else [],
                "attainment": number(percent(actual, plan)),
                "status": "has_rows" if store.code in by_code else "no_data",
                "previous_actual": number(previous_actual),
                "change_percent": number(percent(actual - previous_actual, previous_actual))
                if actual is not None and previous_actual is not None
                else None,
            }
        )
    aggregate_actual = total(list(by_code.values()), metric.aggregation)
    aggregate_plan = sum(matched_plans) if matched_plans else None
    response["totals"] = {
        "actual": number(aggregate_actual),
        "plan": number(aggregate_plan),
        "attainment": number(percent(total(matched_actuals, metric.aggregation), aggregate_plan)),
        "planned_store_count": len(matched_plans),
    }
    if metric.aggregation == "avg":
        response["totals"]["plan"] = None
        response["totals"]["attainment"] = None
        response["warnings"].append(
            "Средние значения планов точек не складываются; выполнение плана доступно по каждой точке"
        )
        if len(months) > 1:
            response["warnings"].append(
                "Планы средних значений за разные месяцы нельзя складывать без весов; выберите один месяц"
            )
    a, b = (
        total(comparable_current, metric.aggregation),
        total(comparable_previous, metric.aggregation),
    )
    response["comparison"] = {
        "date_from": previous_start,
        "date_to": previous_end,
        "current": number(a),
        "previous": number(b),
        "delta": number(a - b) if a is not None and b is not None else None,
        "change_percent": number(percent(a - b, b)) if a is not None and b is not None else None,
        "comparable_count": len(comparable_current),
    }
    response["coverage"]["available"] = sum(store.code in by_code for store in stores)
    response["captured_at"] = utcnow()
    response["calculation"] = {"sql": calculation.sql, "execution": executor.execution}
    cities = {}
    for store in stores:
        cities.setdefault(store.city or "Без города", []).append(store)
    response["cities"] = []
    result_by_store = {item["store_id"]: item for item in response["stores"]}
    for city, members in sorted(cities.items()):
        codes = [store.code for store in members]
        comparable = [
            code
            for code in codes
            if code in by_code
            and code in previous_by_code
            and by_code[code][0] is not None
            and previous_by_code[code][0] is not None
        ]
        current = total([by_code[code] for code in comparable], metric.aggregation)
        previous = total([previous_by_code[code] for code in comparable], metric.aggregation)
        planned = [
            store
            for store in members
            if result_by_store[store.id]["plan"] is not None
            and store.code in by_code
            and by_code[store.code][0] is not None
        ]
        plan = (
            sum(decimal(result_by_store[store.id]["plan"]) for store in planned)
            if planned and metric.aggregation != "avg"
            else None
        )
        response["cities"].append(
            {
                "city": city,
                "store_count": len(members),
                "available_count": sum(code in by_code for code in codes),
                "actual": number(
                    total([by_code[code] for code in codes if code in by_code], metric.aggregation)
                ),
                "previous_actual": number(previous),
                "comparable_current": number(current),
                "change_percent": number(percent(current - previous, previous))
                if current is not None and previous is not None
                else None,
                "plan": number(plan),
                "attainment": number(
                    percent(
                        total([by_code[store.code] for store in planned], metric.aggregation), plan
                    )
                ),
                "comparable_count": len(comparable),
            }
        )
    response["warnings"].append(
        "Наличие строк не подтверждает полноту загрузки. Полнота периода источником не подтверждена"
    )
    if not months:
        response["warnings"].append(
            "Помесячный план показывается только за полные календарные месяцы"
        )
    return response


async def store_analytics(
    db, access, settings, store_id, metric_id, start, end, grain, connections
):
    access.require_scope([store_id])
    if start > end or (end - start).days > 730 or end == date.max:
        raise AppError("invalid_period", "Выберите период продолжительностью не более двух лет")
    store, metric = await db.get(Store, store_id), await db.get(Metric, metric_id)
    if (
        not store
        or not store.active
        or store.workspace_id != access.workspace_id
        or not metric
        or metric.workspace_id != access.workspace_id
    ):
        raise AppError("not_found", "Точка или показатель не найдены", 404)
    source = await get_source(db, metric.source_id, access.workspace_id)
    require_source_access(source, access)
    ensure_ready(source)
    version = source.catalog_version

    async def recheck():
        current = await get_access(db, access.user_id, access.workspace_id)
        current.require_scope([store_id])
        await db.refresh(source)
        require_source_access(source, current)
        await db.refresh(store)
        if source.catalog_version != version or not source.enabled or not store.active:
            raise AppError("access_changed", "Доступ к данным изменился", 403)

    column = exp.column(metric.date_column, table="t", quoted=True)
    date_parts = [
        exp.Extract(this=exp.Var(this=unit), expression=column.copy())
        for unit in ("YEAR", "MONTH", "DAY")
    ]
    value = (
        exp.column(metric.value_column, table="t", quoted=True)
        if metric.value_column
        else exp.Star()
    )
    aggregation = {"sum": exp.Sum, "count": exp.Count, "avg": exp.Avg}[metric.aggregation]
    statement = (
        exp.select(
            *(part.copy() for part in date_parts),
            aggregation(this=value.copy()).as_("actual"),
            exp.Count(this=value).as_("weight"),
        )
        .from_(
            exp.Table(
                this=exp.to_identifier(metric.table_name, quoted=True),
                db=exp.to_identifier(metric.table_schema, quoted=True),
                alias=exp.TableAlias(this=exp.to_identifier("t", quoted=True)),
            )
        )
        .where(
            exp.and_(
                exp.GTE(this=column.copy(), expression=exp.Literal.string(start.isoformat())),
                exp.LT(
                    this=column,
                    expression=exp.Literal.string((end + timedelta(days=1)).isoformat()),
                ),
            )
        )
        .group_by(*(part.copy() for part in date_parts))
    )
    from sql_agent.contracts import QueryContext, QueryError

    executor = ScopedExecutor(source, [store.code], settings, recheck, connections)
    try:
        result = await executor.execute(statement.sql(dialect="postgres"), QueryContext())
    except QueryError as error:
        raise AppError(error.code, str(error), 503) from error
    if result.truncated:
        raise AppError("analytics_truncated", "Сузьте период детализации", 422)
    daily = {
        date(int(row[0]), int(row[1]), int(row[2])): (decimal(row[3]), int(row[4]))
        for row in result.rows
    }
    buckets = {}
    for actual_day, value in daily.items():
        bucket = actual_day if grain == "day" else actual_day - timedelta(days=actual_day.weekday())
        buckets.setdefault(bucket, []).append(value)
    values = {
        bucket: (total(items, metric.aggregation), sum(weight for _, weight in items))
        for bucket, items in buckets.items()
    }
    day = start if grain == "day" else start - timedelta(days=start.weekday())
    step = timedelta(days=1 if grain == "day" else 7)
    series = []
    while day <= end:
        actual, weight = values.get(day, (None, 0))
        series.append({"date": day, "value": number(decimal(actual)), "weight": weight})
        day += step
    return {
        "store": record_payload(store, "id name code city owner_name"),
        "metric": metric_payload(metric),
        "date_from": start,
        "date_to": end,
        "grain": grain,
        "series": series,
        "warnings": [
            "Пропущенные дни не равны нулевым продажам. Полнота источника не подтверждена"
        ],
        "calculation": {"sql": result.sql, "execution": executor.execution},
        "captured_at": utcnow(),
    }
