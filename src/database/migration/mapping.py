import re

from sqlalchemy import MetaData, text
from sqlalchemy import types
from sqlalchemy.schema import DefaultClause

from src.database.identifiers import quote_identifier


_LITERAL_DEFAULT = re.compile(
    r"(?:NULL|TRUE|FALSE|CURRENT_DATE|CURRENT_TIME|CURRENT_TIMESTAMP|"
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|'(?:[^']|'')*')",
    re.IGNORECASE,
)


def _default(value: str) -> str:
    value = value.strip()
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    if _LITERAL_DEFAULT.fullmatch(value):
        return value
    if re.fullmatch(r"[xX]'(?:[a-fA-F0-9]{2})*'", value):
        return "'\\x" + value[2:-1] + "'::bytea"
    raise ValueError(f"SQLite DEFAULT требует явного преобразования: {value!r}.")


def _type(value):
    if isinstance(value, types.Boolean):
        return types.Boolean()
    if isinstance(value, types.Integer):
        return types.BigInteger()
    if isinstance(value, types.Float):
        return types.Float(precision=53)
    if isinstance(value, types.Numeric):


        return types.Numeric()
    if isinstance(value, types.DateTime):
        return types.DateTime(timezone=value.timezone)
    if isinstance(value, types.Date):
        return types.Date()
    if isinstance(value, types.Time):
        return types.Time(timezone=value.timezone)
    if isinstance(value, types.LargeBinary):
        return types.LargeBinary()
    if isinstance(value, types.JSON):
        return types.JSON()
    if isinstance(value, types.String):

        return types.Text()
    raise ValueError(f"Тип SQLite {value!s} не поддерживается для переноса.")


def clone_metadata(source: MetaData, schema: str) -> MetaData:
    target = MetaData()

    def target_schema(table, destination_schema, constraint, referred_schema):
        if referred_schema not in {None, "main"}:
            raise ValueError("Внешние ключи на подключённые SQLite-базы не поддерживаются.")
        return schema

    for original in source.tables.values():
        table = original.to_metadata(target, schema=schema, referred_schema_fn=target_schema)
        quote_identifier(table.name)
        for column in table.columns:
            quote_identifier(column.name)
            column.type = _type(column.type)
            if column.server_default is not None:
                if not isinstance(column.server_default, DefaultClause):
                    raise ValueError("Вычисляемые значения SQLite требуют явного преобразования.")
                column.server_default = DefaultClause(text(_default(str(column.server_default.arg))))
        for constraint in table.constraints:
            if constraint.name is not None:
                quote_identifier(constraint.name)
        for index in table.indexes:
            if index.name is not None:
                quote_identifier(index.name)
            predicate = index.dialect_options["sqlite"].get("where")
            if predicate is not None:
                index.dialect_options["postgresql"]["where"] = predicate
    return target
