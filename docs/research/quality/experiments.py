import argparse
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import random
import sqlite3
import sys


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int | None
    amount_cents: int | None
    status: str
    created_on: str
    paid_on: str | None


@dataclass(frozen=True)
class Fixture:
    orders: tuple[Order, ...]
    items: tuple[tuple[int, int, str], ...]
    customers: tuple[tuple[int, str | None], ...] = ((1, "RU"), (2, "KZ"))


@dataclass(frozen=True)
class Plan:
    metric: str = "paid_revenue"
    group_by: str | None = None
    country: str | None = None
    start: str | None = None
    end: str | None = None
    date_basis: str = "paid_on"
    catalog_version: int = 1


class UnsupportedPlan(ValueError):
    pass


METRICS = {
    "paid_revenue": "sum",
    "paid_orders": "count",
    "paid_average_order": "ratio",
}
DIMENSIONS = {
    "country": 'c."country"',
    "month": 'substr(o."{date_basis}", 1, 7)',
}
BASE = Fixture(
    orders=(
        Order(1, 1, 10_000, "paid", "2026-01-10", "2026-01-10"),
        Order(2, 2, 20_000, "paid", "2026-01-11", "2026-01-11"),
    ),
    items=((1, 1, "A"), (2, 2, "B")),
)


def connect(fixture):
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript('''
        CREATE TABLE customers(id INTEGER PRIMARY KEY, country TEXT);
        CREATE TABLE orders(
            id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id),
            amount_cents INTEGER,
            status TEXT NOT NULL,
            created_on TEXT NOT NULL,
            paid_on TEXT
        );
        CREATE TABLE items(
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            category TEXT NOT NULL
        );
    ''')
    connection.executemany("INSERT INTO customers VALUES (?, ?)", fixture.customers)
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
        [tuple(asdict(order).values()) for order in fixture.orders],
    )
    connection.executemany("INSERT INTO items VALUES (?, ?, ?)", fixture.items)
    return connection


def compile_plan(plan, *, customer_key_verified=True):
    if plan.catalog_version != 1:
        raise UnsupportedPlan("catalog_version_changed")
    if plan.metric not in METRICS:
        raise UnsupportedPlan("unknown_metric")
    if plan.group_by is not None and plan.group_by not in DIMENSIONS:
        raise UnsupportedPlan("unsupported_dimension_or_allocation")
    if plan.date_basis not in {"created_on", "paid_on"}:
        raise UnsupportedPlan("unsupported_date_basis")
    if (plan.start is None) != (plan.end is None):
        raise UnsupportedPlan("incomplete_period")
    if plan.start is not None:
        if date.fromisoformat(plan.start) >= date.fromisoformat(plan.end):
            raise UnsupportedPlan("invalid_period")
    if plan.country is not None and not isinstance(plan.country, str):
        raise UnsupportedPlan("invalid_country")
    needs_customers = plan.group_by == "country" or plan.country is not None
    if needs_customers and not customer_key_verified:
        raise UnsupportedPlan("unknown_join_cardinality")
    dimension = DIMENSIONS[plan.group_by].format(date_basis=plan.date_basis) if plan.group_by is not None else "NULL"
    sql = (
        f'SELECT {dimension} AS dimension, '
        'SUM(o."amount_cents") AS amount_sum, '
        'COUNT(*) AS entity_count, '
        'COUNT(o."amount_cents") AS known_amount_count '
        'FROM "orders" AS o'
    )
    if needs_customers:
        sql += ' LEFT JOIN "customers" AS c ON o."customer_id" = c."id"'
    predicates = ['o."status" = :required_status']
    parameters = {"required_status": "paid"}
    if plan.country is not None:
        predicates.append('c."country" = :country')
        parameters["country"] = plan.country
    if plan.start is not None:
        predicates.extend([
            f'o."{plan.date_basis}" >= :start',
            f'o."{plan.date_basis}" < :end',
        ])
        parameters.update(start=plan.start, end=plan.end)
    sql += " WHERE " + " AND ".join(predicates)
    if plan.group_by is not None:
        sql += f" GROUP BY {dimension} ORDER BY {dimension}"
    return sql, parameters


def run_plan(connection, plan):
    sql, parameters = compile_plan(plan)
    rows = []
    for dimension, amount_sum, entities, known in connection.execute(sql, parameters):
        missing = entities - known
        if METRICS[plan.metric] == "count":
            value = Decimal(entities)
            status = "complete"
        elif missing:
            value = None
            status = "incomplete_amounts"
        elif not entities:
            value = None
            status = "no_matching_rows"
        elif METRICS[plan.metric] == "sum":
            value = Decimal(amount_sum)
            status = "complete"
        else:
            value = Decimal(amount_sum) / Decimal(entities)
            status = "complete"
        rows.append({
            "dimension": dimension,
            "value": None if value is None else str(value),
            "status": status,
            "entity_count": entities,
            "known_amount_count": known,
            "missing_amount_count": missing,
        })
    encoded = json.dumps(asdict(plan), sort_keys=True, separators=(",", ":"))
    return {
        "plan": asdict(plan),
        "plan_id": sha256(encoded.encode()).hexdigest()[:16],
        "sql": sql,
        "parameters": parameters,
        "rows": rows,
    }


def value(connection, sql):
    return connection.execute(sql).fetchone()[0]


def mutation_experiment():
    fixtures = {
        "baseline": BASE,
        "unpaid_order": replace(BASE, orders=(*BASE.orders,
            Order(3, 1, 5_000, "pending", "2026-01-12", None))),
        "second_item": replace(BASE, items=(*BASE.items, (3, 1, "A"))),
        "same_amount_different_order": replace(BASE, orders=(
            BASE.orders[0], replace(BASE.orders[1], amount_cents=10_000))),
        "unknown_customer": replace(BASE,
            orders=(*BASE.orders, Order(3, None, 5_000, "paid", "2026-01-12", "2026-01-12")),
            items=(*BASE.items, (3, 3, "C"))),
    }
    candidates = {
        "correct": "SELECT SUM(amount_cents) FROM orders WHERE status = 'paid'",
        "missing_required_status": "SELECT SUM(amount_cents) FROM orders",
        "join_items": "SELECT SUM(o.amount_cents) FROM orders o JOIN items i ON o.id=i.order_id WHERE o.status='paid'",
        "sum_distinct_amount": "SELECT SUM(DISTINCT amount_cents) FROM orders WHERE status='paid'",
        "inner_customer": "SELECT SUM(o.amount_cents) FROM orders o JOIN customers c ON o.customer_id=c.id WHERE o.status='paid'",
    }
    matrix = {}
    for fixture_name, fixture in fixtures.items():
        connection = connect(fixture)
        expected = sum(order.amount_cents for order in fixture.orders if order.status == "paid")
        actual = {name: value(connection, sql) for name, sql in candidates.items()}
        checked = run_plan(connection, Plan())["rows"][0]
        assert checked["value"] == str(expected)
        assert checked["status"] == "complete"
        matrix[fixture_name] = {
            "expected_cents": expected,
            "values_cents": actual,
            "incorrect": [name for name, result in actual.items() if result != expected],
        }
        connection.close()
    assert not matrix["baseline"]["incorrect"]
    for candidate in set(candidates) - {"correct"}:
        assert any(candidate in case["incorrect"] for case in matrix.values())
    return matrix


def unsupported_plan_experiment():
    rejected = {}
    examples = {
        "order_amount_by_item_category": (replace(Plan(), group_by="category"), {}),
        "unknown_metric": (replace(Plan(), metric="profit"), {}),
        "stale_definition": (replace(Plan(), catalog_version=2), {}),
        "unknown_cardinality": (replace(Plan(), group_by="country"), {"customer_key_verified": False}),
        "one_period_boundary": (replace(Plan(), start="2026-01-01"), {}),
        "invalid_period": (replace(Plan(), start="2026-02-01", end="2026-01-01"), {}),
    }
    for name, (plan, kwargs) in examples.items():
        try:
            compile_plan(plan, **kwargs)
        except UnsupportedPlan as error:
            rejected[name] = str(error)
        else:
            raise AssertionError(name)
    try:
        Plan(metric="paid_revenue", category="A")
    except TypeError:
        rejected["unknown_filter_slot"] = "rejected_not_silently_dropped"
    else:
        raise AssertionError("Unknown filter was ignored")
    connection = connect(BASE)
    hostile_country = "RU' OR 1=1 --"
    result = run_plan(connection, Plan(country=hostile_country))
    assert result["parameters"]["country"] == hostile_country
    assert hostile_country not in result["sql"]
    assert result["rows"][0]["entity_count"] == 0
    connection.close()
    return {"rejected": rejected, "bound_filter_literal": result}


def non_additive_experiment():
    fixture = replace(BASE,
        orders=(
            replace(BASE.orders[0], amount_cents=10_000),
            replace(BASE.orders[1], amount_cents=10_000),
            Order(3, 1, 90_000, "paid", "2026-02-01", "2026-02-01"),
        ),
        items=((1, 1, "A"), (2, 1, "B"), (3, 2, "A"), (4, 3, "A")),
    )
    connection = connect(fixture)
    category_rows = list(connection.execute(
        "SELECT i.category, SUM(o.amount_cents) FROM orders o "
        "JOIN (SELECT DISTINCT order_id, category FROM items) i ON o.id=i.order_id "
        "GROUP BY i.category ORDER BY i.category"
    ))
    total = value(connection, "SELECT SUM(amount_cents) FROM orders")
    assert total == 110_000
    assert sum(row[1] for row in category_rows) == 120_000
    monthly = run_plan(connection, Plan(metric="paid_average_order", group_by="month"))
    overall = run_plan(connection, Plan(metric="paid_average_order"))
    wrong_average = sum(Decimal(row["value"]) for row in monthly["rows"]) / Decimal(len(monthly["rows"]))
    assert wrong_average == Decimal(50_000)
    assert Decimal(overall["rows"][0]["value"]) == Decimal(110_000) / Decimal(3)
    connection.close()
    misleading = replace(BASE,
        orders=tuple(replace(order, amount_cents=10_000) for order in BASE.orders),
        items=((1, 1, "A"), (2, 1, "B")),
    )
    connection = connect(misleading)
    reconciled_groups = list(connection.execute(
        "SELECT i.category, SUM(o.amount_cents) FROM orders o "
        "JOIN items i ON o.id=i.order_id GROUP BY i.category ORDER BY i.category"
    ))
    reconciled_total = value(connection, "SELECT SUM(amount_cents) FROM orders")
    represented_entities = value(connection,
        "SELECT COUNT(DISTINCT o.id) FROM orders o JOIN items i ON o.id=i.order_id")
    assert sum(row[1] for row in reconciled_groups) == reconciled_total == 20_000
    assert represented_entities == 1
    connection.close()
    return {
        "order_total_cents": total,
        "order_totals_touching_each_category_cents": category_rows,
        "sum_of_nonexclusive_categories_cents": sum(row[1] for row in category_rows),
        "monthly_averages_cents": monthly["rows"],
        "incorrect_mean_of_monthly_averages_cents": str(wrong_average),
        "correct_total_average_cents": overall["rows"][0]["value"],
        "reconciliation_is_not_sufficient": {
            "category_values_cents": reconciled_groups,
            "total_cents": reconciled_total,
            "represented_order_ids": represented_entities,
            "actual_order_ids": 2,
            "limitation": "Duplicating one order and dropping another can accidentally preserve the grand total.",
        },
    }


def null_experiment():
    fixtures = {
        "empty": replace(BASE, orders=(), items=()),
        "all_unknown": replace(BASE, orders=tuple(replace(order, amount_cents=None) for order in BASE.orders)),
        "partly_unknown": replace(BASE, orders=(BASE.orders[0], replace(BASE.orders[1], amount_cents=None))),
        "known_zero": replace(BASE, orders=tuple(replace(order, amount_cents=0) for order in BASE.orders)),
    }
    output = {}
    for name, fixture in fixtures.items():
        connection = connect(fixture)
        raw = connection.execute(
            "SELECT SUM(amount_cents), AVG(amount_cents), COUNT(*), COUNT(amount_cents) FROM orders"
        ).fetchone()
        output[name] = {
            "raw_sum_avg_count_all_count_known": raw,
            "checked_revenue": run_plan(connection, Plan())["rows"],
            "checked_count": run_plan(connection, Plan(metric="paid_orders"))["rows"],
        }
        connection.close()
    assert output["empty"]["checked_revenue"][0]["status"] == "no_matching_rows"
    assert output["all_unknown"]["checked_revenue"][0]["status"] == "incomplete_amounts"
    assert output["partly_unknown"]["checked_revenue"][0]["value"] is None
    assert output["known_zero"]["checked_revenue"][0]["value"] == "0"
    return output


def previous_month(as_of, zone):
    local = as_of.astimezone(zone)
    end = date(local.year, local.month, 1)
    previous_day = end - timedelta(days=1)
    return date(previous_day.year, previous_day.month, 1).isoformat(), end.isoformat()


def clarification_experiment():
    january = {"start": "2026-01-01", "end": "2026-02-01"}
    alternatives = [Plan(date_basis=basis, **january) for basis in ("created_on", "paid_on")]
    output = {}
    late_payment = replace(BASE, orders=(*BASE.orders,
        Order(3, 1, 5_000, "paid", "2026-01-31", "2026-02-01")))
    for name, fixture in {"coinciding_dates": BASE, "late_payment": late_payment}.items():
        connection = connect(fixture)
        results = [run_plan(connection, plan) for plan in alternatives]
        output[name] = {result["plan"]["date_basis"]: result["rows"][0]["value"] for result in results}
        connection.close()
    assert len(set(output["coinciding_dates"].values())) == 1
    assert len(set(output["late_payment"].values())) == 2
    initial = Plan(country="RU", group_by="month", **january)
    refined = replace(initial, date_basis="created_on")
    diff = {key: (asdict(initial)[key], asdict(refined)[key])
            for key in asdict(initial) if asdict(initial)[key] != asdict(refined)[key]}
    assert set(diff) == {"date_basis"}
    as_of = datetime.fromisoformat("2026-03-01T00:30:00+03:00")
    fixed_moscow_offset = timezone(timedelta(hours=3))
    output["plan_patch"] = diff
    output["previous_month_at_same_instant"] = {
        "as_of": as_of.isoformat(),
        "UTC": previous_month(as_of, timezone.utc),
        "fixed_UTC_plus_3": previous_month(as_of, fixed_moscow_offset),
    }
    assert output["previous_month_at_same_instant"]["UTC"] != output["previous_month_at_same_instant"]["fixed_UTC_plus_3"]
    return output


def randomized_plan_experiment():
    randomizer = random.Random(739_021)
    checked = 0
    for sample in range(60):
        orders = tuple(Order(
            index + 1, randomizer.choice((1, 2, None)), randomizer.randrange(0, 20_001),
            randomizer.choice(("paid", "pending")), "2026-01-10", "2026-01-10",
        ) for index in range(randomizer.randrange(1, 20)))
        fixture = Fixture(orders, tuple(
            (index + 1, order_id, "A")
            for index, order_id in enumerate(
                order.id for order in orders for _ in range(randomizer.randrange(1, 5))
            )
        ))
        connection = connect(fixture)
        countries = dict(fixture.customers)
        for metric in METRICS:
            for country in (None, "RU"):
                selected = [order for order in orders if order.status == "paid"
                            and (country is None or countries.get(order.customer_id) == country)]
                result = run_plan(connection, Plan(metric=metric, country=country))["rows"][0]
                if metric == "paid_orders":
                    expected = Decimal(len(selected))
                elif not selected:
                    expected = None
                elif metric == "paid_revenue":
                    expected = Decimal(sum(order.amount_cents for order in selected))
                else:
                    expected = Decimal(sum(order.amount_cents for order in selected)) / Decimal(len(selected))
                assert result["value"] == (None if expected is None else str(expected)), (sample, metric, country)
                checked += 1
        connection.close()
    return {"seed": 739_021, "fixtures": 60, "plan_result_comparisons": checked}


def counter_review_experiment():
    fixture = replace(BASE, orders=(*BASE.orders,
        Order(3, 1, 5_000, "paid", "2026-01-12", None)))
    connection = connect(fixture)
    period = Plan(start="2026-01-01", end="2026-02-01")
    period_result = run_plan(connection, period)
    unknown_dates = value(connection,
        "SELECT COUNT(*) FROM orders WHERE status='paid' AND paid_on IS NULL")
    assert period_result["rows"][0]["status"] == "complete"
    assert unknown_dates == 1
    connection.close()
    partial_dimension = "country"
    submitted_question = "Paid revenue by country excluding refunds"
    syntactically_valid = Plan(group_by=partial_dimension)
    compile_plan(syntactically_valid)
    return {
        "missing_date_hidden_by_range_filter": {
            "prototype_result": period_result,
            "paid_orders_without_date": unknown_dates,
            "limitation": "Complete here means amount completeness inside selected rows; population completeness is not proved.",
        },
        "natural_language_omission": {
            "question": submitted_question,
            "plan_accepted": asdict(syntactically_valid),
            "limitation": "A valid plan does not prove that the natural-language requirement about refunds was represented.",
        },
        "snapshot_identity": {
            "limitation": "The prototype plan hash has no data revision; equal plan IDs do not identify equal results.",
        },
    }


def current_validator_experiment():
    root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [str(root), str(root / "packages/sql_agent/src"), str(root / "packages/sql_agent/tests"), str(root / ".tools" / "test-packages")]
    from sql_agent.contracts import Column, ForeignKey, TableRef, TableSchema
    from sql_agent.sql.grammar import SQLGrammarBuilder
    from sql_agent.sql.validation import SQLValidator
    from sql_agent_tests.gbnf_support import GrammarRecognizer

    orders = TableSchema(TableRef("main", "orders"), (
        Column("id", "INTEGER", False), Column("amount_cents", "INTEGER"),
        Column("status", "TEXT", False),
    ), primary_key=("id",))
    items = TableSchema(TableRef("main", "items"), (
        Column("id", "INTEGER", False), Column("order_id", "INTEGER", False),
    ), primary_key=("id",), foreign_keys=(ForeignKey(("order_id",), orders.ref, ("id",)),))
    sql = (
        'SELECT SUM(t1."amount_cents") FROM "main"."orders" AS t1 '
        'JOIN "main"."items" AS t0 ON t1."id" = t0."order_id" '
        'WHERE t1."status" = \'paid\''
    )
    grammar_accepted = GrammarRecognizer(SQLGrammarBuilder.build([orders, items])).accepts(sql)
    assert grammar_accepted
    try:
        normalized = SQLValidator().validate(sql, [orders, items])
    except Exception as error:
        return {"grammar_accepted": True, "ast_check": "not_completed", "reason": str(error)}
    connection = connect(replace(BASE, items=(*BASE.items, (3, 1, "A"))))
    actual = value(connection, normalized)
    expected = value(connection, "SELECT SUM(amount_cents) FROM orders WHERE status='paid'")
    connection.close()
    assert actual == 40_000 and expected == 30_000
    return {"grammar_accepted": True, "ast_accepted": True, "actual_cents": actual,
            "expected_cents": expected, "normalized_sql": normalized}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    output = {
        "scope": "Synthetic SQLite in-memory and Python experiments; no LLM or PostgreSQL execution.",
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "mutation_matrix": mutation_experiment(),
        "unsupported_plans": unsupported_plan_experiment(),
        "non_additive": non_additive_experiment(),
        "null_states": null_experiment(),
        "clarification": clarification_experiment(),
        "randomized_plans": randomized_plan_experiment(),
        "counter_review": counter_review_experiment(),
        "current_validator": current_validator_experiment(),
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2)
    if options.output:
        directory = Path(__file__).resolve().parent
        destination = options.output.resolve()
        if destination.parent != directory:
            raise ValueError("Output must stay in this research directory")
        destination.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
