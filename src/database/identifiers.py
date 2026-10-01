def quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Идентификатор должен быть непустой строкой без NUL.")
    if len(value.encode("utf-8")) > 63:
        raise ValueError("Идентификатор PostgreSQL превышает 63 байта UTF-8.")
    return '"' + value.replace('"', '""') + '"'


def is_user_schema(value: str) -> bool:
    return value not in {"information_schema", "public"} and not value.startswith("pg_")


def validate_schema_name(value: str) -> str:
    quote_identifier(value)
    if not is_user_schema(value):
        raise ValueError("Системные схемы и public недоступны для этой операции.")
    return value
