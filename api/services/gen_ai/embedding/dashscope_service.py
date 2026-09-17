"""Alibaba Cloud Model Studio (DashScope) embedding service."""

from typing import Any

from api.db.db_client import DBClient
from api.services.configuration.options.dashscope import (
    DASHSCOPE_COMPATIBLE_BASE_URL,
    DASHSCOPE_DEFAULT_EMBEDDING_MODEL,
    DASHSCOPE_EMBEDDING_DIMENSION,
)

from .openai_service import OpenAIEmbeddingService


class DashScopeEmbeddingService(OpenAIEmbeddingService):
    """Generate fixed-size embeddings through DashScope's OpenAI API."""

    # DashScope rejects embedding requests containing more than 20 inputs.
    # The document-ingestion loop honors this provider capability while other
    # embedding providers retain their existing batch size.
    max_batch_size = 20

    def __init__(
        self,
        db_client: DBClient,
        api_key: str | None = None,
        model_id: str = DASHSCOPE_DEFAULT_EMBEDDING_MODEL,
        base_url: str | None = DASHSCOPE_COMPATIBLE_BASE_URL,
        embedding_dimension: int = DASHSCOPE_EMBEDDING_DIMENSION,
    ):
        """Initialize a DashScope OpenAI-compatible embedding client.

        ``embedding_dimension`` defaults to 1536 because that is the dimension
        of Dograh's current knowledge-base vector column. DashScope embedding
        models otherwise default to a different size, so every request must
        send the ``dimensions`` parameter explicitly.
        """
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be greater than zero")

        self.embedding_dimension = embedding_dimension
        super().__init__(
            db_client=db_client,
            api_key=api_key,
            model_id=model_id,
            base_url=base_url or DASHSCOPE_COMPATIBLE_BASE_URL,
        )

    def get_embedding_dimension(self) -> int:
        """Return the vector size requested from DashScope."""
        return self.embedding_dimension

    def _request_kwargs(self) -> dict[str, Any]:
        """Request Dograh's configured vector size explicitly."""
        return {"dimensions": self.embedding_dimension}

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts and reject vectors that cannot fit the active index."""
        embeddings = await super().embed_texts(texts)
        self._validate_embedding_dimensions(embeddings)
        return embeddings

    def _validate_embedding_dimensions(self, embeddings: list[list[float]]) -> None:
        for index, embedding in enumerate(embeddings):
            actual_dimension = len(embedding)
            if actual_dimension != self.embedding_dimension:
                raise ValueError(
                    "DashScope embedding model "
                    f"{self.model_id!r} returned {actual_dimension} dimensions "
                    f"for item {index}; expected {self.embedding_dimension}."
                )
