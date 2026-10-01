import asyncio
from collections.abc import Sequence
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from src.domain.query import QueryContext, QueryError
from src.domain.schema import RetrievedTable, TableSchema
from src.rag._runtime import finish_in_thread
from src.rag.indexing.serializer import (
    decode_metadata, encode_metadata, serialize_table, table_id,
)


class SchemaCataloger:
    def __init__(self, schema_reader, embedder, db_path: str,
                 collection_name: str = "tables", *, client=None, batch_size: int = 8):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.schema_reader = schema_reader
        self.embedder = embedder
        self.batch_size = batch_size
        self._lock = asyncio.Lock()
        path = Path(db_path)
        path.mkdir(parents=True, exist_ok=True)
        suffix = sha256(collection_name.encode("utf-8")).hexdigest()[:16]
        self._state_path = path / f"index-state-{suffix}.json"
        self._dirty: set[str] = set()
        self._reset_pending = False
        if self._state_path.exists():
            try:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
                if (state["version"] != 1 or not isinstance(state["dirty_schemas"], list)
                        or not all(isinstance(name, str) for name in state["dirty_schemas"])
                        or not isinstance(state["reset_pending"], bool)):
                    raise ValueError("Invalid index state")
                self._dirty = set(state["dirty_schemas"])
                self._reset_pending = state["reset_pending"]
            except (KeyError, TypeError, ValueError) as exc:
                raise QueryError("index_state_invalid", "Повреждён файл состояния индекса.") from exc
        if client is None:
            import chromadb
            client = chromadb.PersistentClient(path=str(path))
        self.client = client

        self._collection = client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}, embedding_function=None,
        )

    @property
    def dirty_schemas(self) -> frozenset[str]:
        return frozenset(self._dirty)

    def _save_state(self, dirty: set[str], reset_pending: bool) -> None:

        temporary = self._state_path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump({"version": 1, "dirty_schemas": sorted(dirty),
                           "reset_pending": reset_pending}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
        finally:
            temporary.unlink(missing_ok=True)
        self._dirty = set(dirty)
        self._reset_pending = reset_pending

    def _ensure_clean(self, schemas: Sequence[str]) -> None:
        if self._reset_pending or self._dirty.intersection(schemas):
            raise QueryError(
                "index_dirty", "Индекс выбранной схемы не обновлён. Выполните переиндексацию.",
            )

    async def mark_dirty(self, schema_id: str) -> None:

        async with self._lock:
            await finish_in_thread(self._save_state, self._dirty | {schema_id}, self._reset_pending)

    async def index_schema(self, schema_id: str,
                           context: QueryContext | None = None) -> int:
        async with self._lock:
            if self._reset_pending:
                raise QueryError("index_dirty", "Сначала завершите очистку индекса.")
            await finish_in_thread(self._save_state, self._dirty | {schema_id}, False)
            if context is not None:
                context.check_cancelled()
            tables = await self.schema_reader.get_tables([schema_id])
            if any(table.ref.schema != schema_id for table in tables):
                raise ValueError("Schema reader returned tables from another schema")
            tables = sorted(tables, key=lambda table: table.ref)

            await finish_in_thread(self._delete_schema, schema_id)
            for offset in range(0, len(tables), self.batch_size):
                if context is not None:
                    context.check_cancelled()
                batch = tables[offset:offset + self.batch_size]
                documents = [serialize_table(table) for table in batch]
                embeddings = await self.embedder.embed_documents(documents, context)
                await finish_in_thread(self._upsert, batch, documents, embeddings)
            if context is not None:
                context.check_cancelled()
            await finish_in_thread(self._save_state, self._dirty - {schema_id}, False)
            return len(tables)

    def _delete_schema(self, schema_id):
        self._collection.delete(where={"schema_id": schema_id})

    def _upsert(self, tables, documents, embeddings):
        self._collection.upsert(
            ids=[table_id(table) for table in tables], documents=documents,
            metadatas=[encode_metadata(table) for table in tables], embeddings=embeddings,
        )

    async def index_all_schemas(self, context: QueryContext | None = None) -> int:
        count = 0
        for schema_id in await self.schema_reader.list_schemas():
            count += await self.index_schema(schema_id, context)
        return count

    async def reset_store(self, *, schemas_to_reindex: Sequence[str] = ()) -> None:
        async with self._lock:
            pending = set(schemas_to_reindex)
            await finish_in_thread(self._save_state, self._dirty | pending, True)
            await finish_in_thread(self._clear)
            await finish_in_thread(self._save_state, pending, False)

    def _clear(self):

        while True:
            ids = self._collection.get(limit=256, include=[])["ids"]
            if not ids:
                break
            self._collection.delete(ids=ids)

    @staticmethod
    def _where(schemas):
        return ({"schema_id": schemas[0]} if len(schemas) == 1
                else {"schema_id": {"$in": list(schemas)}})

    async def query(self, vector: list[float], schemas: Sequence[str], top_k: int,
                    context: QueryContext | None = None) -> list[RetrievedTable]:
        if not schemas:
            return []
        if top_k < 1:
            raise ValueError("top_k must be positive")
        async with self._lock:
            self._ensure_clean(schemas)
            if context is not None:
                context.check_cancelled()
            result = await finish_in_thread(self._query, vector, tuple(schemas), top_k)
            if context is not None:
                context.check_cancelled()
            return result

    def _query(self, vector, schemas, top_k):
        count = self._collection.count()
        if count == 0:
            return []
        result = self._collection.query(
            query_embeddings=[vector], n_results=min(top_k, count),
            where=self._where(schemas), include=["metadatas", "distances"],
        )
        metadata = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]
        tables = []
        for item, distance in zip(metadata[0], distances[0], strict=True):
            table = decode_metadata(item)
            if table.ref.schema not in schemas:
                raise QueryError("index_invalid", "Индекс вернул таблицу вне выбранных схем.")
            tables.append(RetrievedTable(table, 1.0 - float(distance)))
        return tables

    async def read_tables(self, schemas: Sequence[str]) -> list[TableSchema]:
        if not schemas:
            return []
        async with self._lock:
            self._ensure_clean(schemas)
            return await finish_in_thread(self._read_tables, tuple(schemas))

    def _read_tables(self, schemas):
        tables = []
        offset = 0
        while True:
            result = self._collection.get(
                where=self._where(schemas), limit=256, offset=offset, include=["metadatas"],
            )
            tables.extend(decode_metadata(item) for item in result["metadatas"])
            if len(result["ids"]) < 256:
                return tables
            offset += 256
