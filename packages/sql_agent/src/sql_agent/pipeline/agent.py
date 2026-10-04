import asyncio
from dataclasses import replace
import json
import re
import time

from sql_agent.contracts import (
    AgentRequest,
    AgentResult,
    AgentSettings,
    QueryContext,
    QueryError,
    RetrievedTable,
    TableRef,
)
from sql_agent.generation.prompts import build_messages
from sql_agent.ports import AllowedCatalog, ReadOnlyExecutor, SQLGenerator
from sql_agent.retrieval.context import select_context
from sql_agent.retrieval.serializer import serialize_table
from sql_agent.sql.grammar import SQLGrammarBuilder
from sql_agent.sql.validation import SQLValidator

from .correction import CorrectionPolicy


_SIGNALS = {
    "INSUFFICIENT_CONTEXT": (
        "not_found",
        "insufficient_context",
        "Недостаточно данных для расчёта. Уточните показатель и условия.",
    ),
    "UNSUPPORTED_QUERY": (
        "not_found",
        "unsupported_query",
        "Этот вопрос нельзя выразить поддерживаемым аналитическим SQL.",
    ),
    "CLARIFY_PERIOD": (
        "clarification",
        "clarify_period",
        "За какой период выполнить расчёт и с каким периодом сравнить?",
    ),
    "CLARIFY_METRIC": (
        "clarification",
        "clarify_metric",
        "Какой показатель нужен и как его следует рассчитывать?",
    ),
}


class SQLAgent:
    def __init__(
        self,
        catalog: AllowedCatalog,
        generator: SQLGenerator,
        executor: ReadOnlyExecutor,
        *,
        settings: AgentSettings | None = None,
        retriever=None,
        reranker=None,
    ):
        self.catalog = catalog
        self.generator = generator
        self.executor = executor
        self.settings = settings or AgentSettings()
        self.retriever = retriever
        self.reranker = reranker
        self.validator = SQLValidator()
        self.correction = CorrectionPolicy(self.settings.max_corrections)

    async def run(self, request: AgentRequest, context: QueryContext | None = None) -> AgentResult:
        context = context or QueryContext()
        deadline = time.monotonic() + self.settings.timeout_seconds
        context = replace(
            context,
            deadline=min(context.deadline, deadline) if context.deadline is not None else deadline,
        )
        progress = {"attempts": 0, "sql": None, "version": None, "tables": ()}
        try:
            self._validate_request(request)
            context.check_cancelled()
            async with asyncio.timeout(max(0, context.deadline - time.monotonic())):
                return await self._run(request, context, progress)
        except TimeoutError:
            context.cancelled.set()
            return AgentResult(
                "error",
                error="Превышено общее время обработки запроса.",
                error_code="query_timeout",
                attempts=progress["attempts"],
                sql=progress["sql"],
                catalog_version=progress["version"],
                tables=progress["tables"],
            )
        except QueryError as exc:
            return AgentResult(
                "cancelled" if exc.code == "cancelled" else "error",
                error=str(exc),
                error_code=exc.code,
                attempts=progress["attempts"],
                sql=progress["sql"],
                catalog_version=progress["version"],
                tables=progress["tables"],
            )

    def _validate_request(self, request):
        if not isinstance(request.question, str) or not request.question.strip():
            raise QueryError("invalid_question", "Введите вопрос к данным.")
        if len(request.question) > self.settings.max_question_chars:
            raise QueryError("invalid_question", "Вопрос превышает допустимую длину.")
        try:
            if not isinstance(request.filters, dict):
                raise TypeError("Filters must be an object")
            filter_size = len(json.dumps(request.filters, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise QueryError(
                "invalid_context", "Условия вопроса имеют некорректный формат."
            ) from exc
        if filter_size > self.settings.max_context_chars:
            raise QueryError("context_limit", "Условия вопроса слишком велики.")
        if request.conversation:
            try:
                size = (
                    len(request.conversation.question)
                    + len(request.conversation.sql)
                    + len(json.dumps(request.conversation.filters, allow_nan=False))
                )
            except (TypeError, ValueError) as exc:
                raise QueryError(
                    "invalid_context", "Условия диалога имеют некорректный формат."
                ) from exc
            if size > self.settings.max_context_chars:
                raise QueryError("context_limit", "Основание диалога слишком велико.")
        try:
            metrics_size = sum(
                len(item.definition)
                + len(item.name)
                + len(item.key)
                + len(item.unit)
                + len(json.dumps(item.calculation, ensure_ascii=False, allow_nan=False))
                for item in request.metrics
            )
        except (TypeError, ValueError) as exc:
            raise QueryError(
                "invalid_context", "Определения показателей имеют некорректный формат."
            ) from exc
        if metrics_size > self.settings.max_context_chars:
            raise QueryError(
                "context_limit", "Определения показателей слишком велики для одного вопроса."
            )

    async def _rank(self, question, snapshot, context):
        context.check_cancelled()
        if self.retriever is None:
            terms = set(re.findall(r"\w+", question.casefold()))
            documents = [
                RetrievedTable(
                    table,
                    len(terms.intersection(re.findall(r"\w+", serialize_table(table).casefold()))),
                )
                for table in snapshot.tables
            ]
            documents.sort(key=lambda item: (-item.score, item.table.ref))
            documents = documents[: self.settings.top_k]
        else:
            documents = await self.retriever.retrieve(question, snapshot, context)
        allowed = {table.ref: table for table in snapshot.tables}
        if any(
            item.table.ref not in allowed or item.table != allowed[item.table.ref]
            for item in documents
        ):
            raise QueryError(
                "index_outdated", "Результат поиска не соответствует разрешённому каталогу."
            )
        if self.reranker is not None:
            documents = await self.reranker.rerank(question, documents, context)
        return documents

    def _messages(self, request, tables, **correction):
        return build_messages(
            request.question,
            tables,
            conversation=request.conversation,
            metrics=request.metrics,
            filters=request.filters,
            allow_refusal=True,
            **correction,
        )

    def _fits(self, request, tables, **correction):
        reserve = self.settings.token_reserve + (
            0 if correction else self.settings.correction_token_reserve
        )
        return (
            self.generator.count_tokens(self._messages(request, tables, **correction))
            + self.generator.max_tokens
            + reserve
            <= self.generator.context_size
        )

    def _required_tables(self, request, snapshot):
        required = []
        metric_key = request.filters.get("metric_key")
        if metric_key is not None:
            metrics = [metric for metric in request.metrics if metric.key == metric_key]
            if not metrics:
                raise QueryError(
                    "metric_not_allowed",
                    "Выбранный показатель отсутствует среди доступных определений.",
                )
            required.extend(ref for metric in metrics for ref in metric.tables)
        if request.conversation is None:
            return tuple(dict.fromkeys(required))
        try:
            normalized = self.validator.validate(request.conversation.sql, snapshot.tables)
        except QueryError as exc:
            raise QueryError(
                "context_not_allowed",
                "Предыдущий расчёт больше не соответствует доступным данным. Начните новый вопрос.",
            ) from exc
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(normalized, read="postgres")
        required.extend(TableRef(table.db, table.name) for table in tree.find_all(exp.Table))
        return tuple(dict.fromkeys(required))

    async def _run(self, request, context, progress):
        context.emit("retrieve", "Поиск подходящих таблиц")
        snapshot = await self.catalog.snapshot(context)
        progress["version"] = snapshot.version
        if not snapshot.tables:
            return AgentResult(
                "not_found",
                error="Нет доступных таблиц.",
                error_code="no_tables",
                catalog_version=snapshot.version,
            )
        allowed = {table.ref for table in snapshot.tables}
        if any(ref not in allowed for metric in request.metrics for ref in metric.tables):
            raise QueryError(
                "metric_not_allowed", "Определение показателя ссылается на недоступные данные."
            )
        required = self._required_tables(request, snapshot)
        search = request.question
        if request.filters:
            search += "\n" + json.dumps(request.filters, ensure_ascii=False)
        selected_metric = request.filters.get("metric_key")
        if selected_metric is not None:
            search += "\n" + "\n".join(
                f"{metric.name}: {metric.definition}"
                for metric in request.metrics
                if metric.key == selected_metric
            )
        if request.conversation is not None:
            search += "\n" + request.conversation.question + "\n" + request.conversation.sql
        ranked = await self._rank(search, snapshot, context)
        tables = select_context(
            snapshot,
            ranked,
            max_tables=self.settings.max_tables,
            fits=lambda values: self._fits(request, values),
            context=context,
            required=required,
        )
        if not tables:
            return AgentResult(
                "not_found",
                error="Подходящие таблицы не найдены.",
                error_code="no_tables",
                catalog_version=snapshot.version,
            )
        messages = self._messages(request, tables)
        failed = []
        refreshed = False
        while True:
            context.check_cancelled()
            progress["tables"] = tuple(table.ref.key for table in tables)
            context.emit(
                "context",
                "Контекст подготовлен",
                tables=list(progress["tables"]),
                catalog_version=snapshot.version,
            )
            grammar = SQLGrammarBuilder.build(tables, allow_refusal=True)
            progress["attempts"] += 1
            context.emit("generate", "Генерация SQL", attempt=progress["attempts"])
            sql = (await self.generator.generate(messages, grammar, context)).strip()
            context.check_cancelled()
            if sql in _SIGNALS:
                status, code, message = _SIGNALS[sql]
                return AgentResult(
                    status,
                    error=message if status != "clarification" else None,
                    error_code=code,
                    attempts=progress["attempts"],
                    clarification=message if status == "clarification" else None,
                    catalog_version=snapshot.version,
                    tables=progress["tables"],
                )
            progress["sql"] = sql
            if self.correction.repeated(sql, failed):
                raise QueryError(
                    "repeated_sql", "Модель повторила неудачный запрос. Исправление остановлено."
                )
            try:
                context.emit("validate", "Проверка SQL")
                sql = self.validator.validate(sql, tables)
                context.check_cancelled()
                current = await self.catalog.snapshot(context)
                if current != snapshot:
                    raise QueryError(
                        "catalog_changed",
                        "Доступ или структура источника изменились. Повторите запрос.",
                    )
                context.emit("execute", "Выполнение запроса", sql=sql)
                result = await self.executor.execute(sql, context)
                context.check_cancelled()
                return AgentResult(
                    "success",
                    result=result,
                    attempts=progress["attempts"],
                    sql=sql,
                    catalog_version=snapshot.version,
                    tables=progress["tables"],
                )
            except QueryError as exc:
                if not self.correction.should_retry(
                    attempts=progress["attempts"], retryable=exc.retryable
                ):
                    raise
                failed.append(progress["sql"])
                context.emit(
                    "correct", "Исправление SQL", attempt=progress["attempts"], reason=str(exc)
                )
                correction = {"previous_sql": sql, "error": str(exc)[:1000], "error_code": exc.code}
                changed = False
                if exc.code == "missing_context" and not refreshed:
                    refreshed = True
                    context.emit("retrieve", "Уточнение контекста по ошибке SQL")
                    extra = await self._rank(
                        f"{search}\nSQL: {sql}\n{str(exc)[:1000]}", snapshot, context
                    )
                    merged = {item.table.ref: item for item in [*ranked, *extra]}
                    ranked = sorted(merged.values(), key=lambda item: (-item.score, item.table.ref))
                updated = select_context(
                    snapshot,
                    ranked,
                    max_tables=self.settings.max_tables,
                    fits=lambda values: self._fits(request, values, **correction),
                    context=context,
                    required=required,
                )
                changed = {table.ref: table for table in updated} != {
                    table.ref: table for table in tables
                }
                if changed:
                    failed = []
                tables = updated
                messages = self._messages(request, tables, context_changed=changed, **correction)
