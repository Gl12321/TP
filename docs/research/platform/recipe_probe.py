import argparse
import json
import platform
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

import sqlglot
from sqlglot import exp

from sql_agent.contracts import QueryError, Column, TableRef, TableSchema
from sql_agent.sql.validation import SQLValidator


TABLE = TableSchema(
    TableRef("sales", "orders"),
    tuple(Column(name, kind) for name, kind in (
        ("id", "INTEGER"), ("day", "DATE"), ("city", "TEXT"),
        ("channel", "TEXT"), ("status", "TEXT"), ("amount_cents", "BIGINT"),
    )),
    primary_key=("id",),
)
VALIDATOR = SQLValidator()
DIMENSIONS = frozenset({"city", "channel"})


def column(name):
    return exp.column(name, table="t0", quoted=True)


def compile_recipe(start, end, dimension="city", city=None):
    for value in (start, end):
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("Expected an ISO date")
    if start >= end:
        raise ValueError("Expected a nonempty half-open date interval")
    if dimension not in DIMENSIONS:
        raise ValueError("Unsupported dimension")
    if city is not None and (not isinstance(city, str) or len(city) > 200):
        raise ValueError("Expected a city name")
    predicates = [
        exp.EQ(this=column("status"), expression=exp.Literal.string("paid")),
        exp.GTE(this=column("day"), expression=exp.Literal.string(start)),
        exp.LT(this=column("day"), expression=exp.Literal.string(end)),
    ]
    if city is not None:
        predicates.append(exp.EQ(this=column("city"), expression=exp.Literal.string(city)))
    source = exp.Table(
        this=exp.to_identifier("orders", quoted=True),
        db=exp.to_identifier("sales", quoted=True),
        alias=exp.TableAlias(this=exp.to_identifier("t0", quoted=True)),
    )
    query = (
        exp.select(
            column(dimension),
            exp.alias_(exp.Sum(this=column("amount_cents")), "value", quoted=True),
        )
        .from_(source)
        .where(exp.and_(*predicates))
        .group_by(column(dimension))
        .order_by(column(dimension))
    )
    return VALIDATOR.validate(query.sql(dialect="postgres"), [TABLE])


def rows_for_probe(count):
    cities = ("Moscow", "Kazan", "O'Reilly", "'); DROP TABLE orders; --")
    origin = date(2026, 1, 1)
    for i in range(count):
        yield (
            i, (origin + timedelta(days=i % 365)).isoformat(), cities[i % 4],
            ("online", "shop")[i % 2], "paid" if i % 7 else "cancelled",
            None if i % 19 == 0 else 100 + (i * 97) % 100_000,
        )


def expected(rows, start, end, dimension="city", city=None):
    grouped = defaultdict(list)
    index = {"city": 2, "channel": 3}[dimension]
    for row in rows:
        if row[4] == "paid" and start <= row[1] < end and (city is None or row[2] == city):
            grouped[row[index]].append(row[5])
    result = []
    for key, values in sorted(grouped.items()):
        present = [value for value in values if value is not None]
        result.append((key, sum(present) if present else None))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = list(rows_for_probe(50_000))
    cases = [
        {"start": "2026-01-01", "end": "2027-01-01"},
        {"start": "2026-02-01", "end": "2026-03-01"},
        {"start": "2026-01-01", "end": "2026-01-02"},
        {"start": "2027-01-01", "end": "2028-01-01"},
        {"start": "2026-01-01", "end": "2027-01-01", "dimension": "channel"},
    ]
    cases.extend({
        "start": "2026-01-01", "end": "2027-01-01", "city": city,
    } for city in ("Moscow", "O'Reilly", "'); DROP TABLE orders; --", "Absent"))
    invalid = [
        {"start": "2026-02-30", "end": "2027-01-01"},
        {"start": "2026-01-01", "end": "2026-01-01"},
        {"start": "2026-01-01", "end": "2027-01-01", "dimension": "category"},
        {"start": "20260101", "end": "2027-01-01"},
    ]
    with sqlite3.connect(":memory:") as connection:
        connection.execute("ATTACH DATABASE ':memory:' AS sales")
        connection.execute(
            "CREATE TABLE sales.orders (id INTEGER PRIMARY KEY, day TEXT, city TEXT, "
            "channel TEXT, status TEXT, amount_cents INTEGER)"
        )
        connection.executemany("INSERT INTO sales.orders VALUES (?, ?, ?, ?, ?, ?)", rows)
        for case in cases:
            sql = compile_recipe(**case)
            sqlite_sql = sqlglot.transpile(sql, read="postgres", write="sqlite")[0]
            actual = connection.execute(sqlite_sql).fetchall()
            assert actual == expected(rows, **case), case
        for case in invalid:
            try:
                compile_recipe(**case)
            except ValueError:
                continue
            raise AssertionError(case)
        try:
            VALIDATOR.validate(
                "SELECT t0.city FROM sales.orders AS t0 WHERE t0.city = :city", [TABLE],
            )
        except QueryError as error:
            placeholder_rejection = error.code
        else:
            raise AssertionError("Expected current validator to reject placeholders")
        compile_ms, execute_ms = [], []
        for _ in range(30):
            begin = perf_counter()
            sql = compile_recipe(**cases[0])
            compile_ms.append((perf_counter() - begin) * 1_000)
            sqlite_sql = sqlglot.transpile(sql, read="postgres", write="sqlite")[0]
            begin = perf_counter()
            connection.execute(sqlite_sql).fetchall()
            execute_ms.append((perf_counter() - begin) * 1_000)
        assert connection.execute("SELECT COUNT(*) FROM sales.orders").fetchone()[0] == len(rows)
    report = {
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "sqlglot": sqlglot.__version__, "rows": len(rows),
        "correctness_cases": len(cases), "rejected_inputs": len(invalid),
        "placeholder_rejection": placeholder_rejection, "llm_calls": 0,
        "warm_repeats": len(compile_ms),
        "compile_validate_median_ms": round(statistics.median(compile_ms), 3),
        "compile_validate_max_ms": round(max(compile_ms), 3),
        "sqlite_execute_median_ms": round(statistics.median(execute_ms), 3),
        "sqlite_execute_max_ms": round(max(execute_ms), 3),
        "scope": "Synthetic one-table recipe; not a PostgreSQL or model benchmark",
    }
    output = json.dumps(report, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
