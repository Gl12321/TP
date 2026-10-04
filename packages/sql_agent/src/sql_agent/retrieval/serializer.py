from dataclasses import asdict
from dataclasses import replace
import json

from sql_agent.query import QueryError
from sql_agent.schema import Column, ForeignKey, TableRef, TableSchema


def table_id(table: TableSchema) -> str:
    return json.dumps([table.ref.schema, table.ref.name], ensure_ascii=False)


def serialize_table(table: TableSchema) -> str:
    lines = [f"Schema: {table.ref.schema}", f"Table: {table.ref.name}"]
    if table.description:
        lines.append(f"Description: {table.description}")
    lines.append("Columns:")
    for column in table.columns:
        detail = f"  {column.name}: {column.data_type}"
        if column.comment:
            detail += f" ({column.comment})"
        lines.append(detail)
    if table.primary_key:
        lines.append("Primary key: " + ", ".join(table.primary_key))
    for key in table.foreign_keys:
        lines.append(
            f"Foreign key: {', '.join(key.columns)} -> "
            f"{key.target.key} ({', '.join(key.target_columns)})"
        )
    return "\n".join(lines)


def serialize_chunks(table: TableSchema, *, columns_per_chunk: int = 12) -> list[str]:
    if columns_per_chunk < 1:
        raise ValueError("columns_per_chunk must be positive")
    chunks = []
    for offset in range(0, len(table.columns), columns_per_chunk):
        columns = table.columns[offset : offset + columns_per_chunk]
        names = {column.name for column in columns}
        chunk = replace(
            table,
            columns=columns,
            primary_key=tuple(key for key in table.primary_key if key in names),
            foreign_keys=tuple(
                key for key in table.foreign_keys if names.intersection(key.columns)
            ),
        )
        chunks.append(serialize_table(chunk))
    return chunks


def encode_metadata(table: TableSchema) -> dict:
    return {
        "format_version": 1,
        "schema_id": table.ref.schema,
        "table_name": table.ref.name,
        "table_json": json.dumps(asdict(table), ensure_ascii=False),
    }


def decode_metadata(metadata: dict) -> TableSchema:
    try:
        if metadata["format_version"] != 1:
            raise ValueError("Unsupported schema document version")
        data = json.loads(metadata["table_json"])
        table = TableSchema(
            ref=TableRef(**data["ref"]),
            columns=tuple(Column(**column) for column in data["columns"]),
            primary_key=tuple(data["primary_key"]),
            foreign_keys=tuple(
                ForeignKey(
                    columns=tuple(key["columns"]),
                    target=TableRef(**key["target"]),
                    target_columns=tuple(key["target_columns"]),
                )
                for key in data["foreign_keys"]
            ),
            ddl=data["ddl"],
            description=data["description"],
        )
        if table.ref.schema != metadata["schema_id"] or table.ref.name != metadata["table_name"]:
            raise ValueError("Schema document identity mismatch")
        return table
    except (KeyError, TypeError, ValueError) as exc:
        raise QueryError(
            "index_outdated",
            "Индекс схемы устарел или повреждён. Выполните переиндексацию.",
        ) from exc
