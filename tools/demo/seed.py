from contextlib import AsyncExitStack
from decimal import Decimal

from tools.demo.state import DemoError


async def request(client, method, path, **kwargs):
    response = await client.request(method, "/api/v1" + path, **kwargs)
    if not response.is_success:
        try:
            code = response.json().get("error", {}).get("code", "unknown")
        except ValueError:
            code = "unknown"
        raise DemoError(f"Подготовка demo: {method} {path}: HTTP {response.status_code}, {code}.")
    return response.json()


async def populate_onboarding(app, state, accounts):
    import httpx

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=accounts["url"], timeout=30
        ) as client,
    ):
        director = accounts["accounts"][0]
        session = await request(
            client,
            "POST",
            "/auth/bootstrap",
            json={
                **{key: director[key] for key in ("email", "name", "password")},
                "workspace_name": "Демонстрация · новая сеть",
                "bootstrap_token": state["bootstrap_token"],
            },
        )
        state["workspace_id"] = accounts["workspace_id"] = session["workspaces"][0]["id"]
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        await request(client, "POST", "/auth/logout")


async def populate(app, state, accounts, dataset):
    import httpx

    async with app.router.lifespan_context(app), AsyncExitStack() as stack:
        clients = {}
        for account in accounts["accounts"]:
            clients[account["role"]] = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=accounts["url"],
                    timeout=30,
                )
            )
        owner = clients["director"]
        director = accounts["accounts"][0]
        session = await request(
            owner,
            "POST",
            "/auth/bootstrap",
            json={
                **{key: director[key] for key in ("email", "name", "password")},
                "workspace_name": "Демонстрация · сеть Разбор",
                "bootstrap_token": state["bootstrap_token"],
            },
        )
        owner.headers["X-CSRF-Token"] = session["csrf_token"]
        workspace = session["workspaces"][0]["id"]
        prefix = "/workspaces/" + workspace
        users = {"director": session["user"]["id"]}
        stores = {}
        for item in dataset["stores"]:
            created = await request(owner, "POST", prefix + "/stores", json=item)
            stores[item["code"]] = created["id"]
        for account in accounts["accounts"][1:]:
            role = account["role"]
            scope = {
                "regional_manager": [stores["A"], stores["B"]],
                "franchise_owner": [stores["B"]],
                "store_manager": [stores["A"]],
                "analyst": [],
                "admin": [],
            }[role]
            member = await request(
                owner,
                "POST",
                prefix + "/members",
                json={
                    **account,
                    "all_stores": role in {"analyst", "admin"},
                    "store_ids": scope,
                    "data_access": role != "admin",
                },
            )
            users[role] = member["user_id"]
            account["store_codes"] = [
                code for code, identifier in stores.items() if identifier in scope
            ]
            account["data_access"] = role != "admin"
            session = await request(
                clients[role],
                "POST",
                "/auth/login",
                json={key: account[key] for key in ("email", "password")},
            )
            clients[role].headers["X-CSRF-Token"] = session["csrf_token"]
        source = await request(owner, "POST", prefix + "/sources", json=dataset["source"])
        source_path = prefix + "/sources/" + source["id"]
        checked = await request(owner, "POST", source_path + "/test")
        if checked["status"] != "ready":
            raise DemoError("Тестовый источник не прошёл проверку подключения и каталога.")
        await request(owner, "PUT", source_path + "/policies", json=dataset["policies"])
        metric = await request(
            clients["analyst"],
            "POST",
            prefix + "/metrics",
            json={**dataset["metric"], "source_id": source["id"]},
        )
        await request(
            clients["analyst"],
            "POST",
            prefix + "/metrics",
            json={
                **dataset["metric"],
                "key": "checks",
                "name": "Количество чеков",
                "description": "Сумма checks по business_day: количество оплаченных чеков каждой точки за день.",
                "value_column": "checks",
                "unit": "шт.",
                "source_id": source["id"],
            },
        )
        for plan in dataset["plans"]:
            await request(
                owner,
                "POST",
                prefix + "/plans",
                json={
                    "store_id": stores[plan["store_code"]],
                    "metric_id": metric["id"],
                    "period": plan["period"],
                    "amount": plan["amount"],
                },
            )
        period = {key: dataset["summary"][key] for key in ("date_from", "date_to")}
        case = await request(
            owner,
            "POST",
            prefix + "/cases",
            json={
                "title": "Арбат: как выполнить месячный план",
                "description": "Учебный разбор. Сравним выручку с планом и согласуем действия управляющего. Данные демонстрационные, не прогноз.",
                "store_ids": [stores["A"]],
                "assignee_id": users["store_manager"],
                "measurement": {"metric_id": metric["id"], **period},
            },
        )
        case_path = prefix + "/cases/" + case["id"]
        await request(
            clients["store_manager"],
            "POST",
            case_path + "/comments",
            json={
                "body": "Проверю график смен и доступность популярных позиций. Добавлю результат в этот разбор."
            },
        )
        question = await request(
            owner,
            "POST",
            case_path + "/questions",
            json={"body": "Данные включают возвраты?", "assignee_id": users["analyst"]},
        )
        await request(
            clients["analyst"],
            "POST",
            case_path + "/questions/" + question["id"] + "/answer",
            json={
                "answer": "Да. Показатель суммирует net_revenue, возвраты уже вычтены в источнике. Загружены первые четыре дня октября."
            },
        )
        await request(
            owner,
            "POST",
            case_path + "/questions",
            json={
                "body": "Какие два действия предлагаете для выполнения плана?",
                "assignee_id": users["store_manager"],
            },
        )
        second = await request(
            clients["regional_manager"],
            "POST",
            prefix + "/cases",
            json={
                "title": "Невский: зафиксировать успешную практику",
                "description": "Учебный пример завершённого разбора по одной точке.",
                "store_ids": [stores["B"]],
                "assignee_id": users["franchise_owner"],
                "measurement": {"metric_id": metric["id"], **period},
            },
        )
        await request(
            clients["franchise_owner"],
            "POST",
            prefix + "/cases/" + second["id"] + "/close",
            json={
                "conclusion": "Демонстрационный итог: согласовали наблюдение за числом чеков. Сами данные не доказывают влияние конкретного действия."
            },
        )
        issue = await request(
            clients["analyst"],
            "POST",
            prefix + "/source-issues",
            json={
                "source_id": source["id"],
                "title": "Уточнить полноту ежедневной загрузки",
                "body": "Соединение исправно. В учебной выборке есть только четыре дня октября; нужно определить, как подтверждать полноту периода.",
                "assignee_id": users["admin"],
            },
        )
        issue_path = prefix + "/source-issues/" + issue["id"]
        await request(clients["admin"], "PATCH", issue_path, json={"status": "in_progress"})
        await request(
            clients["admin"],
            "POST",
            issue_path + "/comments",
            json={
                "body": "Подключение и разрешения проверены. Уточняю расписание загрузки; финансовые данные этой роли не доступны."
            },
        )
        overview = await request(
            owner, "GET", prefix + "/overview", params={"metric_id": metric["id"], **period}
        )
        if Decimal(overview["totals"]["actual"]) != Decimal(dataset["summary"]["actual"]):
            raise DemoError("Контрольная сумма реальной аналитики не совпала с тестовыми данными.")
        state.update(
            workspace_id=workspace,
            source_id=source["id"],
            source_database=dataset["source"]["database"],
            metric_id=metric["id"],
            period=period,
        )
        accounts["workspace_id"] = workspace
        for client in clients.values():
            await request(client, "POST", "/auth/logout")
