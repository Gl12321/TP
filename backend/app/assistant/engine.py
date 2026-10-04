import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path

from sql_agent.contracts import AgentResult

from backend.app.infrastructure.errors import AppError
from backend.app.sources.service import ScopedExecutor, SourceConnections, authorized_catalog


@dataclass
class EngineResult(AgentResult):
    execution: dict | None = None


def with_evidence(result, executor):
    return EngineResult(
        **{field.name: getattr(result, field.name) for field in fields(AgentResult)},
        execution=executor.execution,
    )


class BoundCatalog:
    def __init__(self, source, run, check_access):
        self.source = source
        self.run = run
        self.check_access = check_access

    async def snapshot(self, context):
        from sql_agent.contracts import CatalogSnapshot, QueryError

        context.check_cancelled()
        try:
            await self.check_access()
        except AppError as error:
            raise QueryError(error.code, error.message) from error
        scope = hashlib.sha256(json.dumps(sorted(self.run.store_ids)).encode()).hexdigest()[:16]
        namespace = f"{self.run.workspace_id}:{self.source.id}:{scope}"
        return CatalogSnapshot(
            namespace, str(self.source.catalog_version), authorized_catalog(self.source)
        )


class LocalEngine:
    def __init__(self, settings):
        from runtime.config import load_config, resolve_models
        from sql_agent.adapters.embedding import TableEmbedder
        from sql_agent.adapters.llama import LLMClient
        from sql_agent.adapters.reranker import TableReranker
        from sql_agent.contracts import AgentSettings
        from sql_agent.retrieval.semantic import SemanticRetriever
        import os

        config = load_config(settings.config_path)
        self.model = os.getenv("MODEL_PRESET") or config["model"]
        models = resolve_models(config, self.model, root=settings.config_path.resolve().parent)
        limits = config["settings"]
        embedder = TableEmbedder(
            models["embedder"], batch_size=limits.get("EMBEDDING_BATCH_SIZE", 8)
        )
        self.reranker = TableReranker(
            models["reranker"], batch_size=limits.get("RERANKER_BATCH_SIZE", 8)
        )
        self.retriever = SemanticRetriever(
            embedder,
            top_k=limits.get("RETRIEVAL_TOP_K", 10),
            cache_path=Path(os.getenv("APP_INDEX_DIR", ".runtime/indexes")),
        )
        self.generator = LLMClient(
            models["llm"],
            timeout_seconds=limits.get("LLM_TIMEOUT_SECONDS", 600),
            min_free_memory_mb=limits.get("MIN_FREE_MEMORY_MB", 2048),
            runtime_memory_mb=models["llm"].get("runtime_memory_mb", 1024),
        )
        self.agent_settings = AgentSettings(
            max_corrections=limits.get("MAX_CORRECTIONS", 3),
            max_tables=limits.get("CONTEXT_MAX_TABLES", 12),
            top_k=limits.get("RETRIEVAL_TOP_K", 10),
            timeout_seconds=limits.get("LLM_TIMEOUT_SECONDS", 600),
        )
        self.settings = settings
        self.connections = SourceConnections(total=2, per_source=1)

    async def run(self, run, source, stores, metrics, base, context, check_access):
        from sql_agent import SQLAgent
        from sql_agent.contracts import (
            AgentRequest,
            AgentResult,
            ConversationContext,
            MetricDefinition,
            TableRef,
        )

        executor = ScopedExecutor(
            source, [store.code for store in stores], self.settings, check_access, self.connections
        )
        if run.kind == "refresh":
            result = await executor.execute(run.sql, context)
            return with_evidence(AgentResult("success", result=result, sql=result.sql), executor)
        definitions = tuple(
            MetricDefinition(
                metric.key,
                metric.name,
                metric.description,
                metric.unit,
                str(metric.version),
                (TableRef(metric.table_schema, metric.table_name),),
                calculation={
                    "aggregation": metric.aggregation,
                    "value_column": metric.value_column,
                    "date_column": metric.date_column,
                    "store_column": metric.store_column,
                },
            )
            for metric in metrics
        )
        conversation = None
        question = run.question
        if base:
            if base.status == "needs_input":
                question = (
                    f"Исходный вопрос: {base.question}\nУточнение пользователя: {run.question}"
                )
            else:
                conversation = ConversationContext(base.question, base.sql or "", base.context)
        filters = {
            **{key: value for key, value in run.context.items() if value is not None},
            "date_to_inclusive": True,
            "stores": [{"code": store.code, "name": store.name} for store in stores],
        }
        if run.context.get("metric_id") and metrics:
            filters["metric_key"] = metrics[0].key
        request = AgentRequest(question, conversation, definitions, filters=filters)
        agent = SQLAgent(
            BoundCatalog(source, run, check_access),
            self.generator,
            executor,
            settings=self.agent_settings,
            retriever=self.retriever,
            reranker=self.reranker,
        )
        return with_evidence(await agent.run(request, context), executor)

    async def close(self):
        await self.generator.close()
