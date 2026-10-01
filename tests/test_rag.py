import asyncio
import tempfile
from pathlib import Path
from threading import Event, Lock
import unittest

from src.domain.query import QueryContext, QueryError
from src.domain.schema import Column, ForeignKey, RetrievedTable, TableRef, TableSchema
from src.rag._runtime import model_source
from src.rag.embedder import TableEmbedder
from src.rag.indexing.catalog import SchemaCataloger
from src.rag.indexing.serializer import decode_metadata, encode_metadata, table_id
from src.rag.retrieval.context import ContextBuilder
from src.rag.retrieval.reranker import TableReranker
from src.rag.retrieval.retriever import TableRetriever


def table(name, schema="sales", foreign_keys=()):
    return TableSchema(TableRef(schema, name), (Column("id", "INTEGER", False),),
                       primary_key=("id",), foreign_keys=foreign_keys, ddl=f"TABLE {name}")


class FakeReader:
    def __init__(self, tables):
        self.tables = tables

    async def get_tables(self, schemas):
        return [item for item in self.tables if item.ref.schema in schemas]

    async def list_schemas(self):
        return sorted({item.ref.schema for item in self.tables})


class FakeEmbedder:
    def __init__(self):
        self.batches = []
        self.fail = False

    async def embed_documents(self, texts, context=None):
        if self.fail:
            raise RuntimeError("Embedding failed")
        self.batches.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text, context=None):
        await asyncio.sleep(0)
        return [1.0, 0.0]


class FakeCollection:
    def __init__(self):
        self.items = {}
        self.queries = []
        self.fail_delete = False

    def upsert(self, ids, documents, metadatas, embeddings):
        for key, metadata in zip(ids, metadatas):
            self.items[key] = metadata

    def _filtered(self, where):
        items = list(self.items.items())
        if where is None:
            return items
        value = where["schema_id"]
        schemas = value["$in"] if isinstance(value, dict) else [value]
        return [(key, metadata) for key, metadata in items if metadata["schema_id"] in schemas]

    def get(self, where=None, limit=256, offset=0, include=None):
        items = self._filtered(where)[offset:offset + limit]
        return {"ids": [key for key, _ in items], "metadatas": [value for _, value in items]}

    def delete(self, where=None, ids=None):
        if self.fail_delete:
            raise RuntimeError("Delete failed")
        selected = ids if ids is not None else [key for key, _ in self._filtered(where)]
        for key in selected:
            self.items.pop(key, None)

    def count(self):
        return len(self.items)

    def query(self, query_embeddings, n_results, where, include):
        self.queries.append(where)
        items = self._filtered(where)[:n_results]
        return {"ids": [[key for key, _ in items]],
                "metadatas": [[value for _, value in items]],
                "distances": [[0.1] * len(items)]}


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()

    def get_or_create_collection(self, **kwargs):
        assert kwargs["embedding_function"] is None
        return self.collection


class SerializationTests(unittest.TestCase):
    def test_metadata_round_trip_preserves_identifiers_and_foreign_keys(self):
        item = TableSchema(
            TableRef('strange.schema', 'line,"items'),
            (Column('x,y"z', "NUMERIC(12,2)", False, "Точная сумма"),),
            primary_key=('x,y"z',),
            foreign_keys=(ForeignKey(('x,y"z',), TableRef("other.schema", "orders"), ("id",)),),
            ddl='CREATE TABLE "line,""items" (...)', description="Продажи",
        )
        self.assertEqual(decode_metadata(encode_metadata(item)), item)
        self.assertNotEqual(table_id(table("b.c", "a")), table_id(table("c", "a.b")))

    def test_legacy_and_mismatched_metadata_fail_closed(self):
        for metadata in ({"column_names": "one,two"},
                         {**encode_metadata(table("one")), "schema_id": "forbidden"}):
            with self.assertRaises(QueryError) as caught:
                decode_metadata(metadata)
            self.assertEqual(caught.exception.code, "index_outdated")

    def test_model_source_recognizes_local_dir_without_hub_lookup(self):
        with tempfile.TemporaryDirectory() as path:
            config = {"repo_id": "BAAI/bge-m3", "cache_path": path}
            self.assertEqual(model_source(config), ("BAAI/bge-m3", path))
            Path(path, "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(model_source(config), (path, None))


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.reader = FakeReader([table("orders"), table("deleted"), table("private", "other")])
        self.embedder = FakeEmbedder()
        self.client = FakeClient()
        self.catalog = self.new_catalog()

    def new_catalog(self):
        return SchemaCataloger(self.reader, self.embedder, self.temporary.name,
                               client=self.client, batch_size=1)

    async def test_reindex_removes_disappeared_tables_and_batches_embeddings(self):
        self.assertEqual(await self.catalog.index_all_schemas(), 3)
        self.reader.tables = [table("orders"), table("private", "other")]
        await self.catalog.index_schema("sales")
        documents = await self.catalog.read_tables(["sales"])
        self.assertEqual([item.ref.name for item in documents], ["orders"])
        self.assertEqual([len(batch) for batch in self.embedder.batches], [1, 1, 1, 1])
        self.assertEqual(len(await self.catalog.read_tables(["other"])), 1)

    async def test_failed_index_is_dirty_after_restart_until_successful_reindex(self):
        await self.catalog.index_schema("sales")
        self.embedder.fail = True
        with self.assertRaises(RuntimeError):
            await self.catalog.index_schema("sales")
        restarted = self.new_catalog()
        self.assertEqual(restarted.dirty_schemas, frozenset({"sales"}))
        with self.assertRaises(QueryError) as caught:
            await restarted.query([1.0, 0.0], ["sales"], 10)
        self.assertEqual(caught.exception.code, "index_dirty")
        self.embedder.fail = False
        await restarted.index_schema("sales")
        self.assertFalse(self.new_catalog().dirty_schemas)

    async def test_invalidation_before_db_write_survives_restart(self):
        await self.catalog.mark_dirty("sales")
        with self.assertRaises(QueryError):
            await self.new_catalog().read_tables(["sales"])

    async def test_schema_filters_remain_local_under_concurrent_retrieval(self):
        await self.catalog.index_all_schemas()
        retriever = TableRetriever(self.catalog, self.embedder)
        sales, other = await asyncio.gather(
            retriever.retrieve("sales question", ["sales"]),
            retriever.retrieve("other question", ["other"]),
        )
        self.assertTrue(all(item.table.ref.schema == "sales" for item in sales))
        self.assertTrue(all(item.table.ref.schema == "other" for item in other))
        self.assertEqual(await retriever.retrieve("none", []), [])

    async def test_reset_keeps_collection_identity_and_old_retriever_usable(self):
        await self.catalog.index_schema("sales")
        retriever = TableRetriever(self.catalog, self.embedder)
        original = self.client.collection
        await self.catalog.reset_store()
        self.assertEqual(await retriever.retrieve("empty", ["sales"]), [])
        self.assertIs(self.catalog._collection, original)
        await self.catalog.index_schema("sales")
        self.assertEqual(len(await retriever.retrieve("restored", ["sales"])), 2)

    async def test_failed_reset_blocks_all_schemas_across_restart(self):
        await self.catalog.index_schema("sales")
        self.client.collection.fail_delete = True
        with self.assertRaises(RuntimeError):
            await self.catalog.reset_store()
        restarted = self.new_catalog()
        with self.assertRaises(QueryError):
            await restarted.query([1.0, 0.0], ["anything"], 1)
        self.client.collection.fail_delete = False
        await restarted.reset_store()
        self.assertEqual(await restarted.query([1.0, 0.0], ["sales"], 1), [])

    async def test_empty_schema_removes_old_documents(self):
        await self.catalog.index_schema("sales")
        self.reader.tables = []
        self.assertEqual(await self.catalog.index_schema("sales"), 0)
        self.assertEqual(await self.catalog.read_tables(["sales"]), [])

    async def test_rebuild_blocks_unfinished_schemas_across_restart(self):
        await self.catalog.index_all_schemas()
        await self.catalog.reset_store(schemas_to_reindex=["sales", "other"])
        restarted = self.new_catalog()
        self.assertEqual(restarted.dirty_schemas, frozenset({"sales", "other"}))
        await restarted.index_schema("sales")
        self.assertEqual(len(await restarted.read_tables(["sales"])), 2)
        with self.assertRaises(QueryError) as caught:
            await self.new_catalog().read_tables(["other"])
        self.assertEqual(caught.exception.code, "index_dirty")


class RankingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reranker_sorts_filters_and_uses_bounded_batches(self):
        batches = []
        scores = iter([0.1, 0.9, 0.9, 0.001])

        def scorer(pairs):
            batches.append(pairs)
            return [next(scores) for _ in pairs]

        reranker = TableReranker({}, batch_size=2, scorer=scorer)
        ranked = await reranker.rerank("question", [RetrievedTable(table(name))
                                                   for name in ("low", "z", "a", "excluded")])
        self.assertEqual([item.table.ref.name for item in ranked], ["a", "z", "low"])
        self.assertEqual([len(batch) for batch in batches], [2, 2])
        self.assertEqual(await reranker.rerank("question", []), [])

    async def test_embedder_bounds_batch_and_sequence_length(self):
        class Model:
            max_seq_length = 8192
            batches = []

            def encode(self, texts, **kwargs):
                self.batches.append(list(texts))
                return [[1.0, 0.0] for _ in texts]

        model = Model()
        embedder = TableEmbedder({}, batch_size=2, model=model)
        self.assertEqual(len(await embedder.embed_documents(["a", "b", "c"])), 3)
        self.assertEqual(model.batches, [["a", "b"], ["c"]])
        self.assertEqual(model.max_seq_length, 1024)
        self.assertEqual(await embedder.embed_documents([]), [])

    async def test_cancellation_does_not_release_model_while_worker_runs(self):
        started, release = Event(), Event()
        lock = Lock()

        class Model:
            calls = 0
            active = 0
            maximum = 0

            def encode(self, texts, **kwargs):
                with lock:
                    self.calls += 1
                    call = self.calls
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                try:
                    if call == 1:
                        started.set()
                        if not release.wait(3):
                            raise RuntimeError("Test worker was not released")
                    return [[1.0] for _ in texts]
                finally:
                    with lock:
                        self.active -= 1

        model = Model()
        embedder = TableEmbedder({}, model=model)
        first = asyncio.create_task(embedder.embed_query("first"))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            first.cancel()
            second = asyncio.create_task(embedder.embed_query("second"))
            await asyncio.sleep(0.01)
            self.assertEqual(model.calls, 1)
            self.assertFalse(second.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual(await second, [1.0])
        self.assertEqual(model.maximum, 1)

    async def test_cancelled_context_stops_before_scoring(self):
        context = QueryContext()
        context.cancelled.set()
        calls = []
        reranker = TableReranker({}, scorer=lambda pairs: calls.append(pairs))
        with self.assertRaises(QueryError):
            await reranker.rerank("question", [RetrievedTable(table("one"))], context)
        self.assertEqual(calls, [])


class ContextTests(unittest.IsolatedAsyncioTestCase):
    def linked_tables(self):
        left = table("customers")
        right = table("products")
        bridge = table("orders", foreign_keys=(
            ForeignKey(("customer_id",), left.ref, ("id",)),
            ForeignKey(("product_id",), right.ref, ("id",)),
        ))
        return left, right, bridge

    async def test_context_adds_bridge_in_both_fk_directions(self):
        left, right, bridge = self.linked_tables()
        builder = ContextBuilder(FakeReader([right, left, bridge]), max_tables=3)
        result = await builder.build([RetrievedTable(left, 0.9), RetrievedTable(right, 0.8)], ["sales"])
        self.assertEqual([item.ref for item in result], [left.ref, bridge.ref, right.ref])

    async def test_table_budget_fails_instead_of_dropping_required_bridge(self):
        left, right, bridge = self.linked_tables()
        builder = ContextBuilder(FakeReader([left, right, bridge]), max_tables=2)
        with self.assertRaises(QueryError) as caught:
            await builder.build([RetrievedTable(left), RetrievedTable(right)], ["sales"])
        self.assertEqual(caught.exception.code, "context_too_large")

    async def test_context_rejects_changed_metadata_and_forbidden_schema(self):
        current = table("orders")
        builder = ContextBuilder(FakeReader([current]))
        stale = TableSchema(current.ref, (Column("removed", "TEXT"),))
        for candidate, code in ((stale, "index_outdated"),
                                (table("secret", "private"), "schema_not_allowed")):
            with self.assertRaises(QueryError) as caught:
                await builder.build([RetrievedTable(candidate)], ["sales"])
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
