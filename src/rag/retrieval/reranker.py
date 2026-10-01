import asyncio
from threading import Event

from src.domain.query import QueryContext, QueryError
from src.domain.schema import RetrievedTable
from src.rag._runtime import finish_in_thread, model_source
from src.rag.indexing.serializer import serialize_table


class TableReranker:
    def __init__(self, model_config: dict, threshold: float = 0.002,
                 batch_size: int = 8, *, scorer=None):
        if batch_size < 1 or not 0 <= threshold <= 1:
            raise ValueError("Expected positive batch_size and threshold in [0, 1]")
        self.threshold = threshold
        self.batch_size = batch_size
        self._lock = asyncio.Lock()
        self.max_length = int(model_config.get("max_length", 512))
        if self.max_length < 1:
            raise ValueError("max_length must be positive")
        if scorer is None:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            source, cache = model_source(model_config)
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    source, cache_dir=cache, local_files_only=True, trust_remote_code=False,
                )
                self.model = AutoModelForSequenceClassification.from_pretrained(
                    source, cache_dir=cache, local_files_only=True, trust_remote_code=False,
                ).to("cpu").eval()
            except (OSError, ValueError) as exc:
                raise QueryError(
                    "model_unavailable", "Локальная модель reranker недоступна. Подготовьте её файлы отдельно.",
                ) from exc
            self._torch = torch
            scorer = self._score_pairs
        self._scorer = scorer

    async def rerank(self, question: str, documents: list[RetrievedTable],
                     context: QueryContext | None = None) -> list[RetrievedTable]:
        if not documents:
            return []
        stop = Event()
        async with self._lock:
            return await finish_in_thread(self._rerank, question, documents, context, stop,
                                          on_cancel=stop.set)

    def _score_pairs(self, pairs):
        with self._torch.inference_mode():
            inputs = self.tokenizer(
                pairs, padding=True, truncation=True, max_length=self.max_length,
                return_tensors="pt",
            )
            logits = self.model(**inputs).logits.reshape(-1)
            return self._torch.sigmoid(logits).cpu().tolist()

    def _rerank(self, question, documents, context, stop):
        ranked = []
        for offset in range(0, len(documents), self.batch_size):
            if stop.is_set():
                raise QueryError("cancelled", "Оценка таблиц отменена.")
            if context is not None:
                context.check_cancelled()
            batch = documents[offset:offset + self.batch_size]
            pairs = [(question, serialize_table(document.table)) for document in batch]
            scores = self._scorer(pairs)
            if len(scores) != len(batch):
                raise ValueError("Reranker returned an invalid batch size")
            ranked.extend(RetrievedTable(document.table, float(score))
                          for document, score in zip(batch, scores)
                          if float(score) >= self.threshold)
        if context is not None:
            context.check_cancelled()
        return sorted(ranked, key=lambda document: (-document.score, document.table.ref))
