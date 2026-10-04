from typing import Protocol

from .contracts import CatalogSnapshot, QueryContext, QueryResult


class AllowedCatalog(Protocol):
    async def snapshot(self, context: QueryContext) -> CatalogSnapshot: ...


class SQLGenerator(Protocol):
    context_size: int
    max_tokens: int

    def count_tokens(self, messages: list[dict[str, str]]) -> int: ...

    async def generate(
        self, messages: list[dict[str, str]], grammar: str, context: QueryContext
    ) -> str: ...


class ReadOnlyExecutor(Protocol):
    async def execute(self, sql: str, context: QueryContext) -> QueryResult: ...
