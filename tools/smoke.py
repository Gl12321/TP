import argparse
import http.cookiejar
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.request
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke check against a new isolated Razbor deployment."
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".runtime" / "compose.env",
    )
    args = parser.parse_args()
    base = args.url.rstrip("/")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    csrf = ""

    def call(method, path, payload=None):
        headers = {"Origin": base}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    status = call("GET", "/api/v1/auth/status")
    if not status["bootstrap_required"]:
        raise SystemExit(
            "Smoke requires a fresh isolated deployment; existing data was not changed."
        )
    settings = dict(
        line.split("=", 1)
        for line in args.env_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    session = call(
        "POST",
        "/api/v1/auth/bootstrap",
        {
            "email": f"smoke-{uuid4().hex}@example.test",
            "password": secrets.token_urlsafe(32),
            "name": "Smoke owner",
            "workspace_name": "Smoke network",
            "bootstrap_token": settings["APP_BOOTSTRAP_TOKEN"],
        },
    )
    csrf = session["csrf_token"]
    workspace = session["workspaces"][0]["id"]
    prefix = f"/api/v1/workspaces/{workspace}"
    store = call(
        "POST",
        prefix + "/stores",
        {"name": "Smoke store", "code": "smoke-001", "city": "Test city"},
    )
    stores = call("GET", prefix + "/stores")
    assert any(item["id"] == store["id"] for item in stores)
    conversation = call("POST", prefix + "/conversations", {"title": "Smoke question"})
    assert conversation["id"]
    case = call(
        "POST", prefix + "/cases", {"title": "Smoke discussion", "store_ids": [store["id"]]}
    )
    call("POST", prefix + f"/cases/{case['id']}/comments", {"body": "Smoke comment"})
    detail = call("GET", prefix + f"/cases/{case['id']}")
    assert any(item["body"] == "Smoke comment" for item in detail["comments"])
    overview = call("GET", prefix + "/overview?date_from=2026-01-01&date_to=2026-01-31")
    assert overview["totals"]["actual"] is None
    with opener.open(base + "/", timeout=10) as response:
        html = response.read().decode()
        assert '<div id="root">' in html or '<div id="root" />' in html
    call("POST", "/api/v1/auth/logout")
    try:
        call("GET", "/api/v1/auth/session")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Logout did not revoke the session")
    print(
        "Smoke passed: setup, session, scoped stores, conversation, discussion, empty analytics, UI, logout."
    )


if __name__ == "__main__":
    main()
