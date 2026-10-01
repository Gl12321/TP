from collections.abc import Sequence

from sqlalchemy import inspect, text

from src.database.client import PostgresClient
from src.database.identifiers import quote_identifier, validate_schema_name
from src.domain.schema import Column, ForeignKey, TableRef, TableSchema


class SchemaReader:
    def __init__(self, client: PostgresClient):
        self.client = client

    async def list_schemas(self) -> list[str]:
        async with self.client.admin_engine.connect() as connection:
            result = await connection.execute(text("""
                SELECT nspname FROM pg_catalog.pg_namespace
                WHERE nspname NOT IN ('information_schema', 'public')
                  AND pg_catalog.left(nspname, 3) <> 'pg_'
                ORDER BY nspname
            """))
            return list(result.scalars())

    async def get_tables(self, schemas: Sequence[str]) -> list[TableSchema]:
        selected = tuple(dict.fromkeys(validate_schema_name(name) for name in schemas))
        if not selected:
            return []

        def read(connection):
            inspector = inspect(connection)
            tables = []
            for schema in selected:
                for name in sorted(inspector.get_table_names(schema=schema)):
                    columns = tuple(Column(
                        name=column["name"],
                        data_type=column["type"].compile(dialect=connection.dialect),
                        nullable=column.get("nullable", True),
                        comment=column.get("comment"),
                    ) for column in inspector.get_columns(name, schema=schema))
                    primary_key = tuple(inspector.get_pk_constraint(
                        name, schema=schema,
                    ).get("constrained_columns") or ())
                    foreign_keys = tuple(ForeignKey(
                        columns=tuple(key["constrained_columns"]),
                        target=TableRef(key.get("referred_schema") or schema, key["referred_table"]),
                        target_columns=tuple(key["referred_columns"]),
                    ) for key in inspector.get_foreign_keys(name, schema=schema))
                    description = inspector.get_table_comment(name, schema=schema).get("text") or ""
                    table = TableSchema(
                        ref=TableRef(schema, name), columns=columns, primary_key=primary_key,
                        foreign_keys=foreign_keys, description=description,
                    )
                    tables.append(TableSchema(
                        ref=table.ref, columns=columns, primary_key=primary_key,
                        foreign_keys=foreign_keys, description=description, ddl=_ddl(table),
                    ))
            return tables

        async with self.client.admin_engine.connect() as connection:


            await connection.execute(text("SET LOCAL search_path = pg_catalog"))
            return await connection.run_sync(read)

    async def drop_all_schemas(self) -> list[str]:

        async with self.client.admin_engine.begin() as connection:
            await connection.execute(text("""
                SELECT pg_catalog.set_config('lock_timeout', :lock_timeout, true),
                       pg_catalog.set_config('statement_timeout', :statement_timeout, true)
            """), {
                "lock_timeout": str(self.client.lock_timeout_ms),
                "statement_timeout": str(self.client.statement_timeout_ms),
            })
            result = await connection.execute(text("""
                SELECT nspname FROM pg_catalog.pg_namespace
                WHERE nspname NOT IN ('information_schema', 'public')
                  AND pg_catalog.left(nspname, 3) <> 'pg_' ORDER BY nspname
            """))
            schemas = list(result.scalars())
            for schema in schemas:
                await connection.exec_driver_sql(
                    f"DROP SCHEMA {quote_identifier(schema)} CASCADE"
                )
            return schemas


def _ddl(table: TableSchema) -> str:

    quoted = quote_identifier
    items = [
        f"{quoted(column.name)} {column.data_type}"
        + ("" if column.nullable else " NOT NULL")
        for column in table.columns
    ]
    if table.primary_key:
        items.append("PRIMARY KEY (" + ", ".join(map(quoted, table.primary_key)) + ")")
    for key in table.foreign_keys:
        items.append(
            "FOREIGN KEY (" + ", ".join(map(quoted, key.columns)) + ") REFERENCES "
            + quoted(key.target.schema) + "." + quoted(key.target.name)
            + " (" + ", ".join(map(quoted, key.target_columns)) + ")"
        )
    return (
        f"CREATE TABLE {quoted(table.ref.schema)}.{quoted(table.ref.name)} (\n  "
        + ",\n  ".join(items) + "\n);"
    )
