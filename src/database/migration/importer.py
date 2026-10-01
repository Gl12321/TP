import sqlite3
from collections.abc import Callable

from sqlalchemy import Boolean, MetaData, Numeric, create_engine, func, select, text, type_coerce
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable
from sqlalchemy.types import NullType

from src.database.identifiers import quote_identifier, validate_schema_name
from src.domain.query import QueryError
from src.database.migration.mapping import clone_metadata
from src.database.migration.validation import (
    configure_sqlite, validate_sqlite, validate_sqlite_path,
)


class DatabaseImporter:
    def __init__(self, sync_url: str | URL, upload_limit_bytes: int = 512 * 1024 * 1024,
                 batch_size: int = 1_000, lock_timeout_ms: int = 3_000,
                 statement_timeout_ms: int = 120_000):
        url = make_url(sync_url)
        if url.get_backend_name() != "postgresql":
            raise ValueError("Целевой сервер импорта должен быть PostgreSQL.")
        if min(upload_limit_bytes, batch_size, lock_timeout_ms, statement_timeout_ms) <= 0:
            raise ValueError("Ограничения импорта должны быть положительными.")
        self.url = url.set(drivername="postgresql+psycopg2")
        self.upload_limit_bytes = upload_limit_bytes
        self.batch_size = batch_size
        self.lock_timeout_ms = lock_timeout_ms
        self.statement_timeout_ms = statement_timeout_ms

    def migrate_db(self, schema_name: str, sqlite_path: str, *,
                   before_replace: Callable[[], None] | None = None) -> None:

        schema = validate_schema_name(schema_name)
        path = validate_sqlite_path(sqlite_path, self.upload_limit_bytes)

        def connect():
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            configure_sqlite(connection)
            return connection

        source_engine = create_engine("sqlite://", creator=connect, poolclass=StaticPool)
        destination_engine = None
        try:
            with source_engine.connect() as source:


                source.exec_driver_sql("BEGIN")
                names = validate_sqlite(source.connection.driver_connection)
                source_metadata = MetaData()
                source_metadata.reflect(bind=source, only=names)
                target_metadata = clone_metadata(source_metadata, schema)


                destination_engine = create_engine(self.url, pool_pre_ping=True)
                with destination_engine.begin() as destination:
                    destination.exec_driver_sql("SET LOCAL search_path = pg_catalog")
                    destination.exec_driver_sql("SET LOCAL standard_conforming_strings = on")
                    destination.execute(text("""
                        SELECT pg_catalog.set_config('lock_timeout', :lock_timeout, true),
                               pg_catalog.set_config('statement_timeout', :statement_timeout, true)
                    """), {
                        "lock_timeout": str(self.lock_timeout_ms),
                        "statement_timeout": str(self.statement_timeout_ms),
                    })
                    self._check_external_dependencies(destination, schema)
                    if before_replace is not None:
                        before_replace()
                    destination.exec_driver_sql(f"DROP SCHEMA IF EXISTS {quote_identifier(schema)} CASCADE")
                    destination.exec_driver_sql(f"CREATE SCHEMA {quote_identifier(schema)}")
                    targets = {table.name: table for table in target_metadata.tables.values()}
                    for table in targets.values():

                        destination.execute(CreateTable(table, include_foreign_key_constraints=[]))
                    for original in source_metadata.tables.values():
                        target = targets[original.name]


                        columns = [type_coerce(column, NullType()).label(column.name)
                                   if isinstance(column.type, (Boolean, Numeric)) else column
                                   for column in original.columns]
                        result = source.execute(select(*columns))
                        try:
                            while batch := result.fetchmany(self.batch_size):
                                destination.execute(target.insert(), [dict(row._mapping) for row in batch])
                        finally:
                            result.close()
                    for table in targets.values():


                        for index in table.indexes:
                            destination.execute(CreateIndex(index))
                    for table in targets.values():
                        for constraint in table.foreign_key_constraints:
                            destination.execute(AddConstraint(constraint))
                    for table in targets.values():
                        self._reset_sequence(destination, table)
        finally:
            source_engine.dispose()
            if destination_engine is not None:
                destination_engine.dispose()

    @staticmethod
    def _check_external_dependencies(connection, schema: str) -> None:
        dependent = connection.scalar(text("""
            WITH RECURSIVE affected(classid, objid, objsubid) AS (
                SELECT 'pg_catalog.pg_namespace'::regclass::oid, oid, 0
                FROM pg_catalog.pg_namespace WHERE nspname = :schema
                UNION
                SELECT dependency.classid, dependency.objid, dependency.objsubid
                FROM pg_catalog.pg_depend dependency
                JOIN affected parent
                  ON dependency.refclassid = parent.classid
                 AND dependency.refobjid = parent.objid
                 AND (parent.objsubid = 0 OR dependency.refobjsubid = parent.objsubid)
            )
            SELECT description.identity
            FROM affected
            CROSS JOIN LATERAL pg_catalog.pg_identify_object(classid, objid, objsubid) description
            CROSS JOIN LATERAL pg_catalog.pg_identify_object_as_address(classid, objid, objsubid) address
            WHERE COALESCE(description.schema, address.object_names[1], '') NOT IN (:schema, 'pg_toast')
               OR description.type = 'extension'
            LIMIT 1
        """), {"schema": schema})
        if dependent is not None:
            raise QueryError(
                "external_dependencies",
                f"Замена схемы затронет зависимый объект вне неё: {dependent}. "
                "Сначала измените внешние зависимости в PostgreSQL."
            )

    @staticmethod
    def _reset_sequence(connection, table) -> None:
        keys = list(table.primary_key.columns)
        if len(keys) != 1:
            return
        key = keys[0]
        relation = quote_identifier(table.schema) + "." + quote_identifier(table.name)
        sequence = connection.scalar(text(
            "SELECT pg_catalog.pg_get_serial_sequence(:relation, :column)"
        ), {"relation": relation, "column": key.name})
        if sequence is None:
            return
        maximum = connection.scalar(select(func.max(key)))
        has_positive_key = maximum is not None and maximum >= 1
        connection.execute(text(
            "SELECT pg_catalog.setval(CAST(:sequence AS regclass), :value, :is_called)"
        ), {
            "sequence": sequence,
            "value": maximum if has_positive_key else 1,
            "is_called": has_positive_key,
        })
