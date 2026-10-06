from urllib.parse import urlsplit

from tools.demo.state import DemoError


async def check_administrator(admin_url):
    import asyncpg

    connection = await asyncpg.connect(admin_url, timeout=10)
    try:
        allowed = await connection.fetchval(
            "SELECT rolsuper OR (rolcreatedb AND rolcreaterole) FROM pg_roles WHERE rolname=current_user"
        )
        if not allowed:
            raise DemoError(
                "Для первой подготовки demo нужны права создания баз и ролей PostgreSQL."
            )
    finally:
        await connection.close()


async def create_application(admin_url, state):
    import asyncpg

    parsed = urlsplit(state["database_url"])
    role, database = parsed.username, parsed.path.lstrip("/")
    connection = await asyncpg.connect(admin_url, timeout=10)
    role_created = database_created = False
    try:
        exists = await connection.fetchval(
            "SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1) OR EXISTS(SELECT FROM pg_database WHERE datname=$2)",
            role,
            database,
        )
        if exists:
            raise DemoError("Имена ресурсов demo уже заняты. Существующие БД и роли не изменены.")
        await connection.execute(
            f"CREATE ROLE \"{role}\" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{parsed.password}'"
        )
        role_created = True
        await connection.execute(f'CREATE DATABASE "{database}" OWNER "{role}"')
        database_created = True
        await connection.execute(f'REVOKE CONNECT ON DATABASE "{database}" FROM PUBLIC')
        await connection.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"')
        application = await asyncpg.connect(admin_url, database=database, timeout=10)
        try:
            await application.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await application.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{role}"')
        finally:
            await application.close()
    except BaseException:
        if database_created:
            await connection.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        if role_created:
            await connection.execute(f'DROP ROLE "{role}"')
        raise
    finally:
        await connection.close()


async def cleanup_application(admin_url, state):
    import asyncpg

    from tools.demo.state import validate

    validate(state)
    parsed, admin = urlsplit(state["database_url"]), urlsplit(admin_url)
    if parsed.hostname != admin.hostname or parsed.port != (admin.port or 5432):
        raise DemoError("Сервер очистки отличается от сервера demo.")
    connection = await asyncpg.connect(admin_url, timeout=10)
    try:
        await connection.execute(
            f'DROP DATABASE IF EXISTS "{parsed.path.lstrip("/")}" WITH (FORCE)'
        )
        await connection.execute(f'DROP ROLE IF EXISTS "{parsed.username}"')
    finally:
        await connection.close()


async def check_application(state):
    import asyncpg

    connection = await asyncpg.connect(
        state["database_url"].replace("postgresql+asyncpg://", "postgresql://"), timeout=10
    )
    try:
        valid = await connection.fetchval(
            "SELECT EXISTS(SELECT FROM app_setup WHERE id=1) AND EXISTS(SELECT FROM workspaces WHERE id=$1)",
            state["workspace_id"],
        )
        if not valid:
            raise DemoError(
                "База demo не соответствует сохранённому пространству; данные не изменены."
            )
    finally:
        await connection.close()
