from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import Any, Callable
from uuid import uuid4


class GenerationRefusal(str, Enum):
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    UNSUPPORTED_QUERY = "UNSUPPORTED_QUERY"


class QueryError(Exception):


    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class QueryEvent:
    event: str
    request_id: str
    content: dict[str, Any]


@dataclass
class QueryContext:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: Event = field(default_factory=Event)
    on_event: Callable[[QueryEvent], None] | None = None

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise QueryError("cancelled", "Запрос отменён.")

    def emit(self, stage: str, message: str, **details: Any) -> None:
        self.check_cancelled()
        if self.on_event is not None:
            self.on_event(QueryEvent("stage", self.request_id, {
                "stage": stage, "message": message, **details,
            }))


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


@dataclass
class AgentResult:
    status: str
    result: QueryResult | None = None
    error: str | None = None
    error_code: str | None = None
    attempts: int = 0
    sql: str | None = None
