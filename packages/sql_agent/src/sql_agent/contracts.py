from dataclasses import dataclass, field
from typing import Any

from .query import (
    AgentResult,
    AgentStatus,
    GenerationRefusal,
    QueryContext,
    QueryError,
    QueryEvent,
    QueryResult,
)
from .schema import Column, ForeignKey, RetrievedTable, TableRef, TableSchema


@dataclass(frozen=True)
class CatalogSnapshot:
    namespace: str
    version: str
    tables: tuple[TableSchema, ...]

    def __post_init__(self):
        if not self.namespace or not self.version:
            raise ValueError("Catalog namespace and version must be nonempty")
        if len({table.ref for table in self.tables}) != len(self.tables):
            raise ValueError("Catalog contains duplicate table references")
        if any(not table.columns for table in self.tables):
            raise ValueError("Catalog tables must have visible columns")


@dataclass(frozen=True)
class ConversationContext:
    question: str
    sql: str
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    name: str
    definition: str
    unit: str = ""
    version: str = "1"
    tables: tuple[TableRef, ...] = ()
    calculation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRequest:
    question: str
    conversation: ConversationContext | None = None
    metrics: tuple[MetricDefinition, ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSettings:
    max_corrections: int = 3
    max_tables: int = 12
    top_k: int = 10
    token_reserve: int = 32
    correction_token_reserve: int = 256
    timeout_seconds: float = 600
    max_question_chars: int = 4000
    max_context_chars: int = 16000

    def __post_init__(self):
        if (
            isinstance(self.max_corrections, bool)
            or not isinstance(self.max_corrections, int)
            or self.max_corrections < 0
        ):
            raise ValueError("max_corrections must not be negative")
        for name in (
            "max_tables",
            "top_k",
            "token_reserve",
            "correction_token_reserve",
            "max_question_chars",
            "max_context_chars",
        ):
            if (
                isinstance(getattr(self, name), bool)
                or not isinstance(getattr(self, name), int)
                or getattr(self, name) < 1
            ):
                raise ValueError(f"{name} must be a positive integer")
        import math

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")


__all__ = [
    "AgentRequest",
    "AgentResult",
    "AgentStatus",
    "AgentSettings",
    "CatalogSnapshot",
    "Column",
    "ConversationContext",
    "ForeignKey",
    "GenerationRefusal",
    "MetricDefinition",
    "QueryContext",
    "QueryError",
    "QueryEvent",
    "QueryResult",
    "RetrievedTable",
    "TableRef",
    "TableSchema",
]
