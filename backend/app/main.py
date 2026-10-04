import logging
import secrets
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.access import routes as access_routes
from backend.app.access import account as account_routes
from backend.app.analytics import routes as analytics_routes
from backend.app.assistant import routes as assistant_routes
from backend.app.collaboration import routes as collaboration_routes
from backend.app.infrastructure.config import load_settings
from backend.app.infrastructure.database import Database, load_models
from backend.app.infrastructure.errors import AppError, handle_app_error
from backend.app.infrastructure.http import BodyLimitMiddleware
from backend.app.infrastructure.security import DUMMY_PASSWORD_HASH
from backend.app.sources.service import SourceConnections
from backend.app.sources import routes as source_routes


logger = logging.getLogger("razbor.api")


def create_app(settings=None):
    settings = settings or load_settings()
    load_models()
    database = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        yield
        await database.close()

    app = FastAPI(
        title="Разбор",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.database = database
    app.state.password_gate = asyncio.Semaphore(4)
    app.state.dummy_password_hash = DUMMY_PASSWORD_HASH
    app.state.source_connections = SourceConnections()
    app.add_exception_handler(AppError, handle_app_error)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        fields = [
            {
                "field": ".".join(str(item) for item in issue["loc"] if item != "body"),
                "message": issue["msg"],
            }
            for issue in error.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation",
                    "message": "Проверьте введённые данные",
                    "fields": fields,
                }
            },
            status_code=422,
        )

    @app.exception_handler(IntegrityError)
    async def conflict(request, error):
        return JSONResponse(
            {
                "error": {
                    "code": "conflict",
                    "message": "Данные уже существуют или изменились. Обновите страницу",
                }
            },
            status_code=409,
        )

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        request_id = secrets.token_hex(12)
        origin = request.headers.get("origin")
        if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
            current_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin != current_origin and origin not in settings.allowed_origins:
                return JSONResponse(
                    {"error": {"code": "origin", "message": "Источник запроса не разрешён"}},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path in {"/api/docs", "/docs/oauth2-redirect"}:
            policy = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        response.headers["Content-Security-Policy"] = policy
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Last-Event-ID"],
        )
    app.add_middleware(BodyLimitMiddleware)

    for router in (
        access_routes.router,
        account_routes.router,
        analytics_routes.router,
        source_routes.router,
        assistant_routes.router,
        collaboration_routes.router,
    ):
        app.include_router(router, prefix="/api/v1")

    @app.get("/health")
    @app.get("/api/v1/health")
    async def health():
        from backend.app.worker import healthy

        try:
            worker_ready = await healthy(database)
        except Exception:
            worker_ready = False
        return {"status": "ok", "worker_ready": worker_ready}

    @app.get("/ready")
    async def ready():
        try:
            async with database.sessions() as db:
                await db.execute(text("SELECT id FROM app_setup LIMIT 1"))
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise AppError("not_found", "Маршрут не найден", 404)
        root = settings.frontend_dist.resolve()
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if Path(path).suffix:
            raise AppError("not_found", "Файл не найден", 404)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {
                "message": "Frontend build is missing. Build frontend/ and restart the application.",
                "api": "/api/docs",
            },
            status_code=503,
        )

    return app


app = create_app()
