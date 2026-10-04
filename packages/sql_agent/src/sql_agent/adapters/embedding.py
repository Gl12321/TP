import asyncio
from hashlib import sha256
import json
from threading import Event

from sql_agent.query import QueryContext, QueryError
from sql_agent.adapters.runtime import finish_in_thread, model_source


class TableEmbedder:
    def __init__(self, model_config: dict, batch_size: int = 8, threads: int = 2, *, encoder=None):
        if batch_size < 1 or threads < 1:
            raise ValueError("batch_size and threads must be positive")
        self.batch_size = batch_size
        self._lock = asyncio.Lock()
        adapter = model_config.get("adapter", "plain")
        if adapter not in {"plain", "e5"}:
            raise ValueError("Embedding adapter must be plain or e5")
        self.query_prefix = model_config.get("query_prefix", "query: " if adapter == "e5" else "")
        self.document_prefix = model_config.get(
            "passage_prefix", "passage: " if adapter == "e5" else ""
        )
        if not isinstance(self.query_prefix, str) or not isinstance(self.document_prefix, str):
            raise ValueError("Embedding prefixes must be strings")
        self.dimension = model_config.get("dimension")
        if self.dimension is not None and (
            isinstance(self.dimension, bool)
            or not isinstance(self.dimension, int)
            or self.dimension < 1
        ):
            raise ValueError("Embedding dimension must be positive")
        self.normalize = model_config.get("normalize", True)
        if not isinstance(self.normalize, bool):
            raise ValueError("Embedding normalize must be a boolean")
        self.max_length = model_config.get("max_length", 512)
        if type(self.max_length) is not int or self.max_length < 1:
            raise ValueError("max_length must be a positive integer")
        self.fingerprint = sha256(
            json.dumps(
                {
                    "repo_id": model_config.get("repo_id"),
                    "revision": model_config.get("revision"),
                    "adapter": adapter,
                    "max_length": model_config.get("max_length", 512),
                    "query_prefix": self.query_prefix,
                    "passage_prefix": self.document_prefix,
                    "dimension": self.dimension,
                    "normalize": self.normalize,
                    "pooling": "masked_mean",
                    "serializer": 2,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if encoder is None:
            import torch
            from transformers import AutoModel, AutoTokenizer

            source, cache = model_source(model_config)
            torch.set_num_threads(threads)
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    source,
                    cache_dir=cache,
                    local_files_only=True,
                    trust_remote_code=False,
                    revision=model_config.get("revision"),
                )
                self.model = (
                    AutoModel.from_pretrained(
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
                    "Локальная модель эмбеддингов недоступна. Подготовьте её файлы отдельно.",
                ) from exc
            self._torch = torch
            encoder = self._encode_batch
        self._encoder = encoder

    def _encode_batch(self, texts):
        with self._torch.inference_mode():
            inputs = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            hidden = self.model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vectors = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
            if self.normalize:
                vectors = self._torch.nn.functional.normalize(vectors, p=2, dim=1)
            return vectors.cpu().tolist()

    async def embed_documents(
        self, texts: list[str], context: QueryContext | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        stop = Event()
        async with self._lock:
            return await finish_in_thread(
                self._encode,
                [self.document_prefix + text for text in texts],
                context,
                stop,
                on_cancel=stop.set,
            )

    async def embed_query(self, text: str, context: QueryContext | None = None) -> list[float]:
        stop = Event()
        async with self._lock:
            vectors = await finish_in_thread(
                self._encode, [self.query_prefix + text], context, stop, on_cancel=stop.set
            )
            return vectors[0]

    def _encode(self, texts, context, stop):
        result = []
        for offset in range(0, len(texts), self.batch_size):
            if stop.is_set():
                raise QueryError("cancelled", "Вычисление эмбеддингов отменено.")
            if context is not None:
                context.check_cancelled()
            batch = texts[offset : offset + self.batch_size]
            vectors = self._encoder(batch)
            if len(vectors) != len(batch):
                raise ValueError("Embedding model returned an invalid batch size")
            if self.dimension is not None and any(
                len(vector) != self.dimension for vector in vectors
            ):
                raise QueryError(
                    "model_output", "Размерность модели поиска не соответствует настройкам."
                )
            result.extend(vectors.tolist() if hasattr(vectors, "tolist") else vectors)
        if context is not None:
            context.check_cancelled()
        return result
