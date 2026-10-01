from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from src.domain.query import QueryError


def _async_url(value: str | URL) -> URL:
    if not value:
        raise ValueError("Нужно явно задать подключение PostgreSQL.")
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Поддерживается только PostgreSQL.")
    return url.set(drivername="postgresql+asyncpg")


class PostgresClient:
    def __init__(self, admin_url: str | URL, read_url: str | URL,
                 statement_timeout_ms: int = 30_000, lock_timeout_ms: int = 3_000):
        if statement_timeout_ms <= 0 or lock_timeout_ms <= 0:
            raise ValueError("Таймауты PostgreSQL должны быть положительными.")
        self.statement_timeout_ms = statement_timeout_ms
        self.lock_timeout_ms = lock_timeout_ms
        admin = _async_url(admin_url)
        read = _async_url(read_url)
        self.admin_engine = create_async_engine(
            admin, pool_pre_ping=True, pool_size=2, max_overflow=0,
        )
        self.read_engine = create_async_engine(
            read, pool_pre_ping=True, pool_size=2, max_overflow=0,
        )

    async def verify_read_role(self) -> None:

        async with self.read_engine.connect() as connection:
            unsafe_role = await connection.scalar(text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_roles
                    WHERE (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb
                           OR rolname IN ('pg_write_all_data', 'pg_read_server_files',
                                          'pg_write_server_files', 'pg_execute_server_program',
                                          'pg_signal_backend'))
                      AND pg_catalog.pg_has_role(oid, 'MEMBER')
                )
            """))
            owns_database = await connection.scalar(text("""
                SELECT pg_catalog.pg_has_role(datdba, 'MEMBER')
                FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()
            """))
            writable_table = await connection.scalar(text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname <> 'information_schema'
                      AND pg_catalog.left(n.nspname, 3) <> 'pg_'
                      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND pg_catalog.has_table_privilege(
                          c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
                      )
                )
            """))
        if unsafe_role or owns_database or writable_table:
            raise QueryError(
                "unsafe_read_role",
                "Подключение запросов должно использовать отдельную роль только для чтения "
                "без административных полномочий и прав изменения таблиц.",
            )

    async def close(self) -> None:
        try:
            await self.read_engine.dispose()
        finally:
            await self.admin_engine.dispose()
