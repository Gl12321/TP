from collections import deque
from collections.abc import Sequence

from src.domain.query import QueryContext, QueryError
from src.domain.schema import RetrievedTable, TableRef, TableSchema


class ContextBuilder:
    def __init__(self, schema_reader, max_tables: int = 8):
        if max_tables < 1:
            raise ValueError("max_tables must be positive")
        self.schema_reader = schema_reader
        self.max_tables = max_tables

    async def build(self, ranked: list[RetrievedTable], allowed_schemas: Sequence[str],
                    context: QueryContext | None = None) -> list[TableSchema]:
        if not ranked or not allowed_schemas:
            return []
        if context is not None:
            context.check_cancelled()
        allowed = set(allowed_schemas)
        tables = {table.ref: table for table in
                  await self.schema_reader.get_tables(sorted(allowed))
                  if table.ref.schema in allowed}
        graph = {ref: set() for ref in tables}
        for table in tables.values():
            for key in table.foreign_keys:
                if key.target in tables:
                    graph[table.ref].add(key.target)
                    graph[key.target].add(table.ref)
        selected = []
        for candidate in ranked:
            if context is not None:
                context.check_cancelled()
            ref = candidate.table.ref
            if ref.schema not in allowed:
                raise QueryError("schema_not_allowed", "Поиск вернул таблицу вне выбранных схем.")
            if ref not in tables or candidate.table != tables[ref]:
                raise QueryError("index_outdated", "Структура БД изменилась. Обновите индекс схемы.")
            if ref in selected:
                continue

            path = self._shortest_path(ref, set(selected), graph)
            additions = [item for item in path if item not in selected] if path else [ref]
            if len(selected) + len(additions) > self.max_tables:
                raise QueryError(
                    "context_too_large",
                    "Таблицы и необходимые связи не помещаются в контекст. "
                    "Сузьте вопрос или список выбранных схем.",
                )
            selected.extend(additions)
        if context is not None:
            context.check_cancelled()
        return [tables[ref] for ref in selected]

    @staticmethod
    def _shortest_path(start: TableRef, targets: set[TableRef], graph) -> list[TableRef]:
        if not targets:
            return []
        pending = deque([start])
        previous: dict[TableRef, TableRef | None] = {start: None}
        while pending:
            ref = pending.popleft()
            if ref in targets:
                path = []
                while ref is not None:
                    path.append(ref)
                    ref = previous[ref]
                return path
            for neighbour in sorted(graph[ref]):
                if neighbour not in previous:
                    previous[neighbour] = ref
                    pending.append(neighbour)
        return []
