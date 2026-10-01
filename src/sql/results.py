from base64 import b64encode
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
import json
import math
from typing import Any
from uuid import UUID

from src.domain.query import QueryError


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return {"days": value.days, "seconds": value.seconds, "microseconds": value.microseconds}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"encoding": "base64", "value": b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return json_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    raise QueryError("unsupported_result_type", f"Неподдерживаемый тип результата: {type(value).__name__}")


def encode_event(value: Any, *, max_bytes: int = 8 * 1024 * 1024) -> str:
    encoded = json.dumps(json_value(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > max_bytes:
        raise QueryError("result_too_large", "Ответ превышает допустимый размер. Уточните запрос.")
    return encoded + "\n"
