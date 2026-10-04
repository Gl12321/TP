import asyncio
from collections import OrderedDict
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from uuid import uuid4

from sql_agent.adapters.concurrency import finish_in_thread
from sql_agent.contracts import CatalogSnapshot, QueryContext, QueryError, RetrievedTable

from .serializer import serialize_chunks


class SemanticRetriever:
    def __init__(
        self,
        embedder,
        *,
        top_k: int = 10,
        cache_path: str | Path | None = None,
        max_cached_catalogs: int = 4,
        columns_per_chunk: int = 12,
        max_index_chunks: int = 4096,
    ):
        if min(top_k, max_cached_catalogs, columns_per_chunk, max_index_chunks) < 1:
            raise ValueError("Retrieval limits must be positive")
        if not getattr(embedder, "fingerprint", None):
            raise ValueError("Embedder must provide a stable model fingerprint")
        self.embedder = embedder
        self.top_k = top_k
        self.cache_path = Path(cache_path) if cache_path is not None else None
        self.max_cached_catalogs = max_cached_catalogs
        self.columns_per_chunk = columns_per_chunk
        self.max_index_chunks = max_index_chunks
        self._cache = OrderedDict()
        self._lock = asyncio.Lock()

    def _key(self, snapshot):
        payload = {
            "namespace": snapshot.namespace,
            "version": snapshot.version,
            "tables": [
                asdict(table) for table in sorted(snapshot.tables, key=lambda item: item.ref)
            ],
            "embedder": self.embedder.fingerprint,
            "columns_per_chunk": self.columns_per_chunk,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def _vectors(values, count, dimension=None):
        if not isinstance(values, list) or len(values) != count:
            raise ValueError("Invalid embedding count")
        result = []
        for vector in values:
            if not isinstance(vector, (list, tuple)) or not vector:
                raise ValueError("Invalid embedding vector")
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in vector
            ):
                raise ValueError("Invalid embedding dimensions or values")
            norm = math.sqrt(sum(value * value for value in vector))
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("Invalid embedding norm")
            result.append(tuple(value / norm for value in vector))
        return tuple(result)

    def _read(self, key, count):
        if self.cache_path is None:
            return None
        try:
            path = self.cache_path / f"{key}.json"
            if path.stat().st_size > 64 * 1024 * 1024:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("key") != key or data.get("format") != 1:
                return None
            return self._vectors(data["vectors"], count, getattr(self.embedder, "dimension", None))
        except (OSError, KeyError, ValueError, TypeError):
            return None

    def _write(self, key, vectors):
        if self.cache_path is None:
            return
        self.cache_path.mkdir(parents=True, exist_ok=True)
        target = self.cache_path / f"{key}.json"
        temporary = target.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(
                    {"format": 1, "key": key, "vectors": vectors},
                    handle,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    async def retrieve(
        self, question: str, snapshot: CatalogSnapshot, context: QueryContext
    ) -> list[RetrievedTable]:
        context.check_cancelled()
        if not snapshot.tables:
            return []
        tables = sorted(snapshot.tables, key=lambda item: item.ref)
        chunks = [
            (index, text)
            for index, table in enumerate(tables)
            for text in serialize_chunks(table, columns_per_chunk=self.columns_per_chunk)
        ]
        if len(chunks) > self.max_index_chunks:
            raise QueryError(
                "catalog_limit",
                "Каталог слишком велик для локального поиска. Ограничьте профиль доступных таблиц.",
            )
        key = self._key(snapshot)
        async with self._lock:
            vectors = self._cache.get(key)
            if vectors is None:
                vectors = await finish_in_thread(self._read, key, len(chunks))
            if vectors is None:
                context.emit("index", "Подготовка поиска по доступным таблицам", tables=len(tables))
                encoded = await self.embedder.embed_documents([text for _, text in chunks], context)
                try:
                    vectors = self._vectors(encoded, len(chunks))
                except ValueError as exc:
                    raise QueryError(
                        "model_output", "Модель поиска вернула некорректные векторы."
                    ) from exc
                context.check_cancelled()
                await finish_in_thread(self._write, key, vectors)
            self._cache[key] = vectors
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cached_catalogs:
                self._cache.popitem(last=False)
            try:
                query = self._vectors(
                    [await self.embedder.embed_query(question, context)], 1, len(vectors[0])
                )[0]
            except ValueError as exc:
                raise QueryError(
                    "model_output", "Вектор вопроса не соответствует индексу."
                ) from exc
            scores = {}
            for (index, _), vector in zip(chunks, vectors, strict=True):
                context.check_cancelled()
                score = sum(left * right for left, right in zip(query, vector, strict=True))
                scores[index] = max(scores.get(index, -math.inf), score)
            return sorted(
                (RetrievedTable(tables[index], score) for index, score in scores.items()),
                key=lambda item: (-item.score, item.table.ref),
            )[: self.top_k]
