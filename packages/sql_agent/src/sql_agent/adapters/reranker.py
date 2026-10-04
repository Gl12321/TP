import asyncio
import math
from threading import Event

from sql_agent.query import QueryContext, QueryError
from sql_agent.schema import RetrievedTable
from sql_agent.adapters.runtime import finish_in_thread, model_source
from sql_agent.retrieval.serializer import serialize_chunks


class TableReranker:
    def __init__(
        self,
        model_config: dict,
        threshold: float | None = None,
        batch_size: int = 8,
        *,
        scorer=None,
    ):
        if batch_size < 1 or (
            threshold is not None and (isinstance(threshold, bool) or not math.isfinite(threshold))
        ):
            raise ValueError("Expected positive batch_size and a finite threshold or None")
        self.threshold = threshold
        self.batch_size = batch_size
        self._lock = asyncio.Lock()
        self.activation = model_config.get("activation", "sigmoid")
        if self.activation not in {"sigmoid", "identity"}:
            raise ValueError("Reranker activation must be sigmoid or identity")
        self.max_length = int(model_config.get("max_length", 512))
        if self.max_length < 1:
            raise ValueError("max_length must be positive")
        if scorer is None:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            source, cache = model_source(model_config)
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    source,
                    cache_dir=cache,
                    local_files_only=True,
                    trust_remote_code=False,
                    revision=model_config.get("revision"),
                )
                self.model = (
                    AutoModelForSequenceClassification.from_pretrained(
                        source,
                        cache_dir=cache,
                        local_files_only=True,
                        trust_remote_code=False,
                        revision=model_config.get("revision"),
                    )
                    .to("cpu")
                    .eval()
                )
            except (OSError, ValueError) as exc:
                raise QueryError(
                    "model_unavailable",
                    "Локальная модель reranker недоступна. Подготовьте её файлы отдельно.",
                ) from exc
            self._torch = torch
            scorer = self._score_pairs
        self._scorer = scorer

    async def rerank(
        self, question: str, documents: list[RetrievedTable], context: QueryContext | None = None
    ) -> list[RetrievedTable]:
        if not documents:
            return []
        stop = Event()
        async with self._lock:
            return await finish_in_thread(
                self._rerank, question, documents, context, stop, on_cancel=stop.set
            )

    def _score_pairs(self, pairs):
        with self._torch.inference_mode():
            inputs = self.tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            logits = self.model(**inputs).logits
            if logits.ndim != 2 or logits.shape[1] != 1:
                raise QueryError(
                    "model_config", "Reranker должен возвращать один скаляр на пару текстов."
                )
            scores = logits[:, 0]
            if self.activation == "sigmoid":
                scores = self._torch.sigmoid(scores)
            return scores.cpu().tolist()

    def _rerank(self, question, documents, context, stop):
        chunks = [
            (index, text)
            for index, document in enumerate(documents)
            for text in serialize_chunks(document.table)
        ]
        best = {}
        for offset in range(0, len(chunks), self.batch_size):
            if stop.is_set():
                raise QueryError("cancelled", "Оценка таблиц отменена.")
            if context is not None:
                context.check_cancelled()
            batch = chunks[offset : offset + self.batch_size]
            pairs = [(question, text) for _, text in batch]
            scores = self._scorer(pairs)
            if len(scores) != len(batch):
                raise ValueError("Reranker returned an invalid batch size")
            if not all(math.isfinite(float(score)) for score in scores):
                raise QueryError("model_output", "Reranker вернул некорректную оценку.")
            for (index, _), score in zip(batch, scores, strict=True):
                best[index] = max(best.get(index, -math.inf), float(score))
        if context is not None:
            context.check_cancelled()
        ranked = [
            RetrievedTable(documents[index].table, score)
            for index, score in best.items()
            if self.threshold is None or score >= self.threshold
        ]
        return sorted(ranked, key=lambda document: (-document.score, document.table.ref))
