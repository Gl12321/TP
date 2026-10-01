from dataclasses import asdict
import json

from src.domain.query import QueryError
from src.domain.schema import Column, ForeignKey, TableRef, TableSchema


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
        lines.append(f"Foreign key: {', '.join(key.columns)} -> "
                     f"{key.target.key} ({', '.join(key.target_columns)})")
    return "\n".join(lines)


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
            foreign_keys=tuple(ForeignKey(
                columns=tuple(key["columns"]), target=TableRef(**key["target"]),
                target_columns=tuple(key["target_columns"]),
            ) for key in data["foreign_keys"]),
            ddl=data["ddl"], description=data["description"],
        )
        if (table.ref.schema != metadata["schema_id"]
                or table.ref.name != metadata["table_name"]):
            raise ValueError("Schema document identity mismatch")
        return table
    except (KeyError, TypeError, ValueError) as exc:
        raise QueryError(
            "index_outdated", "Индекс схемы устарел или повреждён. Выполните переиндексацию.",
        ) from exc
