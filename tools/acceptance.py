import argparse
from decimal import Decimal
import http.cookiejar
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.backup import Transport, read_environment


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.csrf = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, path, payload=None):
        headers = {"Origin": self.base, "X-CSRF-Token": self.csrf}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers
        )
        with self.opener.open(request, timeout=40) as response:
            return json.load(response)

    def wait(self, prefix, run_id):
        deadline = time.monotonic() + 900
        previous = None
        peak_api_seconds = 0
        while time.monotonic() < deadline:
            started = time.monotonic()
            run = self.call("GET", f"{prefix}/runs/{run_id}")
            self.call("GET", "/api/v1/auth/session")
            peak_api_seconds = max(peak_api_seconds, time.monotonic() - started)
            if run["stage"] != previous:
                print(f"AI stage: {run['stage']}", flush=True)
                previous = run["stage"]
            if run["status"] not in {"queued", "running", "cancel_requested"}:
                if run["status"] != "succeeded":
                    raise AssertionError(
                        f"AI did not complete: {run['status']}; {run.get('error')}"
                    )
                print(
                    f"Peak run and session API round-trip during inference: {peak_api_seconds:.3f}s",
                    flush=True,
                )
                return run
            time.sleep(2)
        raise TimeoutError("AI request exceeded acceptance deadline")


def docker_fixture(values, env_file):
    transport = Transport(values, env_file)
    suffix = secrets.token_hex(6)
    database, account = "acceptance_" + suffix, "acceptance_reader_" + suffix
    password = secrets.token_urlsafe(32)
    for statement in (
        f"CREATE ROLE {account} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
        f"CREATE DATABASE {database}",
    ):
        transport.pg(
            "psql", ["-X", "-v", "ON_ERROR_STOP=1", "-d", values["DB_NAME"], "-c", statement]
        )
    sql = f"""
CREATE SCHEMA reporting;
CREATE TABLE reporting.sales(id bigint PRIMARY KEY, store_code text NOT NULL, business_day date NOT NULL, net_revenue numeric(14,2) NOT NULL, internal_note text);
INSERT INTO reporting.sales VALUES
 (1,'A','2026-09-01',300000,'private'), (2,'B','2026-09-01',200000,'private'),
 (3,'A','2026-10-01',100000,'private'), (4,'A','2026-10-02',300000,'private'),
 (5,'B','2026-10-01',300000,'private'), (6,'C','2026-10-01',99999999,'private');
GRANT CONNECT ON DATABASE {database} TO {account};
GRANT USAGE ON SCHEMA reporting TO {account};
GRANT SELECT ON reporting.sales TO {account};
ALTER ROLE {account} IN DATABASE {database} SET default_transaction_read_only = on;
"""
    with tempfile.TemporaryFile() as source:
        source.write(sql.encode())
        source.seek(0)
        transport.pg("psql", ["-X", "-v", "ON_ERROR_STOP=1", "-d", database], source=source)
    return {
        "source": {
            "name": "Acceptance PostgreSQL",
            "host": "db",
            "port": 5432,
            "database": database,
            "username": account,
            "password": password,
            "schemas": ["reporting"],
            "ssl_mode": "disable",
        },
        "stores": [
            {"name": "Арбат", "code": "A", "city": "Москва"},
            {"name": "Невский", "code": "B", "city": "Санкт-Петербург"},
        ],
        "policies": {
            "tables": [
                {
                    "schema": "reporting",
                    "name": "sales",
                    "columns": ["id", "store_code", "business_day", "net_revenue"],
                    "store_column": "store_code",
                    "shared": False,
                }
            ]
        },
        "metric": {
            "key": "net_revenue",
            "name": "Выручка после возвратов",
            "description": "Сумма net_revenue в рублях по business_day. Возвраты уже учтены. Точка определяется store_code.",
            "unit": "RUB",
            "table_schema": "reporting",
            "table_name": "sales",
            "value_column": "net_revenue",
            "date_column": "business_day",
            "store_column": "store_code",
            "aggregation": "sum",
        },
        "expected": {"actual": "700000", "stores": {"A": "400000", "B": "300000"}},
    }


def verify_rows(run, expected):
    result = run["result"]
    assert run["sql"] and result["execution"]["sql"], "SQL evidence missing"
    assert not result["truncated"], "Unexpected result truncation"
    assert len(result["rows"]) == len(expected), result["rows"]
    observed = {}
    for row in result["rows"]:
        assert len(row) == 2, row
        code = next((value for value in row if value in expected), None)
        assert code is not None, row
        amount = next(value for value in row if value != code)
        observed[code] = Decimal(str(amount))
    assert observed == {key: Decimal(value) for key, value in expected.items()}, observed


def main():
    parser = argparse.ArgumentParser(
        description="Real AI acceptance against an isolated application"
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".runtime/compose.env")
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Acceptance requires an isolated loopback installation")
    client = Client(args.url)
    status = client.call("GET", "/api/v1/auth/status")
    values = read_environment(args.env_file)
    if args.credentials:
        credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
        session = client.call(
            "POST", "/api/v1/auth/login", {key: credentials[key] for key in ("email", "password")}
        )
    elif status["bootstrap_required"]:
        session = client.call(
            "POST",
            "/api/v1/auth/bootstrap",
            {
                "email": f"acceptance-{secrets.token_hex(6)}@example.test",
                "password": secrets.token_urlsafe(32),
                "name": "Acceptance owner",
                "workspace_name": "Acceptance network",
                "bootstrap_token": values["APP_BOOTSTRAP_TOKEN"],
            },
        )
    else:
        parser.error("Existing deployment requires explicit test credentials; no changes made")
    client.csrf = session["csrf_token"]
    prefix = f"/api/v1/workspaces/{session['workspaces'][0]['id']}"
    fixture = (
        json.loads(args.source_manifest.read_text(encoding="utf-8"))
        if args.source_manifest
        else docker_fixture(values, args.env_file)
    )
    stores = client.call("GET", prefix + "/stores")
    selected = []
    for item in fixture["stores"]:
        record = next((record for record in stores if record["code"] == item["code"]), None)
        selected.append(record or client.call("POST", prefix + "/stores", item))
    source = client.call("POST", prefix + "/sources", fixture["source"])
    checked = client.call("POST", prefix + f"/sources/{source['id']}/test")
    assert checked["status"] == "ready", checked.get("error")
    client.call("PUT", prefix + f"/sources/{source['id']}/policies", fixture["policies"])
    metric = client.call(
        "POST",
        prefix + "/metrics",
        {
            **fixture["metric"],
            "key": "acceptance_" + secrets.token_hex(5),
            "source_id": source["id"],
        },
    )
    overview = client.call(
        "GET",
        prefix + f"/overview?metric_id={metric['id']}&date_from=2026-10-01&date_to=2026-10-31",
    )
    assert Decimal(overview["totals"]["actual"]) == Decimal(fixture["expected"]["actual"])
    conversation = client.call(
        "POST", prefix + "/conversations", {"title": "Проверка настоящей модели"}
    )
    run = client.call(
        "POST",
        prefix + f"/conversations/{conversation['id']}/messages",
        {
            "question": "Покажи выручку по каждой точке за октябрь 2026 года. В таблице нужны код точки и сумма выручки.",
            "source_id": source["id"],
            "store_ids": [item["id"] for item in selected],
            "metric_id": metric["id"],
            "date_from": "2026-10-01",
            "date_to": "2026-10-31",
            "version": conversation["version"],
            "idempotency_key": secrets.token_hex(16),
        },
    )
    run = client.wait(prefix, run["id"])
    verify_rows(run, fixture["expected"]["stores"])
    report = client.call(
        "POST", prefix + "/reports", {"title": "Контрольная выручка", "run_id": run["id"]}
    )
    refreshed = client.call(
        "POST",
        prefix + f"/reports/{report['id']}/refresh",
        {"idempotency_key": secrets.token_hex(16)},
    )
    refreshed = client.wait(prefix, refreshed["id"])
    verify_rows(refreshed, fixture["expected"]["stores"])
    assert refreshed["id"] != run["id"]
    saved = client.call("GET", prefix + f"/runs/{run['id']}")
    assert saved["result"] == run["result"], "Refresh modified the original result"
    client.call("POST", "/api/v1/auth/logout")
    print(
        "Real AI acceptance passed: PostgreSQL source, hidden rows/columns, metric, overview, worker, SQL, exact amounts, immutable report refresh.",
        flush=True,
    )


if __name__ == "__main__":
    main()
