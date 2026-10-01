import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.sql.expression import ClauseElement, Executable

from src.database.client import PostgresClient
from src.core.concurrency import finish_task
from src.domain.query import QueryContext, QueryError, QueryResult


_FIXABLE_STATES = {
    "42601", "42P01", "42703", "42702", "42712", "42803", "42804", "42883", "42P10",
    "22P02", "22012", "22003", "22007", "22008",
}


class _ValidatedSQL(Executable, ClauseElement):


    inherit_cache = False

    def __init__(self, sql: str):
        self.sql = sql


@compiles(_ValidatedSQL)
def _compile_validated_sql(statement, compiler, **kwargs):
    return statement.sql


def _query_error(error: DBAPIError) -> QueryError:
    original = error.orig
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    detail = str(original)[:1_500]
    if state in {"42P01", "42703"}:
        return QueryError("missing_context", detail, retryable=True)
    if state in _FIXABLE_STATES:
        return QueryError("invalid_sql", detail, retryable=True)
    if state == "57014":
        return QueryError("query_timeout", "SQL-запрос прерван или превысил время выполнения.")
    if state == "55P03":
        return QueryError("database_busy", "Истекло время ожидания блокировки в базе данных.")
    if state in {"42501", "25006"}:
        return QueryError("query_forbidden", "Недостаточно прав или запрос нарушает режим чтения.")
    return QueryError("database_error", "Не удалось выполнить запрос к PostgreSQL.")


def _row_size(row: list, limit: int) -> int:

    size = 0
    pending = [iter((row,))]
    while pending and size <= limit:
        try:
            value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        size += sys.getsizeof(value)
        if isinstance(value, dict):
            pending.append(iter(value.keys()))
            pending.append(iter(value.values()))
        elif isinstance(value, (list, tuple)):
            pending.append(iter(value))
    return size


class SQLExecutor:
    def __init__(self, client: PostgresClient, max_rows: int = 1_000,
                 max_result_bytes: int = 8 * 1024 * 1024):
        if max_rows <= 0 or max_result_bytes <= 0:
            raise ValueError("Ограничения результата должны быть положительными.")
        self.client = client
        self.max_rows = max_rows
        self.max_result_bytes = max_result_bytes

    async def execute(self, sql: str, context: QueryContext) -> QueryResult:

        context.check_cancelled()
        operation = asyncio.create_task(self._execute(sql, context))
        try:
            while not operation.done():
                await asyncio.wait({operation}, timeout=0.1)
                context.check_cancelled()
            return await operation
        except DBAPIError as error:
            raise _query_error(error) from error
        except SQLAlchemyError as error:
            raise QueryError("database_error", "Подключение к PostgreSQL недоступно.") from error
        finally:
            if not operation.done():
                operation.cancel()

            cleanup = asyncio.ensure_future(asyncio.gather(operation, return_exceptions=True))
            await finish_task(cleanup)

    async def _execute(self, sql: str, context: QueryContext) -> QueryResult:
        async with self.client.read_engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("SET LOCAL search_path = pg_catalog"))
                await connection.execute(text("SET LOCAL standard_conforming_strings = on"))
                await connection.execute(text("""
                    SELECT pg_catalog.set_config('statement_timeout', :statement_timeout, true),
                           pg_catalog.set_config('lock_timeout', :lock_timeout, true)
                """), {
                    "statement_timeout": str(self.client.statement_timeout_ms),
                    "lock_timeout": str(self.client.lock_timeout_ms),
                })
                context.check_cancelled()
                rows, size, truncated = [], 0, False
                statement = _ValidatedSQL(sql)
                async with connection.stream(statement, execution_options={"yield_per": 1}) as result:
                    columns = list(result.keys())
                    async for record in result:
                        context.check_cancelled()
                        if len(rows) >= self.max_rows:
                            truncated = True
                            break
                        row = list(record)
                        size += _row_size(row, self.max_result_bytes - size)
                        if size > self.max_result_bytes:
                            if not rows:
                                raise QueryError("result_too_large", "Одна строка превышает лимит размера результата.")
                            truncated = True
                            break
                        rows.append(row)
                return QueryResult(sql=sql, columns=columns, rows=rows, truncated=truncated)
