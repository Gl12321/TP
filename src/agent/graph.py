from src.agent.correction import CorrectionPolicy
from src.agent.state import AgentState
from src.domain.query import AgentResult, GenerationRefusal, QueryContext, QueryError
from src.llm.prompts import build_messages
from src.sql.grammar.builder import SQLGrammarBuilder


class SQLAgentGraph:
    def __init__(self, retriever, reranker, context_builder, generator, validator, executor,
                 *, max_corrections: int = 3):
        self.retriever = retriever
        self.reranker = reranker
        self.context_builder = context_builder
        self.generator = generator
        self.validator = validator
        self.executor = executor
        self.correction = CorrectionPolicy(max_corrections)
        self.graph = self._build_graph()

    def _build_graph(self):
        from langgraph.graph import END, StateGraph

        graph = StateGraph(AgentState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("context", self._context)
        graph.add_node("generate", self._generate)
        graph.add_node("execute", self._execute)
        graph.add_node("correct", self._correct)
        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "context")
        graph.add_conditional_edges("context", lambda state: "stop" if state.get("status") == "not_found" else "generate",
                                    {"stop": END, "generate": "generate"})
        graph.add_conditional_edges("generate", lambda state: "execute" if state.get("status") == "pending" else "stop",
                                    {"stop": END, "execute": "execute"})
        graph.add_conditional_edges("execute", self._after_execute, {"stop": END, "correct": "correct"})
        graph.add_edge("correct", "generate")
        return graph.compile()

    async def run(self, question: str, schemas: tuple[str, ...], context: QueryContext) -> AgentResult:
        context.check_cancelled()
        if not schemas:
            return AgentResult("not_found", error="Нет схем для поиска.", error_code="no_schemas")
        try:
            state = await self.graph.ainvoke({
                "question": question, "schemas": schemas, "context": context,
                "attempts": 0, "failed_sql": [], "status": "pending",
                "context_refreshed": False,
                "error": None, "error_code": None, "result": None,
            }, config={"recursion_limit": 12 + 4 * self.correction.max_corrections})
            return AgentResult(state["status"], state.get("result"), state.get("error"),
                               state.get("error_code"), state["attempts"], sql=state.get("sql"))
        except QueryError as exc:
            return AgentResult("cancelled" if exc.code == "cancelled" else "error",
                               error=str(exc), error_code=exc.code)

    async def _retrieve(self, state: AgentState):
        ctx = state["context"]
        ctx.emit("retrieve", "Поиск таблиц")
        docs = await self.retriever.retrieve(state["question"], state["schemas"], ctx)
        return {"documents": docs}

    async def _context(self, state: AgentState):
        ctx = state["context"]
        ctx.emit("rerank", "Оценка релевантности и связей таблиц")
        ranked = await self.reranker.rerank(state["question"], state["documents"], ctx)
        if not ranked:
            return {"status": "not_found", "error": "Подходящие таблицы не найдены.", "error_code": "no_tables"}
        tables = await self.context_builder.build(ranked, state["schemas"], ctx)
        if not tables:
            return {"status": "not_found", "error": "Контекст схемы пуст.", "error_code": "no_tables"}
        messages = build_messages(state["question"], tables, allow_refusal=True)
        self._check_budget(messages)
        ctx.emit("context", "Контекст подготовлен", tables=[table.ref.key for table in tables])
        return {"tables": tables, "messages": messages, "grammar": SQLGrammarBuilder.build(tables, allow_refusal=True)}

    def _check_budget(self, messages):
        used = self.generator.count_tokens(messages)
        if used + self.generator.max_tokens + 32 > self.generator.context_size:
            raise QueryError("context_limit", "Вопрос и схема превышают контекст модели. Выберите меньше схем.")

    async def _generate(self, state: AgentState):
        ctx = state["context"]
        attempt = state["attempts"] + 1
        ctx.emit("generate", "Генерация SQL", attempt=attempt)
        sql = await self.generator.generate(state["messages"], state["grammar"], ctx)
        refusals = {
            GenerationRefusal.INSUFFICIENT_CONTEXT.value: (
                "insufficient_context", "Недостаточно данных для однозначного SQL. Уточните метрику, условия или выбранные схемы.",
            ),
            GenerationRefusal.UNSUPPORTED_QUERY.value: (
                "unsupported_query", "Вопрос нельзя выполнить в поддерживаемом аналитическом SQL. Уточните или упростите запрос.",
            ),
        }
        if sql in refusals:
            code, message = refusals[sql]
            return {"attempts": attempt, "sql": None, "status": "not_found", "error_code": code, "error": message}
        if self.correction.repeated(sql, state["failed_sql"]):
            return {"attempts": attempt, "sql": sql, "status": "error", "error_code": "repeated_sql",
                    "error": "Модель повторила ранее неудачный запрос. Исправление остановлено."}
        return {"attempts": attempt, "sql": sql, "status": "pending"}

    async def _execute(self, state: AgentState):
        ctx = state["context"]
        ctx.emit("validate", "Проверка SQL")
        try:
            sql = self.validator.validate(state["sql"], state["tables"])
            ctx.emit("execute", "Выполнение запроса", sql=sql)
            result = await self.executor.execute(sql, ctx)
            return {"result": result, "status": "success", "error": None, "error_code": None, "sql": sql}
        except QueryError as exc:
            if exc.code == "cancelled":
                raise
            return {"status": "error", "error": str(exc), "error_code": exc.code,
                    "retryable": exc.retryable, "failed_sql": [*state["failed_sql"], state["sql"]]}

    def _after_execute(self, state: AgentState):
        if state["status"] == "success":
            return "stop"
        return "correct" if self.correction.should_retry(
            attempts=state["attempts"], retryable=state.get("retryable", False),
        ) else "stop"

    async def _correct(self, state: AgentState):
        ctx = state["context"]
        ctx.emit("correct", "Исправление SQL", attempt=state["attempts"], reason=state["error"])
        tables = state["tables"]
        updates = {}
        if state.get("error_code") == "missing_context" and not state.get("context_refreshed", False):
            ctx.emit("retrieve", "Уточнение контекста по ошибке SQL")
            search = f'{state["question"]}\nSQL: {state["sql"]}\n{state["error"]}'
            additional = await self.retriever.retrieve(search, state["schemas"], ctx)
            candidates = {item.table.ref: item for item in state["documents"]}
            candidates.update({item.table.ref: item for item in additional})
            ranked = await self.reranker.rerank(state["question"], list(candidates.values()), ctx)
            refreshed = await self.context_builder.build(ranked, state["schemas"], ctx)
            if refreshed:
                tables = refreshed
            updates = {"context_refreshed": True, "documents": list(candidates.values()),
                       "tables": tables, "grammar": SQLGrammarBuilder.build(tables, allow_refusal=True)}
            if {table.ref: table for table in tables} != {table.ref: table for table in state["tables"]}:
                updates["failed_sql"] = []
        messages = build_messages(state["question"], tables,
                                  previous_sql=state["sql"], error=state["error"],
                                  error_code=state.get("error_code"), context_changed="failed_sql" in updates,
                                  allow_refusal=True)
        self._check_budget(messages)
        return {**updates, "messages": messages, "status": "pending"}
