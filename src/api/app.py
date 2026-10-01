import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from src.api.dependencies import Services, build_services
from src.api.routes import queries, schemas
from src.core.config import get_settings
from src.core.logging import configure_logging, get_logger
from src.domain.query import QueryError


class BodyLimitMiddleware:
    def __init__(self, app, settings_provider):
        self.app = app
        self.settings_provider = settings_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = self.settings_provider()
        limit = settings.MAX_UPLOAD_BYTES + 1024 * 1024 if scope.get("path") == "/load_schema" else 65536
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length is not None:
            try:
                oversized = int(length) > limit
            except ValueError:
                oversized = True
            if oversized:
                return await JSONResponse({"detail": "Тело запроса слишком велико."}, status_code=413)(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(413, "Тело запроса слишком велико.")
            return message

        await self.app(scope, bounded_receive, send)


def create_app(injected_services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        resources = injected_services or await build_services(get_settings())
        app.state.services = resources
        try:
            yield
        finally:
            for context in resources.jobs.values():
                context.cancelled.set()
            tasks = list(resources.tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if injected_services is None:
                await resources.generator.close()
                await resources.database.close()

    app = FastAPI(title="SQL Agent", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware, settings_provider=lambda: (
        injected_services.settings if injected_services is not None else get_settings()
    ))
    app.include_router(queries.router)
    app.include_router(schemas.router)

    @app.get("/health", include_in_schema=False)
    async def health():
        if getattr(app.state, "services", None) is None:
            raise HTTPException(503, "Приложение ещё не готово.")
        return {"status": "ready"}

    @app.exception_handler(QueryError)
    async def query_error(_request, exc):
        return JSONResponse(status_code=409 if exc.code == "busy" else 400,
                            content={"detail": {"code": exc.code, "message": str(exc)}})

    @app.exception_handler(Exception)
    async def unexpected_error(_request, exc):
        get_logger("api").error("Unhandled HTTP error", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка обработки запроса."})

    return app
