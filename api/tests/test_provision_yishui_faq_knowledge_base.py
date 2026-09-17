"""Unit tests for the idempotent Yishui FAQ knowledge-base provisioner."""

from __future__ import annotations

from scripts.provision_yishui_faq_knowledge_base import (
    build_direct_faq_configurations,
    describe_source,
    is_retryable_document_status,
)


def test_describe_source_uses_sha256_and_plain_text_metadata(tmp_path) -> None:
    source = tmp_path / "faq.txt"
    source.write_text("香港粤语 FAQ", encoding="utf-8")

    descriptor = describe_source(source)

    assert descriptor["filename"] == "faq.txt"
    assert descriptor["mime_type"] == "text/plain"
    assert descriptor["file_size_bytes"] == len("香港粤语 FAQ".encode())
    assert len(descriptor["file_hash"]) == 64


def test_build_direct_faq_configurations_replaces_only_direct_faq_documents() -> None:
    previous = {
        "direct_consultation_question_limit": 3,
        "memory_configuration": {"enabled": True},
        "direct_knowledge_document_uuids": ["old-document"],
    }

    updated = build_direct_faq_configurations(previous, "new-faq-document")

    assert updated == {
        "direct_consultation_question_limit": 3,
        "memory_configuration": {"enabled": True},
        "direct_knowledge_document_uuids": ["new-faq-document"],
        "direct_local_faq_enabled": False,
    }
    assert previous["direct_knowledge_document_uuids"] == ["old-document"]


def test_build_direct_faq_configurations_can_enable_local_fast_search() -> None:
    updated = build_direct_faq_configurations(
        {"direct_consultation_question_limit": 3}, None
    )

    assert updated == {
        "direct_consultation_question_limit": 3,
        "direct_knowledge_document_uuids": [],
        "direct_local_faq_enabled": True,
    }


def test_only_failed_or_pending_documents_are_safe_to_retry() -> None:
    assert is_retryable_document_status("failed") is True
    assert is_retryable_document_status("pending") is True
    assert is_retryable_document_status("processing") is False
    assert is_retryable_document_status("completed") is False
