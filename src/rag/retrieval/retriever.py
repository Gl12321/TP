from collections.abc import Sequence

from src.domain.query import QueryContext
from src.domain.schema import RetrievedTable


class TableRetriever:
    def __init__(self, catalog, embedder, top_k: int = 10):
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self.catalog = catalog
        self.embedder = embedder
        self.top_k = top_k

    async def retrieve(self, question: str, schemas: Sequence[str],
                       context: QueryContext | None = None) -> list[RetrievedTable]:
        selected = tuple(dict.fromkeys(schemas))
        if not selected:
            return []
        if context is not None:
            context.check_cancelled()
        vector = await self.embedder.embed_query(question, context)
        return await self.catalog.query(vector, selected, self.top_k, context)
