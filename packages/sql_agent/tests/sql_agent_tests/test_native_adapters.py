import importlib.util
from types import SimpleNamespace
import unittest

from sql_agent.adapters.embedding import TableEmbedder


@unittest.skipUnless(
    importlib.util.find_spec("torch"), "Install the local model extra to test CPU tensors"
)
class NativeEmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_padding_is_excluded_from_mean_and_normalized_embedding(self):
        import torch

        calls = []
        hidden = torch.tensor(
            [[[2.0, 0.0], [0.0, 2.0], [999.0, 999.0]], [[3.0, 0.0], [900.0, 800.0], [800.0, 900.0]]]
        )
        mask = torch.tensor([[1, 1, 0], [1, 0, 0]])

        def tokenize(texts, **options):
            calls.append((texts, options))
            return {"attention_mask": mask}

        def model(**inputs):
            self.assertTrue(torch.is_inference_mode_enabled())
            return SimpleNamespace(last_hidden_state=hidden)

        embedder = TableEmbedder(
            {"adapter": "e5", "dimension": 2, "max_length": 32}, encoder=lambda texts: []
        )
        embedder._torch = torch
        embedder.tokenizer = tokenize
        embedder.model = model
        embedder._encoder = embedder._encode_batch
        vectors = await embedder.embed_documents(["first", "second"])
        torch.testing.assert_close(
            torch.tensor(vectors), torch.tensor([[2**-0.5, 2**-0.5], [1.0, 0.0]])
        )
        self.assertEqual(
            calls[0],
            (
                ["passage: first", "passage: second"],
                {"padding": True, "truncation": True, "max_length": 32, "return_tensors": "pt"},
            ),
        )
        embedder.normalize = False
        self.assertEqual(
            await embedder.embed_documents(["first", "second"]), [[1.0, 1.0], [3.0, 0.0]]
        )
