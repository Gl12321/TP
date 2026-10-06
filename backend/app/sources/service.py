import asyncio
import json
import ssl
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy import select

from backend.app.infrastructure.database import utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import SecretStore
from backend.app.sources.models import Source


def public_source(source):
    result = {
        key: getattr(source, key)
        for key in (
            "id",
            "name",
            "host",
            "port",
            "database",
            "username",
            "schemas",
            "ssl_mode",
            "enabled",
            "reader_ids",
            "catalog_version",
            "policy_revision",
            "status",
            "last_checked_at",
            "error",
        )
    }
    result["permitted_table_count"] = len(source.policies)
    return result


def can_read_source(source, access):
    return "analytics:read" in access.capabilities and (
        source.reader_ids is None or access.user_id in source.reader_ids
    )


def require_source_access(source, access):
    if source.workspace_id != access.workspace_id or not can_read_source(source, access):
        raise AppError(
            "source_forbidden",
            "Профиль данных недоступен. Обратитесь к администратору пространства",
            403,
        )


async def validate_readers(db, workspace_id, reader_ids):
    from backend.app.access.models import Membership

    if reader_ids is None:
        return
    allowed = set(
        (
            await db.scalars(
                select(Membership.user_id).where(
                    Membership.workspace_id == workspace_id,
                    Membership.active.is_(True),
                    Membership.user_id.in_(reader_ids),
                )
            )
        ).all()
    )
    if allowed != set(reader_ids):
        raise AppError("invalid_readers", "Выберите действующих участников этого пространства")


async def get_source(db, source_id, workspace_id, *, for_update=False):
    statement = (
        select(Source)
        .where(Source.id == source_id, Source.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    source = await db.scalar(statement)
    if source is None:
        raise AppError("not_found", "Источник не найден", 404)
    return source


def ensure_ready(source):
    if not source.enabled or source.status != "ready" or not source.policies:
        raise AppError(
            "source_not_ready",
            "Источник ещё не проверен или для него не настроен доступ к таблицам",
            409,
        )


@asynccontextmanager
async def source_connection(source, settings):
    if source.host.lower() not in {host.lower() for host in settings.source_hosts}:
        raise AppError(
            "source_host_blocked", "Адрес источника отсутствует в разрешённых адресах сервера", 403
        )
    tls = False
    if source.ssl_mode != "disable":
        tls = ssl.create_default_context()
        if source.ssl_mode == "require":
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
    password = SecretStore(settings.secret_key).decrypt(source.encrypted_password)
    try:
        connection = await asyncpg.connect(
            host=source.host,
            port=source.port,
            database=source.database,
            user=source.username,
            password=password,
            ssl=tls,
            timeout=10,
            command_timeout=settings.sql_timeout_ms / 1000,
            server_settings={
                "application_name": "razbor_reader",
                "default_transaction_read_only": "on",
            },
        )
    except (OSError, asyncpg.PostgresError, TimeoutError) as error:
        raise AppError(
            "source_connection",
            "Не удалось подключиться к источнику. Проверьте адрес, роль и параметры TLS",
            503,
        ) from error
    try:
        yield connection
    finally:
        await connection.close(timeout=5)


class SourceConnections:
    def __init__(self, total=8, per_source=2, acquire_timeout=5):
        self.total = asyncio.Semaphore(total)
        self.per_source = per_source
        self.acquire_timeout = acquire_timeout
        self.sources = {}

    @asynccontextmanager
    async def connect(self, source, settings):
        limiter = self.sources.setdefault(source.id, asyncio.Semaphore(self.per_source))
        try:
            await asyncio.wait_for(self.total.acquire(), timeout=self.acquire_timeout)
        except TimeoutError:
            raise AppError(
                "source_busy", "Соединения с источниками заняты. Повторите запрос позже", 503
            )
        try:
            try:
                await asyncio.wait_for(limiter.acquire(), timeout=self.acquire_timeout)
            except TimeoutError:
                raise AppError(
                    "source_busy", "Источник обрабатывает другие запросы. Повторите позже", 503
                )
            try:
                async with source_connection(source, settings) as connection:
                    yield connection
            finally:
                limiter.release()
        finally:
            self.total.release()


async def inspect_source(source, settings, connections):
    async with connections.connect(source, settings) as connection:
        async with connection.transaction(readonly=True):
            role = await connection.fetchrow(
                "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            if any(role.values()):
                raise AppError(
                    "unsafe_role",
                    "Используйте отдельную роль чтения без административных полномочий",
                    400,
                )
            tables = await connection.fetch(
                "SELECT n.nspname AS schema, c.relname AS name, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','v','m','p') AND has_table_privilege(c.oid, 'SELECT') ORDER BY n.nspname,c.relname",
                source.schemas,
            )
            catalog = []
            for table in tables:
                writable = await connection.fetchval(
                    "SELECT has_table_privilege($1::oid, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') OR has_any_column_privilege($1::oid, 'INSERT,UPDATE')",
                    table["oid"],
                )
                if writable:
                    raise AppError(
                        "unsafe_role",
                        "Роль источника имеет права записи. Подключите роль только для чтения",
                        400,
                    )
                columns = await connection.fetch(
                    "SELECT a.attname AS name, format_type(a.atttypid,a.atttypmod) AS data_type, NOT a.attnotnull AS nullable, col_description(a.attrelid,a.attnum) AS comment FROM pg_attribute a WHERE a.attrelid=$1::oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                    table["oid"],
                )
                primary_key = await connection.fetchval(
                    "SELECT array_agg(a.attname ORDER BY k.ordinality) FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum WHERE c.conrelid=$1::oid AND c.contype='p'",
                    table["oid"],
                )
                foreign_keys = await connection.fetch(
                    "SELECT c.oid,n.nspname AS target_schema,r.relname AS target_name,array_agg(a.attname ORDER BY k.ordinality) AS columns,array_agg(b.attname ORDER BY k.ordinality) AS target_columns FROM pg_constraint c JOIN pg_class r ON r.oid=c.confrelid JOIN pg_namespace n ON n.oid=r.relnamespace CROSS JOIN LATERAL unnest(c.conkey,c.confkey) WITH ORDINALITY k(attnum,target_attnum,ordinality) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum JOIN pg_attribute b ON b.attrelid=c.confrelid AND b.attnum=k.target_attnum WHERE c.conrelid=$1::oid AND c.contype='f' GROUP BY c.oid,n.nspname,r.relname",
                    table["oid"],
                )
                description = await connection.fetchval(
                    "SELECT obj_description($1::oid, 'pg_class')", table["oid"]
                )
                catalog.append(
                    {
                        "schema": table["schema"],
                        "name": table["name"],
                        "columns": [dict(column) for column in columns],
                        "primary_key": primary_key or [],
                        "foreign_keys": [
                            {key: value for key, value in dict(foreign_key).items() if key != "oid"}
                            for foreign_key in foreign_keys
                        ],
                        "description": description or "",
                    }
                )
            if not catalog:
                raise AppError(
                    "empty_catalog", "В выбранных схемах нет доступных для чтения таблиц", 400
                )
            source.catalog = catalog
            source.catalog_version += 1
            source.status = "ready"
            source.error = None
            source.last_checked_at = utcnow()


def authorized_catalog(source):
    from sql_agent.contracts import Column, ForeignKey, TableRef, TableSchema

    records = {(table["schema"], table["name"]): table for table in source.catalog}
    allowed = {
        (policy["schema"], policy["name"]): set(policy["columns"]) for policy in source.policies
    }
    tables = []
    for policy in source.policies:
        table = records.get((policy["schema"], policy["name"]))
        if not table:
            raise AppError(
                "catalog_changed",
                "Разрешённая таблица исчезла из источника. Проверьте настройки доступа",
                409,
            )
        columns = {column["name"]: column for column in table["columns"]}
        if not set(policy["columns"]).issubset(columns):
            raise AppError(
                "catalog_changed", "Структура источника изменилась. Проверьте разрешённые поля", 409
            )
        foreign_keys = []
        for foreign_key in table.get("foreign_keys", []):
            target = (foreign_key["target_schema"], foreign_key["target_name"])
            if (
                target in allowed
                and set(foreign_key["columns"]).issubset(policy["columns"])
                and set(foreign_key["target_columns"]).issubset(allowed[target])
            ):
                foreign_keys.append(
                    ForeignKey(
                        tuple(foreign_key["columns"]),
                        TableRef(*target),
                        tuple(foreign_key["target_columns"]),
                    )
                )
        primary_key = tuple(table.get("primary_key", []))
        if not set(primary_key).issubset(policy["columns"]):
            primary_key = ()
        tables.append(
            TableSchema(
                ref=TableRef(policy["schema"], policy["name"]),
                columns=tuple(Column(**columns[name]) for name in policy["columns"]),
                primary_key=primary_key,
                foreign_keys=tuple(foreign_keys),
                description=table.get("description", ""),
            )
        )
    return tuple(tables)


def scoped_sql(sql, tables, policies):
    import sqlglot
    from sqlglot import exp
    from sql_agent.sql.validation import SQLValidator

    normalized = SQLValidator().validate(sql, tables)
    tree = sqlglot.parse_one(normalized, read="postgres")
    allowed = {(policy["schema"], policy["name"]): policy for policy in policies}
    for table in list(tree.find_all(exp.Table)):
        policy = allowed.get((table.db, table.name))
        if policy is None:
            raise AppError("policy_denied", "Таблица недоступна", 403)
        relation = table.copy()
        relation.set("alias", None)
        selection = exp.select(
            *(exp.column(name, quoted=True) for name in policy["columns"])
        ).from_(relation)
        if not policy["shared"]:
            column = exp.column(policy["store_column"], quoted=True).sql(dialect="postgres")
            predicate = sqlglot.parse_one(
                f"CAST({column} AS TEXT) = ANY($1::TEXT[])", read="postgres"
            )
            selection = selection.where(predicate)
        table.replace(
            exp.Subquery(
                this=selection,
                alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name, quoted=True)),
            )
        )
    return (
        normalized,
        tree.sql(dialect="postgres", comments=False),
        any(
            not policy["shared"]
            for policy in allowed.values()
            if (policy["schema"], policy["name"])
            in {
                (table.db, table.name)
                for table in sqlglot.parse_one(normalized, read="postgres").find_all(exp.Table)
            }
        ),
    )


class ScopedExecutor:
    def __init__(self, source, store_codes, settings, check_access, connections):
        self.source = source
        self.store_codes = list(store_codes)
        self.settings = settings
        self.check_access = check_access
        self.connections = connections
        self.tables = authorized_catalog(source)
        self.execution = None

    async def execute(self, sql, context):
        context.check_cancelled()
        task = asyncio.create_task(self._execute(sql, context))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.1)
                context.check_cancelled()
                if done:
                    return await task
        except BaseException:
            task.cancel()
            try:
                await task
            except (Exception, asyncio.CancelledError):
                pass
            raise

    async def _execute(self, sql, context):
        from sql_agent.contracts import QueryError, QueryResult
        from sql_agent.sql.results import json_value

        context.check_cancelled()
        await self.check_access()
        normalized, statement, needs_scope = scoped_sql(sql, self.tables, self.source.policies)
        if needs_scope and not self.store_codes:
            raise QueryError("empty_scope", "Нет доступных точек для выполнения запроса")
        try:
            async with self.connections.connect(self.source, self.settings) as connection:
                async with connection.transaction(readonly=True):
                    await connection.execute(
                        "SELECT set_config('statement_timeout', $1, true)",
                        str(self.settings.sql_timeout_ms),
                    )
                    await connection.execute(
                        "SELECT set_config('lock_timeout', $1, true)",
                        str(self.settings.sql_lock_timeout_ms),
                    )
                    await connection.execute("SELECT set_config('search_path', 'pg_catalog', true)")
                    prepared = await connection.prepare(statement)
                    attributes = prepared.get_attributes()
                    rows, total_bytes, truncated = [], 0, False
                    arguments = [self.store_codes] if needs_scope else []
                    async for record in prepared.cursor(*arguments, prefetch=16):
                        context.check_cancelled()
                        row = [json_value(value) for value in record]
                        total_bytes += len(
                            json.dumps(row, ensure_ascii=False, allow_nan=False).encode()
                        )
                        if (
                            len(rows) >= self.settings.max_result_rows
                            or total_bytes > self.settings.max_result_bytes
                        ):
                            truncated = True
                            break
                        rows.append(row)
                    context.check_cancelled()
                    await self.check_access()
                    self.execution = {
                        "sql": statement,
                        "parameters": [self.store_codes] if needs_scope else [],
                    }
                    return QueryResult(
                        normalized,
                        [attribute.name for attribute in attributes],
                        rows,
                        truncated,
                        [attribute.type.name for attribute in attributes],
                    )
        except AppError as error:
            raise QueryError(error.code, error.message) from error
        except (asyncpg.PostgresError, TimeoutError) as error:
            code = getattr(error, "sqlstate", "")
            if code == "57014" or isinstance(error, TimeoutError):
                raise QueryError(
                    "sql_timeout", "Источник не завершил запрос за допустимое время"
                ) from error
            retryable = code in {
                "42703",
                "42P01",
                "42803",
                "42804",
                "42883",
                "22012",
                "22007",
                "22008",
            }
            message = {
                "42703": "Колонка отсутствует в источнике",
                "42P01": "Таблица отсутствует в источнике",
                "42803": "Некорректная группировка",
                "42804": "Несовместимые типы данных",
                "42883": "Операция не поддерживает типы аргументов",
                "22012": "Деление на ноль",
                "22007": "Некорректный формат даты",
                "22008": "Некорректная дата",
            }.get(code, "Источник отклонил запрос")
            raise QueryError("source_query", message, retryable=retryable) from error
