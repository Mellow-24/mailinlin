"""Regression coverage for the direct voice-demo FAQ fast path."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat import direct_faq_retrieval as retrieval


def test_enrich_query_never_guesses_zodiac_from_year_alone() -> None:
    assert retrieval.enrich_direct_faq_query("我係95年出世") == "我係95年出世"
    assert retrieval.enrich_direct_faq_query("2012年出生") == "2012年出生"


def test_enrich_query_uses_lunar_new_year_boundary_for_complete_date() -> None:
    assert "生肖馬" in retrieval.enrich_direct_faq_query(
        "我係2003年1月31日出世"
    )
    assert "生肖羊" in retrieval.enrich_direct_faq_query(
        "我係2003年2月1日出世"
    )


def test_enrich_query_leaves_non_year_answer_unchanged() -> None:
    assert retrieval.enrich_direct_faq_query("我想睇事業") == "我想睇事業"


def test_replace_reference_removes_previous_reference_before_adding_new_one() -> None:
    messages = [
        {"role": "system", "content": "global rules"},
        {
            "role": "system",
            "content": f"{retrieval.FAQ_REFERENCE_MARKER}\nold reference",
        },
        {"role": "user", "content": "旧问题"},
    ]

    updated = retrieval.replace_direct_faq_reference(messages, "new reference")

    assert updated == [
        {"role": "system", "content": "global rules"},
        {"role": "user", "content": "旧问题"},
        {
            "role": "system",
            "content": f"{retrieval.FAQ_REFERENCE_MARKER}\nnew reference",
        },
    ]


def test_format_reference_limits_two_chunks_and_each_chunk_length() -> None:
    reference = retrieval.format_direct_faq_reference(
        [
            {"text": "甲" * (retrieval.MAX_CHARS_PER_CHUNK + 50)},
            {"text": "乙" * (retrieval.MAX_CHARS_PER_CHUNK + 50)},
            {"text": "丙" * 10},
        ]
    )

    assert reference.startswith(retrieval.FAQ_REFERENCE_MARKER)
    assert reference.count("FAQ 参考") == retrieval.MAX_CHUNKS
    assert "丙" * 10 not in reference
    assert "甲" * retrieval.MAX_CHARS_PER_CHUNK in reference
    assert "乙" * retrieval.MAX_CHARS_PER_CHUNK in reference


@pytest.mark.asyncio
async def test_prepare_reference_skips_retrieval_without_documents(monkeypatch) -> None:
    retrieval_call = AsyncMock()
    monkeypatch.setattr(retrieval, "retrieve_from_knowledge_base", retrieval_call)

    result = await retrieval.prepare_direct_faq_reference(
        query="客厅财位点摆？",
        organization_id=1,
        document_uuids=[],
    )

    assert result == ""
    retrieval_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_reference_times_out_without_raising(monkeypatch) -> None:
    async def slow_retrieval(**_kwargs):
        await asyncio.sleep(0.05)
        return {"chunks": [{"text": "不应返回"}]}

    monkeypatch.setattr(retrieval, "retrieve_from_knowledge_base", slow_retrieval)

    result = await retrieval.prepare_direct_faq_reference(
        query="客厅财位点摆？",
        organization_id=1,
        document_uuids=["faq-document"],
        timeout_seconds=0.001,
    )

    assert result == ""


@pytest.mark.asyncio
async def test_prepare_reference_passes_only_bounded_retrieval_contract(monkeypatch) -> None:
    retrieval_call = AsyncMock(
        return_value={"chunks": [{"text": "财位宜整洁通爽。"}]}
    )
    monkeypatch.setattr(retrieval, "retrieve_from_knowledge_base", retrieval_call)

    result = await retrieval.prepare_direct_faq_reference(
        query="客厅财位点摆？",
        organization_id=7,
        document_uuids=["faq-document"],
        embeddings_api_key="test-key",
        embeddings_model="test-embedding",
        embeddings_provider="dashscope",
    )

    assert "财位宜整洁通爽。" in result
    assert retrieval_call.await_args.kwargs["limit"] == retrieval.MAX_CHUNKS
    assert retrieval_call.await_args.kwargs["document_uuids"] == ["faq-document"]


def test_local_faq_search_prefers_matching_question_and_answer(tmp_path) -> None:
    source = tmp_path / "faq.txt"
    source.write_text(
        """FAQ 编号：风水-1
分类：家居风水
用户问法（口语）：客厅财位点样摆先好？
粤语参考回答：财位宜整洁明亮，避免堆杂物。

FAQ 编号：生肖-1
分类：生肖运程
用户问法（口语）：属鼠今年事业点？
粤语参考回答：主动应对变化会较稳阵。
""",
        encoding="utf-8",
    )

    chunks = retrieval.search_local_faq("客厅财位应该点摆？", source)

    assert len(chunks) == 1
    assert "财位宜整洁明亮" in chunks[0]["text"]
    assert "属鼠" not in chunks[0]["text"]


@pytest.mark.asyncio
async def test_prepare_reference_uses_local_faq_without_embedding_request(tmp_path) -> None:
    source = tmp_path / "faq.txt"
    source.write_text(
        """FAQ 编号：风水-1
分类：家居风水
用户问法（口语）：客厅财位点样摆先好？
粤语参考回答：财位宜整洁明亮，避免堆杂物。
""",
        encoding="utf-8",
    )

    result = await retrieval.prepare_direct_faq_reference(
        query="客厅财位应该点摆？",
        organization_id=1,
        document_uuids=[],
        local_faq_source_path=source,
    )

    assert "财位宜整洁明亮" in result
