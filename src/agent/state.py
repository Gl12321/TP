from typing import TypedDict

from src.domain.query import QueryContext, QueryResult
from src.domain.schema import RetrievedTable, TableSchema


class AgentState(TypedDict, total=False):
    context_refreshed: bool
    question: str
    schemas: tuple[str, ...]
    context: QueryContext
    documents: list[RetrievedTable]
    tables: list[TableSchema]
    messages: list[dict[str, str]]
    grammar: str
    sql: str | None
    failed_sql: list[str]
    attempts: int
    error: str | None
    error_code: str | None
    retryable: bool
    status: str
    result: QueryResult | None
