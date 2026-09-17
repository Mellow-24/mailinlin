"""Unit tests for the DashScope OpenAI-compatible embedding client."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.services.configuration.options.dashscope import (
    DASHSCOPE_COMPATIBLE_BASE_URL,
    DASHSCOPE_EMBEDDING_DIMENSION,
)
from api.services.gen_ai.embedding import openai_service
from api.services.gen_ai.embedding.dashscope_service import (
    DashScopeEmbeddingService,
)


def _fake_openai_client(*, returned_dimension: int):
    create = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[0.0] * returned_dimension),
            ]
        )
    )
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    return client, create


@pytest.mark.asyncio
async def test_dashscope_uses_base_url_model_and_explicit_dimensions(monkeypatch):
    client, create = _fake_openai_client(
        returned_dimension=DASHSCOPE_EMBEDDING_DIMENSION
    )
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(openai_service, "AsyncOpenAI", client_factory)

    base_url = "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    service = DashScopeEmbeddingService(
        db_client=SimpleNamespace(),
        api_key="sk-test",
        model_id="text-embedding-v4",
        base_url=base_url,
    )

    embeddings = await service.embed_texts(["feng shui"])

    client_factory.assert_called_once_with(api_key="sk-test", base_url=base_url)
    create.assert_awaited_once_with(
        input=["feng shui"],
        model="text-embedding-v4",
        dimensions=1536,
    )
    assert len(embeddings[0]) == 1536
    assert service.get_embedding_dimension() == 1536
    assert service.max_batch_size == 20


def test_dashscope_uses_beijing_shared_endpoint_by_default(monkeypatch):
    client, _ = _fake_openai_client(returned_dimension=DASHSCOPE_EMBEDDING_DIMENSION)
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(openai_service, "AsyncOpenAI", client_factory)

    DashScopeEmbeddingService(
        db_client=SimpleNamespace(),
        api_key="sk-test",
    )

    client_factory.assert_called_once_with(
        api_key="sk-test",
        base_url=DASHSCOPE_COMPATIBLE_BASE_URL,
    )


@pytest.mark.asyncio
async def test_dashscope_rejects_unexpected_embedding_dimension(monkeypatch):
    client, _ = _fake_openai_client(returned_dimension=1024)
    monkeypatch.setattr(openai_service, "AsyncOpenAI", Mock(return_value=client))
    service = DashScopeEmbeddingService(
        db_client=SimpleNamespace(),
        api_key="sk-test",
    )

    with pytest.raises(
        ValueError,
        match=("qwen3.7-text-embedding.*returned 1024 dimensions.*expected 1536"),
    ):
        await service.embed_texts(["feng shui"])
