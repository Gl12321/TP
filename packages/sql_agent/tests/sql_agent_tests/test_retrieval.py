from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sql_agent import CatalogSnapshot, Column, ForeignKey, QueryContext, QueryError, RetrievedTable
from sql_agent.retrieval.context import select_context
from sql_agent.retrieval.semantic import SemanticRetriever
from .test_sql_grammar import TABLES


class Embedder:
    fingerprint = "model-1"
    dimension = 2

    def __init__(self):
        self.calls = []

    async def embed_documents(self, texts, context):
        self.calls.append(texts)
        return [[1.0, 0.0] for text in texts]

    async def embed_query(self, text, context):
        return [1.0, 0.0]


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_is_partitioned_by_reader_catalog_and_model_and_recovers_corruption(self):
        with TemporaryDirectory() as directory:
            embedder = Embedder()
            snapshot = CatalogSnapshot("workspace-1/source/reader", "1", TABLES)
            retriever = SemanticRetriever(embedder, cache_path=directory)
            context = QueryContext()
            result = await retriever.retrieve("amount", snapshot, context)
            self.assertEqual({item.table for item in result}, set(TABLES))
            await SemanticRetriever(embedder, cache_path=directory).retrieve(
                "amount", snapshot, context
            )
            self.assertEqual(len(embedder.calls), 1)
            for changed in (
                replace(snapshot, namespace="workspace-2/source/reader"),
                replace(snapshot, version="2"),
                replace(snapshot, tables=(replace(TABLES[0], columns=(Column("id", "text"),)),)),
            ):
                await retriever.retrieve("amount", changed, context)
            self.assertEqual(len(embedder.calls), 4)
            embedder.fingerprint = "model-2"
            await retriever.retrieve("amount", snapshot, context)
            self.assertEqual(len(embedder.calls), 5)
            for path in Path(directory).glob("*.json"):
                path.write_text('{"vectors": [NaN]}', encoding="utf-8")
            await SemanticRetriever(embedder, cache_path=directory).retrieve(
                "amount", snapshot, context
            )
            self.assertEqual(len(embedder.calls), 6)

    async def test_wide_tables_index_all_columns_and_bound_catalog_size(self):
        embedder = Embedder()
        wide = replace(TABLES[0], columns=tuple(Column(f"column_{i}", "text") for i in range(25)))
        snapshot = CatalogSnapshot("source", "1", (wide,))
        retriever = SemanticRetriever(embedder, columns_per_chunk=12)
        result = await retriever.retrieve("column_24", snapshot, QueryContext())
        self.assertEqual([item.table for item in result], [wide])
        self.assertEqual(len(embedder.calls[0]), 3)
        self.assertIn("column_24", embedder.calls[0][-1])
        with self.assertRaises(QueryError) as caught:
            await SemanticRetriever(embedder, max_index_chunks=1).retrieve(
                "x", snapshot, QueryContext()
            )
        self.assertEqual(caught.exception.code, "catalog_limit")

    def test_context_adds_bridges_preserves_required_tables_and_fails_closed(self):
        left, right = TABLES
        bridge = replace(
            right,
            ref=replace(right.ref, name="links"),
            foreign_keys=(
                ForeignKey(("id",), left.ref, ("id",)),
                ForeignKey(("id",), right.ref, ("id",)),
            ),
        )
        snapshot = CatalogSnapshot("source", "1", (left, right, bridge))
        ranked = [RetrievedTable(left), RetrievedTable(right)]
        options = {"max_tables": 3, "fits": lambda values: True, "context": QueryContext()}
        selected = select_context(snapshot, ranked, **options)
        self.assertEqual({table.ref for table in selected}, {left.ref, bridge.ref, right.ref})
        required = (left.ref, right.ref)
        for limited in ({"max_tables": 2}, {"fits": lambda values: len(values) <= 2}):
            with self.subTest(limited=limited), self.assertRaises(QueryError) as caught:
                select_context(snapshot, ranked, required=required, **(options | limited))
            self.assertEqual(caught.exception.code, "context_limit")
        with self.assertRaises(QueryError) as caught:
            select_context(
                snapshot,
                [RetrievedTable(replace(left, columns=(Column("secret", "text"),)))],
                **options,
            )
        self.assertEqual(caught.exception.code, "index_outdated")
