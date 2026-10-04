from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from sql_agent.adapters.embedding import TableEmbedder
from sql_agent.adapters.reranker import TableReranker
from sql_agent.contracts import (
    CatalogSnapshot,
    Column,
    ForeignKey,
    QueryContext,
    QueryError,
    RetrievedTable,
    TableRef,
    TableSchema,
)
from sql_agent.retrieval.context import select_context
from sql_agent.retrieval.semantic import SemanticRetriever


class Embeddings:
    fingerprint = "model-revision-1"
    dimension = 2

    def __init__(self):
        self.batches = []

    async def embed_documents(self, texts, context):
        self.batches.append(texts)
        return [[1, 0] if "amount" in text else [0, 1] for text in texts]

    async def embed_query(self, question, context):
        return [1, 0]


class Model:
    max_seq_length = 512

    def __init__(self):
        self.calls = []

    def __call__(self, batch):
        self.calls.append(batch)
        return [[1.0, 0.0] for _ in batch]


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orders = TableSchema(TableRef("sales", "orders"), (Column("amount", "numeric"),))
        self.stores = TableSchema(TableRef("sales", "stores"), (Column("city", "text"),))
        self.snapshot = CatalogSnapshot(
            "tenant-1/source-1/profile-1", "v1", (self.orders, self.stores)
        )

    async def test_e5_prefixes_differ_for_question_and_document(self):
        model = Model()
        embedder = TableEmbedder({"adapter": "e5", "dimension": 2}, encoder=model)
        await embedder.embed_documents(["orders"])
        await embedder.embed_query("выручка")
        self.assertEqual(model.calls, [["passage: orders"], ["query: выручка"]])
        plain = TableEmbedder({"adapter": "plain"}, encoder=model)
        self.assertNotEqual(embedder.fingerprint, plain.fingerprint)

    async def test_explicit_prefixes_and_dimension_are_enforced(self):
        model = Model()
        embedder = TableEmbedder(
            {"query_prefix": "Q: ", "passage_prefix": "D: ", "dimension": 3}, encoder=model
        )
        with self.assertRaises(QueryError):
            await embedder.embed_query("test")
        self.assertEqual(model.calls, [["Q: test"]])

    async def test_namespace_schema_version_and_model_change_invalidate_cache(self):
        model = Embeddings()
        with tempfile.TemporaryDirectory() as directory:
            retriever = SemanticRetriever(model, cache_path=directory, top_k=1)
            result = await retriever.retrieve("revenue", self.snapshot, QueryContext())
            self.assertEqual(result[0].table, self.orders)
            await retriever.retrieve("revenue", self.snapshot, QueryContext())
            self.assertEqual(len(model.batches), 1)
            for changed in (
                replace(self.snapshot, namespace="tenant-2/source-1/profile-1"),
                replace(self.snapshot, version="v2"),
                replace(self.snapshot, tables=(self.stores,)),
            ):
                await retriever.retrieve("revenue", changed, QueryContext())
            model.fingerprint = "different-model"
            await retriever.retrieve("revenue", self.snapshot, QueryContext())
            self.assertEqual(len(model.batches), 5)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 5)

    async def test_persistent_vectors_are_reused_and_corruption_rebuilt(self):
        model = Embeddings()
        with tempfile.TemporaryDirectory() as directory:
            await SemanticRetriever(model, cache_path=directory).retrieve(
                "x", self.snapshot, QueryContext()
            )
            await SemanticRetriever(model, cache_path=directory).retrieve(
                "x", self.snapshot, QueryContext()
            )
            self.assertEqual(len(model.batches), 1)
            next(Path(directory).glob("*.json")).write_text('{"vectors":[]}', encoding="utf-8")
            await SemanticRetriever(model, cache_path=directory).retrieve(
                "x", self.snapshot, QueryContext()
            )
            self.assertEqual(len(model.batches), 2)

    async def test_valid_json_with_invalid_shape_or_dimension_rebuilds_cache(self):
        model = Embeddings()
        with tempfile.TemporaryDirectory() as directory:
            retriever = SemanticRetriever(model, cache_path=directory)
            await retriever.retrieve("x", self.snapshot, QueryContext())
            path = next(Path(directory).glob("*.json"))
            wrong_dimension = {
                "key": retriever._key(self.snapshot),
                "format": 1,
                "vectors": [[1, 0, 0], [0, 1, 0]],
            }
            for index, payload in enumerate(([], None, wrong_dimension), start=2):
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    result = await SemanticRetriever(model, cache_path=directory).retrieve(
                        "x", self.snapshot, QueryContext()
                    )
                    self.assertEqual(result[0].table, self.orders)
                    self.assertEqual(len(model.batches), index)

    async def test_wide_tables_index_late_columns(self):
        columns = tuple(Column(f"column_{index}", "text") for index in range(30)) + (
            Column("amount", "numeric"),
        )
        wide = replace(self.orders, columns=columns)
        model = Embeddings()
        retriever = SemanticRetriever(model, top_k=1)
        result = await retriever.retrieve(
            "revenue", replace(self.snapshot, tables=(wide, self.stores)), QueryContext()
        )
        self.assertEqual(result[0].table, wide)
        self.assertEqual(len(model.batches[0]), 4)

    async def test_reranker_sort_does_not_depend_on_old_model_threshold(self):
        scorer = lambda pairs: [0.001, 0.002]
        reranker = TableReranker({}, scorer=scorer)
        result = await reranker.rerank(
            "revenue", [RetrievedTable(self.orders), RetrievedTable(self.stores)]
        )
        self.assertEqual([item.table for item in result], [self.stores, self.orders])

    async def test_raw_negative_logits_are_ranked_without_implicit_filter(self):
        reranker = TableReranker({"activation": "identity"}, scorer=lambda pairs: [-3.0, -1.0])
        result = await reranker.rerank(
            "revenue", [RetrievedTable(self.orders), RetrievedTable(self.stores)]
        )
        self.assertEqual([item.table for item in result], [self.stores, self.orders])

    def test_budget_preserves_bridge_tables_as_a_group(self):
        a = TableSchema(TableRef("s", "a"), (Column("id", "integer"),))
        bridge = TableSchema(
            TableRef("s", "bridge"),
            (Column("id", "integer"),),
            foreign_keys=(ForeignKey(("id",), a.ref, ("id",)),),
        )
        end = TableSchema(
            TableRef("s", "end"),
            (Column("id", "integer"),),
            foreign_keys=(ForeignKey(("id",), bridge.ref, ("id",)),),
        )
        snapshot = CatalogSnapshot("n", "v", (a, bridge, end))
        ranked = [RetrievedTable(a, 1), RetrievedTable(end, 0.9)]
        limited = select_context(
            snapshot,
            ranked,
            max_tables=3,
            fits=lambda tables: len(tables) <= 2,
            context=QueryContext(),
        )
        self.assertEqual(limited, [a])
        full = select_context(
            snapshot, ranked, max_tables=3, fits=lambda tables: True, context=QueryContext()
        )
        self.assertEqual({table.ref for table in full}, {a.ref, bridge.ref, end.ref})

    def test_required_conversation_tables_cannot_be_silently_dropped(self):
        with self.assertRaises(QueryError) as caught:
            select_context(
                self.snapshot,
                [],
                max_tables=1,
                fits=lambda tables: False,
                context=QueryContext(),
                required=(self.orders.ref,),
            )
        self.assertEqual(caught.exception.code, "context_limit")
