import argparse
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import random
import sqlite3
import sys


@dataclass(frozen=True)
class Total:
    revenue_cents: int
    orders: int
    missing_amounts: int = 0
    complete: bool = True

    def validate(self):
        if not self.complete:
            raise ValueError("incomplete_result")
        if self.missing_amounts:
            raise ValueError("missing_amounts")
        if self.orders < 0 or (self.orders == 0 and self.revenue_cents != 0):
            raise ValueError("inconsistent_order_count")


def split_change(before: Total, after: Total):
    before.validate()
    after.validate()
    delta = after.revenue_cents - before.revenue_cents
    percentage = Fraction(delta, before.revenue_cents) * 100 if before.revenue_cents > 0 else None
    if not before.orders or not after.orders:
        return {"mode": "zero_order_period", "delta_cents": delta, "percentage": percentage}
    old_average = Fraction(before.revenue_cents, before.orders)
    new_average = Fraction(after.revenue_cents, after.orders)
    count_part = (after.orders - before.orders) * (old_average + new_average) / 2
    average_part = (new_average - old_average) * (before.orders + after.orders) / 2
    return {
        "mode": "symmetric_two_factor",
        "delta_cents": delta,
        "percentage": percentage,
        "order_count_part_cents": count_part,
        "average_order_part_cents": average_part,
    }


def segment_changes(before, after, *, complete: bool):
    if not complete:
        raise ValueError("incomplete_segments")
    changes = [
        {"kind": "segment", "segment": name, "delta_cents": after.get(name, 0) - before.get(name, 0)}
        for name in before.keys() | after.keys()
    ]
    return sorted(changes, key=lambda value: (
        -abs(value["delta_cents"]), value["segment"] is not None, value["segment"] or "",
    ))


def display_top(changes, limit):
    if limit < 1:
        raise ValueError("invalid_display_limit")
    selected = changes[:limit]
    remaining = changes[limit:]
    if remaining:
        selected = [*selected, {
            "kind": "remainder",
            "delta_cents": sum(value["delta_cents"] for value in remaining),
            "segments": len(remaining),
        }]
    return selected


def rejected(operation, expected):
    try:
        operation()
    except ValueError as error:
        assert str(error) == expected
        return expected
    raise AssertionError("Invalid comparison was accepted")


def arithmetic_evidence():
    before, after = Total(2_400_000, 20), Total(1_500_000, 15)
    split = split_change(before, after)
    assert split["delta_cents"] == -900_000
    assert split["order_count_part_cents"] == -550_000
    assert split["average_order_part_cents"] == -350_000
    regions = segment_changes(
        {"Moscow": 1_500_000, "Saint Petersburg": 900_000},
        {"Moscow": 1_200_000, "Saint Petersburg": 300_000},
        complete=True,
    )
    assert sum(item["delta_cents"] for item in regions) == split["delta_cents"]
    top_one = display_top(regions, 1)
    assert sum(item["delta_cents"] for item in top_one) == split["delta_cents"]
    assert regions[0]["delta_cents"] != split["delta_cents"]
    nullable_segments = segment_changes({None: 100, "other_segments": 100}, {None: 0, "other_segments": 200}, complete=True)
    assert nullable_segments[0]["segment"] is None
    labelled_remainder = display_top(list(reversed(nullable_segments)), 1)
    assert labelled_remainder[0]["kind"] == "segment"
    assert labelled_remainder[0]["segment"] == "other_segments"
    assert labelled_remainder[1]["kind"] == "remainder"

    old_average = Fraction(before.revenue_cents, before.orders)
    new_average = Fraction(after.revenue_cents, after.orders)
    order_first = [
        (after.orders - before.orders) * old_average,
        after.orders * (new_average - old_average),
    ]
    average_first = [
        (after.orders - before.orders) * new_average,
        before.orders * (new_average - old_average),
    ]
    assert order_first != average_first
    assert sum(order_first) == sum(average_first) == split["delta_cents"]

    random_source = random.Random(41)
    for _ in range(1_000):
        left = Total(random_source.randint(-50_000_000, 50_000_000), random_source.randint(1, 1_000))
        right = Total(random_source.randint(-50_000_000, 50_000_000), random_source.randint(1, 1_000))
        actual = split_change(left, right)
        reverse = split_change(right, left)
        assert actual["order_count_part_cents"] + actual["average_order_part_cents"] == actual["delta_cents"]
        assert reverse["order_count_part_cents"] == -actual["order_count_part_cents"]
        assert reverse["average_order_part_cents"] == -actual["average_order_part_cents"]

    old_rates = {"A": (9, 10), "B": (20, 100)}
    new_rates = {"A": (80, 100), "B": (1, 10)}
    for group in old_rates:
        assert Fraction(*new_rates[group]) < Fraction(*old_rates[group])
    old_total_rate = Fraction(sum(item[0] for item in old_rates.values()), sum(item[1] for item in old_rates.values()))
    new_total_rate = Fraction(sum(item[0] for item in new_rates.values()), sum(item[1] for item in new_rates.values()))
    assert new_total_rate > old_total_rate

    zero_start = split_change(Total(0, 0), Total(100_000, 4))
    assert zero_start["percentage"] is None
    assert "order_count_part_cents" not in zero_start
    no_orders = split_change(Total(100_000, 4), Total(0, 0))
    negative_start = split_change(Total(-20_000, 2), Total(10_000, 2))
    assert negative_start["percentage"] is None

    return {
        "basis": "Synthetic inputs; exact rational arithmetic; no LLM, PostgreSQL or causal inference",
        "revenue_comparison": split,
        "region_contributions": regions,
        "top_one_and_remainder": top_one,
        "nullable_segments": nullable_segments,
        "real_remainder_like_name": labelled_remainder,
        "order_dependent_decompositions": {"count_first": order_first, "average_first": average_first},
        "simpson_counterexample": {
            "before": old_rates, "after": new_rates,
            "overall_before": old_total_rate, "overall_after": new_total_rate,
            "every_segment_declines_but_total_rate_rises": True,
        },
        "zero_start": zero_start,
        "zero_end": no_orders,
        "negative_start": negative_start,
        "rejections": [
            rejected(lambda: split_change(Total(100, 1, complete=False), after), "incomplete_result"),
            rejected(lambda: split_change(Total(100, 1, missing_amounts=1), after), "missing_amounts"),
            rejected(lambda: split_change(Total(100, 0), after), "inconsistent_order_count"),
            rejected(lambda: segment_changes({"A": 100}, {"A": 200}, complete=False), "incomplete_segments"),
        ],
        "randomized_cases": 1_000,
        "checks": "exact additivity and reversal symmetry passed for every randomized case",
    }


def project_sql_evidence():
    root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [str(root), str(root / "packages/sql_agent/src"), str(root / "packages/sql_agent/tests"), str(root / ".tools" / "test-packages")]
    import sqlglot
    from sql_agent.contracts import Column, TableRef, TableSchema
    from sql_agent.sql.grammar import SQLGrammarBuilder
    from sql_agent.sql.validation import SQLValidator
    from sql_agent_tests.gbnf_support import GrammarRecognizer

    tables = (TableSchema(TableRef("sales", "orders"), (
        Column("id", "integer", nullable=False), Column("region", "text", nullable=False),
        Column("paid_at", "date", nullable=False), Column("amount_cents", "integer", nullable=False),
        Column("status", "text", nullable=False), Column("currency", "text", nullable=False),
    ), primary_key=("id",)),)
    sql = """SELECT t0."region",
SUM(CASE WHEN t0."paid_at" < DATE '2026-06-01' THEN t0."amount_cents" ELSE 0 END) AS "before_cents",
SUM(CASE WHEN t0."paid_at" >= DATE '2026-06-01' THEN t0."amount_cents" ELSE 0 END) AS "after_cents",
COUNT(CASE WHEN t0."paid_at" < DATE '2026-06-01' THEN 1 ELSE NULL END) AS "before_orders",
COUNT(CASE WHEN t0."paid_at" >= DATE '2026-06-01' THEN 1 ELSE NULL END) AS "after_orders"
FROM "sales"."orders" AS t0
WHERE t0."status" = 'paid' AND t0."currency" = 'RUB'
AND t0."paid_at" >= DATE '2026-05-01' AND t0."paid_at" < DATE '2026-07-01'
GROUP BY t0."region"
ORDER BY t0."region"
"""
    assert GrammarRecognizer(SQLGrammarBuilder.build(tables)).accepts(sql)
    normalized = SQLValidator().validate(sql, tables)
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("ATTACH DATABASE ':memory:' AS sales")
        connection.execute("CREATE TABLE sales.orders (id INTEGER PRIMARY KEY, region TEXT, paid_at TEXT, amount_cents INTEGER, status TEXT, currency TEXT)")
        rows = []
        for region, day, amount, count in (
            ("Moscow", "2026-05-15", 150_000, 10),
            ("Saint Petersburg", "2026-05-15", 90_000, 10),
            ("Moscow", "2026-06-15", 120_000, 10),
            ("Saint Petersburg", "2026-06-15", 60_000, 5),
        ):
            for _ in range(count):
                rows.append((len(rows) + 1, region, day, amount, "paid", "RUB"))
        rows.extend([
            (36, "Moscow", "2026-06-16", 999_999, "pending", "RUB"),
            (37, "Moscow", "2026-06-16", 999_999, "paid", "USD"),
            (38, "Moscow", "2026-07-01", 999_999, "paid", "RUB"),
        ])
        connection.executemany("INSERT INTO sales.orders VALUES (?, ?, ?, ?, ?, ?)", rows)
        sqlite_sql = sqlglot.transpile(normalized, read="postgres", write="sqlite")[0]
        actual = connection.execute(sqlite_sql).fetchall()
    finally:
        connection.close()
    assert actual == [("Moscow", 1_500_000, 1_200_000, 10, 10), ("Saint Petersburg", 900_000, 300_000, 10, 5)]
    return {
        "grammar_recognizer": "accepted by current project GBNF",
        "ast_validator": "accepted by current project SQLValidator",
        "execution": "SQLite in-memory after SQLGlot dialect conversion; PostgreSQL not executed",
        "sql": sql,
        "rows": actual,
        "input_rows": len(rows),
        "excluded_controls": ["unpaid order", "different currency", "upper date boundary"],
    }


def encode(value):
    if isinstance(value, Fraction):
        return str(value)
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-project-sql", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    evidence = arithmetic_evidence()
    if arguments.check_project_sql:
        evidence["project_sql"] = project_sql_evidence()
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2, default=encode) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(f"Evidence written to {arguments.output.name}")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
