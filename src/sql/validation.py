from functools import lru_cache
from typing import Any, Sequence

from src.domain.query import QueryError
from src.domain.schema import TableRef, TableSchema


_ALLOWED_NODES = frozenset({
    "select", "from", "table", "tablealias", "identifier", "column", "star",
    "literal", "null", "boolean", "distinct", "alias", "join", "where",
    "group", "having", "order", "ordered", "limit", "offset", "paren",
    "and", "or", "not", "eq", "neq", "gt", "gte", "lt", "lte", "like",
    "ilike", "between", "in", "is", "add", "sub", "mul", "div", "mod",
    "neg", "case", "if", "coalesce", "nullif", "count", "sum", "avg",
    "min", "max", "extract", "timestamptrunc", "datetrunc", "currentdate",
    "cast", "datatype", "interval", "var",
})
_AGGREGATES = frozenset({"count", "sum", "avg", "min", "max"})
_DATE_UNITS = frozenset({
    "YEAR", "QUARTER", "MONTH", "WEEK", "DAY", "HOUR", "MINUTE", "SECOND",
})
_EXTRACT_UNITS = _DATE_UNITS | {"DOW", "DOY", "EPOCH"}


def _invalid(message: str, *, code: str = "invalid_sql") -> None:
    raise QueryError(code, message, retryable=True)


@lru_cache(maxsize=1)
def _postgres_dialect():
    from sqlglot.dialects.postgres import Postgres

    def date_trunc(arguments):
        if len(arguments) != 2:
            _invalid("DATE_TRUNC допускает ровно два аргумента: период и дату.")
        return Postgres.Parser.FUNCTIONS["DATE_TRUNC"](arguments)

    class AnalyticsPostgres(Postgres):
        class Parser(Postgres.Parser):
            FUNCTIONS = {**Postgres.Parser.FUNCTIONS, "DATE_TRUNC": date_trunc}

    return AnalyticsPostgres()


class SQLValidator:


    def __init__(self, *, max_sql_chars: int = 32_768, max_ast_depth: int = 100):
        self.max_sql_chars = max_sql_chars
        self.max_ast_depth = max_ast_depth

    def validate(self, sql: str, tables: Sequence[TableSchema]) -> str:
        if not isinstance(sql, str) or not sql.strip():
            _invalid("Модель вернула пустой SQL.")
        if len(sql) > self.max_sql_chars or "\x00" in sql:
            _invalid("SQL слишком длинный или содержит нулевой символ.")
        try:
            import sqlglot
            from sqlglot import exp
            from sqlglot.errors import ParseError, TokenError
        except ImportError as exc:
            raise QueryError(
                "missing_dependency", "Для проверки SQL требуется пакет sqlglot.",
            ) from exc
        try:
            statements = sqlglot.parse(sql, read=_postgres_dialect())
        except (ParseError, TokenError, RecursionError, ValueError) as exc:
            _invalid(f"SQL не удалось разобрать: {str(exc)[:500]}")


        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            _invalid("Разрешён ровно один SELECT без CTE и подзапросов.")
        tree = statements[0]
        self._check_nodes(tree, exp)


        for identifier in tree.find_all(exp.Identifier):
            name = identifier.name if identifier.args.get("quoted") else identifier.name.lower()
            try:
                valid_length = 0 < len(name.encode("utf-8")) <= 63
            except UnicodeEncodeError:
                valid_length = False
            if not valid_length:
                _invalid("SQL-идентификатор пуст или превышает 63 байта.")
            identifier.set("this", name)
            identifier.set("quoted", True)

        catalog = {table.ref: table for table in tables}
        if not catalog or len(catalog) != len(tables):
            _invalid("Контекст таблиц пуст или содержит повторения.")
        scope, join_scopes = self._sources(tree, catalog, exp)
        aliases = self._output_aliases(tree, exp)
        for expression in tree.expressions:
            self._resolve_columns(expression, scope, {}, exp)
        self._expand_ordinals(tree, exp)
        for clause in ("where", "group", "having"):
            expression = tree.args.get(clause)
            if expression is not None:
                self._resolve_columns(expression, scope, {}, exp)
        order = tree.args.get("order")
        if order is not None:
            for ordered in order.expressions:


                output = aliases if isinstance(ordered.this, exp.Column) else {}
                self._resolve_columns(ordered, scope, output, exp)
        for predicate, visible in join_scopes:
            self._resolve_columns(predicate, visible, {}, exp)
        self._check_aggregates(tree, aliases, exp)
        self._check_limits(tree, exp)
        return tree.sql(dialect="postgres", pretty=False, comments=False)

    def _check_nodes(self, tree: Any, exp: Any) -> None:
        pending = [(tree, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > self.max_ast_depth:
                _invalid("SQL содержит слишком глубокую вложенность выражений.")
            if node.key not in _ALLOWED_NODES:
                _invalid(f"Конструкция {node.key} не поддерживается аналитическим SQL.")
            if isinstance(node, exp.Select) and node is not tree:
                _invalid("Подзапросы пока не поддерживаются.")
            if node.key == "distinct" and node.args.get("on") is not None:
                _invalid("DISTINCT ON пока не поддерживается.")
            if node.key == "if" and not isinstance(node.parent, exp.Case):
                _invalid("Используйте CASE WHEN вместо IF.")
            if node.key == "currentdate" and node.args.get("this") is not None:
                _invalid("CURRENT_DATE используется без аргументов.")
            if node.key == "cast":
                target = node.args.get("to")
                target_name = target.sql(dialect="postgres").upper() if target is not None else ""
                if (
                    target_name not in {"DATE", "TIMESTAMP"}
                    or not isinstance(node.this, exp.Literal)
                    or not node.this.is_string
                ):
                    _invalid("Допускаются только строковые литералы DATE и TIMESTAMP.")
            if node.key in {"timestamptrunc", "datetrunc"}:
                unit = node.args.get("unit")
                if (
                    unit is None or unit.name.upper() not in _DATE_UNITS
                    or node.args.get("zone") is not None
                ):
                    _invalid("Недопустимая единица DATE_TRUNC.")
            if node.key == "extract" and node.this.name.upper() not in _EXTRACT_UNITS:
                _invalid("Недопустимая единица EXTRACT.")
            if node.key == "interval" and not (isinstance(node.this, exp.Literal) and node.this.is_string):
                _invalid("INTERVAL должен содержать строковый литерал.")
            if node.key == "var":
                parent = node.parent
                if parent is None or parent.key not in {
                    "interval", "extract", "timestamptrunc", "datetrunc",
                }:
                    _invalid("Недопустимое служебное выражение SQL.")
            if node.key == "in":
                if not node.expressions or any(not self._is_literal(item, exp) for item in node.expressions):
                    _invalid("IN допускает только непустой список литералов.")
            if isinstance(node, exp.Star):
                parent = node.parent
                direct = parent is tree
                qualified = isinstance(parent, exp.Column) and parent.parent is tree
                count = isinstance(parent, exp.Count) and parent.this is node and not parent.expressions
                if not (direct or qualified or count) or any(node.args.values()):
                    _invalid("Звёздочка разрешена в SELECT и COUNT(*).")
            pending.extend((child, depth + 1) for child in node.iter_expressions())

    @staticmethod
    def _is_literal(node: Any, exp: Any) -> bool:
        if isinstance(node, (exp.Literal, exp.Null, exp.Boolean)):
            return True
        if isinstance(node, exp.Neg):
            return isinstance(node.this, exp.Literal) and not node.this.is_string
        return node.key in {"cast", "interval"}

    @staticmethod
    def _sources(
        tree: Any, catalog: dict[TableRef, TableSchema], exp: Any,
    ) -> tuple[dict[str, TableSchema], list[tuple[Any, dict[str, TableSchema]]]]:
        from_clause = tree.args.get("from_") or tree.args.get("from")
        if from_clause is None or not isinstance(from_clause.this, exp.Table) or from_clause.expressions:
            _invalid("Нужна таблица FROM с явно указанной схемой.")
        scope: dict[str, TableSchema] = {}
        seen: set[TableRef] = set()
        join_scopes: list[tuple[Any, dict[str, TableSchema]]] = []

        def add(table: Any) -> None:
            if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier):
                _invalid("Источником данных должна быть таблица.")
            if not table.db or table.catalog:
                _invalid("Каждая таблица должна иметь имя schema.table без имени базы данных.")
            ref = TableRef(table.db, table.name)
            if ref.schema.lower() == "information_schema" or ref.schema.lower().startswith("pg_"):
                _invalid("Доступ к системным схемам запрещён.")
            if ref not in catalog:
                _invalid(f"Таблица {ref.key} отсутствует в выбранном контексте.", code="missing_context")
            if ref in seen:
                _invalid("Повторное соединение таблицы с самой собой пока не поддерживается.")
            alias = table.alias_or_name
            if alias in scope:
                _invalid(f"Псевдоним таблицы {alias} повторяется.")
            if table.alias_column_names:
                _invalid("Переименование колонок в псевдониме таблицы запрещено.")
            seen.add(ref)
            scope[alias] = catalog[ref]

        add(from_clause.this)
        for join in tree.args.get("joins") or []:
            side = str(join.args.get("side") or "").upper()
            kind = str(join.args.get("kind") or "").upper()
            if (
                side not in {"", "LEFT"} or kind not in {"", "INNER", "OUTER"}
                or (kind == "OUTER" and side != "LEFT")
            ):
                _invalid("Поддерживаются только INNER JOIN и LEFT JOIN.")
            if join.args.get("method") or join.args.get("using") or join.args.get("on") is None:
                _invalid("JOIN должен содержать явное условие ON.")
            add(join.this)
            join_scopes.append((join.args["on"], dict(scope)))
        return scope, join_scopes

    @staticmethod
    def _output_aliases(tree: Any, exp: Any) -> dict[str, Any]:
        aliases: dict[str, Any] = {}
        for expression in tree.expressions:
            if isinstance(expression, exp.Alias):
                alias = expression.alias
                if alias in aliases:
                    _invalid(f"Выходной псевдоним {alias} повторяется.")
                aliases[alias] = expression.this
        return aliases

    @staticmethod
    def _resolve_columns(
        expression: Any, scope: dict[str, TableSchema],
        output_aliases: dict[str, Any], exp: Any,
    ) -> None:
        for column in expression.find_all(exp.Column):
            if column.catalog:
                _invalid("Ссылки на другую базу данных запрещены.")
            if column.table:
                table = scope.get(column.table)
                if table is None:
                    _invalid(f"Таблица или псевдоним {column.table} не определены в этом выражении.")
                if column.db and (column.db != table.ref.schema or column.table != table.ref.name):
                    _invalid("Квалификатор колонки не соответствует источнику данных.")
                if not column.is_star and column.name not in table.column_names:
                    _invalid(f"В таблице {table.ref.key} нет колонки {column.name}.", code="missing_context")
                column.set("db", None)
            elif column.db:
                _invalid("Колонка со схемой должна также указывать таблицу.")
            elif column.name in output_aliases:
                continue
            else:
                matches = [table for table in scope.values() if column.name in table.column_names]
                if len(matches) != 1:
                    _invalid(f"Колонка {column.name} отсутствует или неоднозначна; укажите псевдоним таблицы.")


                alias = next(alias for alias, table in scope.items() if table is matches[0])
                column.set("table", exp.to_identifier(alias, quoted=True))

    @staticmethod
    def _expand_ordinals(tree: Any, exp: Any) -> None:

        def expand(value: Any) -> Any:
            if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
                return value
            if len(value.this) > len(str(len(tree.expressions))):
                _invalid("Номер выражения GROUP BY/ORDER BY выходит за список SELECT.")
            position = int(value.this)
            if position < 1 or position > len(tree.expressions):
                _invalid("Номер выражения GROUP BY/ORDER BY выходит за список SELECT.")
            selected = tree.expressions[position - 1]
            selected = selected.this if isinstance(selected, exp.Alias) else selected
            if any(
                isinstance(item, exp.Star) or (isinstance(item, exp.Column) and item.is_star)
                for item in tree.expressions
            ):
                _invalid("Номера GROUP BY/ORDER BY нельзя использовать вместе с SELECT *.")
            return selected.copy()

        group = tree.args.get("group")
        if group is not None:
            group.set("expressions", [expand(value) for value in group.expressions])
        order = tree.args.get("order")
        if order is not None:
            for ordered in order.expressions:
                ordered.set("this", expand(ordered.this))

    @staticmethod
    def _check_aggregates(tree: Any, aliases: dict[str, Any], exp: Any) -> None:
        def aggregates(node: Any) -> list[Any]:
            return [item for item in node.walk() if item.key in _AGGREGATES]

        forbidden = [tree.args.get("where"), tree.args.get("group")]
        forbidden.extend(join.args.get("on") for join in tree.args.get("joins") or [])
        if any(aggregates(node) for node in forbidden if node is not None):
            _invalid("Агрегаты запрещены в WHERE, JOIN ON и GROUP BY.")
        all_aggregates = aggregates(tree)
        for aggregate in all_aggregates:
            if any(item is not aggregate for item in aggregates(aggregate)):
                _invalid("Вложенные агрегатные функции запрещены.")
            if aggregate.expressions:
                _invalid("Агрегатная функция принимает один аргумент.")
            if aggregate.key != "count" and isinstance(aggregate.this, exp.Distinct):
                _invalid("DISTINCT в аргументе поддерживается только для COUNT.")
            if isinstance(aggregate.this, exp.Distinct) and len(aggregate.this.expressions) != 1:
                _invalid("COUNT(DISTINCT ...) принимает одно выражение.")
        group = tree.args.get("group")
        having = tree.args.get("having")
        grouped = group is not None or bool(all_aggregates) or having is not None
        if not grouped:
            return
        group_expressions = list(group.expressions) if group is not None else []

        def ensure_grouped(node: Any) -> None:
            if node.key in _AGGREGATES:
                return
            if any(node == item for item in group_expressions):
                return
            if isinstance(node, exp.Star) or (isinstance(node, exp.Column) and node.is_star):
                _invalid("SELECT * нельзя смешивать с группировкой или агрегатами.")
            if isinstance(node, exp.Column):
                _invalid(f"Колонка {node.sql(dialect='postgres')} должна входить в GROUP BY или агрегат.")
            for child in node.iter_expressions():
                ensure_grouped(child)

        for expression in tree.expressions:
            ensure_grouped(expression.this if isinstance(expression, exp.Alias) else expression)
        if having is not None:
            ensure_grouped(having.this)
        order = tree.args.get("order")
        if order is not None:
            for ordered in order.expressions:
                value = ordered.this
                if isinstance(value, exp.Column) and not value.table and value.name in aliases:
                    value = aliases[value.name]
                ensure_grouped(value)

    @staticmethod
    def _check_limits(tree: Any, exp: Any) -> None:
        for clause in ("limit", "offset"):
            node = tree.args.get(clause)
            if node is None:
                continue
            value = node.expression
            if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
                _invalid(f"{clause.upper()} должен быть целым неотрицательным числом.")
            if len(value.this) > 10 or int(value.this) > 2_147_483_647:
                _invalid(f"{clause.upper()} превышает допустимый предел.")
