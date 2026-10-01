import asyncio
from threading import Event

from src.domain.query import QueryContext, QueryError
from src.rag._runtime import finish_in_thread, model_source


class TableEmbedder:
    def __init__(self, model_config: dict, batch_size: int = 8, threads: int = 2,
                 *, model=None):
        if batch_size < 1 or threads < 1:
            raise ValueError("batch_size and threads must be positive")
        self.batch_size = batch_size
        self._lock = asyncio.Lock()
        if model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            source, cache = model_source(model_config)
            torch.set_num_threads(threads)
            try:
                model = SentenceTransformer(
                    source, device="cpu", cache_folder=cache,
                    local_files_only=True, trust_remote_code=False,
                )
            except (OSError, ValueError) as exc:
                raise QueryError(
                    "model_unavailable",
                    "Локальная модель эмбеддингов недоступна. Подготовьте её файлы отдельно.",
                ) from exc
        self.model = model
        max_length = int(model_config.get("max_length", 1024))
        if max_length < 1:
            raise ValueError("max_length must be positive")
        if hasattr(model, "max_seq_length"):
            model.max_seq_length = min(model.max_seq_length, max_length)

    async def embed_documents(self, texts: list[str],
                              context: QueryContext | None = None) -> list[list[float]]:
        if not texts:
            return []
        stop = Event()
        async with self._lock:
            return await finish_in_thread(self._encode, texts, context, stop,
                                          on_cancel=stop.set)

    async def embed_query(self, text: str,
                          context: QueryContext | None = None) -> list[float]:
        return (await self.embed_documents([text], context))[0]

    def _encode(self, texts, context, stop):
        result = []
        for offset in range(0, len(texts), self.batch_size):
            if stop.is_set():
                raise QueryError("cancelled", "Вычисление эмбеддингов отменено.")
            if context is not None:
                context.check_cancelled()
            batch = texts[offset:offset + self.batch_size]
            vectors = self.model.encode(
                batch, batch_size=self.batch_size, normalize_embeddings=True,
                show_progress_bar=False, convert_to_numpy=True,
            )
            if len(vectors) != len(batch):
                raise ValueError("Embedding model returned an invalid batch size")
            result.extend(vectors.tolist() if hasattr(vectors, "tolist") else vectors)
        if context is not None:
            context.check_cancelled()
        return result
