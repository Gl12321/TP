from collections import deque
from collections.abc import Callable, Sequence

from sql_agent.contracts import (
    CatalogSnapshot,
    QueryContext,
    QueryError,
    RetrievedTable,
    TableRef,
    TableSchema,
)


def select_context(
    snapshot: CatalogSnapshot,
    ranked: Sequence[RetrievedTable],
    *,
    max_tables: int,
    fits: Callable[[list[TableSchema]], bool],
    context: QueryContext,
    required: Sequence[TableRef] = (),
) -> list[TableSchema]:
    tables = {table.ref: table for table in snapshot.tables}
    graph = {ref: set() for ref in tables}
    for table in tables.values():
        for key in table.foreign_keys:
            if key.target in tables:
                graph[table.ref].add(key.target)
                graph[key.target].add(table.ref)
    selected: list[TableRef] = []
    for ref in dict.fromkeys(required):
        if ref not in tables:
            raise QueryError(
                "context_not_allowed", "Основание расчёта содержит недоступную таблицу."
            )
        path = shortest_path(ref, set(selected), graph)
        selected.extend(item for item in (path or [ref]) if item not in selected)
    if selected and (len(selected) > max_tables or not fits([tables[ref] for ref in selected])):
        raise QueryError(
            "context_limit",
            "Обязательные таблицы расчёта не помещаются в контекст модели. Уточните вопрос или увеличьте контекст.",
        )
    for item in ranked:
        context.check_cancelled()
        ref = item.table.ref
        if ref not in tables or item.table != tables[ref]:
            raise QueryError(
                "index_outdated", "Результат поиска не соответствует разрешённому каталогу."
            )
        if ref in selected:
            continue
        path = shortest_path(ref, set(selected), graph)
        proposed = [*selected, *[key for key in (path or [ref]) if key not in selected]]
        if len(proposed) <= max_tables and fits([tables[key] for key in proposed]):
            selected = proposed
    if not selected and ranked:
        raise QueryError(
            "context_limit",
            "Нужные таблицы не помещаются в контекст модели. Уточните вопрос или увеличьте контекст.",
        )
    return [tables[ref] for ref in selected]


def shortest_path(start: TableRef, targets: set[TableRef], graph) -> list[TableRef]:
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
