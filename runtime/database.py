import os


def main() -> None:
    import psycopg2
    from psycopg2 import sql

    account = os.environ["APP_DB_USER"]
    administrator = os.environ["DB_USER"]
    if account == administrator:
        raise ValueError("Приложению нужна отдельная роль PostgreSQL.")
    parameters = {
        "host": os.environ.get("DB_HOST", "db"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "user": administrator,
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 10,
    }
    database = os.environ["APP_DB_NAME"]
    connection = psycopg2.connect(dbname=os.environ["DB_NAME"], **parameters)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (account,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(account)))
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %s"
                ).format(sql.Identifier(account)),
                (os.environ["APP_DB_PASSWORD"],),
            )
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(account)
                    )
                )
            cursor.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database)
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(account)
                )
            )
    finally:
        connection.close()
    connection = psycopg2.connect(dbname=database, **parameters)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                cursor.execute(
                    sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                        sql.Identifier(account)
                    )
                )
    finally:
        connection.close()
    print("PostgreSQL: роль приложения готова.", flush=True)


if __name__ == "__main__":
    main()
