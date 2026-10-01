import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.domain.query import QueryContext, QueryError
from src.core.concurrency import finish_in_thread, finish_task


class OperationGate:
    def __init__(self):
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def enter(self):
        if self._lock.locked():
            raise QueryError("busy", "Выполняется другой запрос или обновление схемы. Повторите позже.")
        async with self._lock:
            yield


@dataclass
class Services:
    settings: Any
    database: Any
    schema_reader: Any
    importer: Any
    catalog: Any
    agent: Any
    generator: Any
    gate: OperationGate = field(default_factory=OperationGate)
    jobs: dict[str, QueryContext] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)


bearer = HTTPBearer(auto_error=False)


def services(request: Request) -> Services:
    resources = getattr(request.app.state, "services", None)
    if resources is None:
        raise HTTPException(503, "Приложение ещё не готово.")
    return resources


def authorize(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    resources: Services = Depends(services),
) -> None:
    expected = resources.settings.API_TOKEN
    if not expected:
        raise HTTPException(503, "Доступ к API не настроен.")
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(401, "Неверный токен доступа.", headers={"WWW-Authenticate": "Bearer"})


async def build_services(settings) -> Services:

    from src.agent.graph import SQLAgentGraph
    from src.database.client import PostgresClient
    from src.database.executor import SQLExecutor
    from src.database.metadata import SchemaReader
    from src.database.migration.importer import DatabaseImporter
    from src.llm.client import LLMClient
    from src.rag.embedder import TableEmbedder
    from src.rag.indexing.catalog import SchemaCataloger
    from src.rag.retrieval.context import ContextBuilder
    from src.rag.retrieval.reranker import TableReranker
    from src.rag.retrieval.retriever import TableRetriever
    from src.sql.validation import SQLValidator

    if (not settings.API_TOKEN or len(settings.API_TOKEN) < 24 or not settings.API_TOKEN.isascii()
            or not settings.API_TOKEN.isprintable() or any(c.isspace() for c in settings.API_TOKEN)):
        raise ValueError("API_TOKEN must contain at least 24 printable ASCII characters without spaces")
    for name in ("MAX_UPLOAD_BYTES", "MAX_RESULT_ROWS", "MAX_RESULT_BYTES", "SQL_TIMEOUT_MS",
                 "SQL_LOCK_TIMEOUT_MS", "LLM_TIMEOUT_SECONDS", "RETRIEVAL_TOP_K",
                 "CONTEXT_MAX_TABLES", "EMBEDDING_BATCH_SIZE", "RERANKER_BATCH_SIZE", "EVENT_QUEUE_SIZE"):
        if getattr(settings, name) <= 0:
            raise ValueError(f"{name} must be positive")
    if not 0 <= settings.MAX_CORRECTIONS <= 10:
        raise ValueError("MAX_CORRECTIONS must be between 0 and 10")
    if settings.MIN_FREE_MEMORY_MB <= 0:
        raise ValueError("MIN_FREE_MEMORY_MB must be positive")
    database = PostgresClient(settings.db_url_async, settings.db_read_url,
                              settings.SQL_TIMEOUT_MS, settings.SQL_LOCK_TIMEOUT_MS)
    generator = None
    generator_task = None
    try:
        await database.verify_read_role()
        reader = SchemaReader(database)
        embedder = await finish_in_thread(TableEmbedder, settings.MODELS["embedder"],
                                          batch_size=settings.EMBEDDING_BATCH_SIZE)
        catalog = SchemaCataloger(reader, embedder, settings.VECTOR_DB_PATH,
                                   batch_size=settings.EMBEDDING_BATCH_SIZE)
        retriever = TableRetriever(catalog, embedder, top_k=settings.RETRIEVAL_TOP_K)
        reranker = await finish_in_thread(TableReranker, settings.MODELS["reranker"],
                                         batch_size=settings.RERANKER_BATCH_SIZE)
        generator_task = asyncio.create_task(asyncio.to_thread(
            LLMClient, settings.MODELS["llm"], timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
            min_free_memory_mb=settings.MIN_FREE_MEMORY_MB,
        ))
        generator = await finish_task(generator_task)
        agent = SQLAgentGraph(retriever, reranker, ContextBuilder(reader, settings.CONTEXT_MAX_TABLES),
                              generator, SQLValidator(),
                              SQLExecutor(database, max_rows=settings.MAX_RESULT_ROWS,
                                          max_result_bytes=settings.MAX_RESULT_BYTES),
                              max_corrections=settings.MAX_CORRECTIONS)
        return Services(settings, database, reader,
                         DatabaseImporter(settings.db_url_sync, upload_limit_bytes=settings.MAX_UPLOAD_BYTES),
                         catalog, agent, generator)
    except BaseException:
        if generator is None and generator_task is not None and not generator_task.cancelled():
            with suppress(Exception):
                generator = generator_task.result()
        if generator is not None:
            with suppress(Exception):
                await generator.close()
        await database.close()
        raise
