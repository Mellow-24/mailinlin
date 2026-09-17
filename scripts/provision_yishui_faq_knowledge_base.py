#!/usr/bin/env python3
"""Configure the Yishui Cantonese FAQ for the fast direct-demo path.

The default local mode searches the generated FAQ text directly, avoiding an
embedding request in the user-facing hot path. The optional vector mode keeps
the FAQ document outside workflow nodes: node documents are exposed as LLM
tools and can trigger an extra model round.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

from api.db import db_client
from api.services.storage import storage_fs
from api.tasks.knowledge_base_processing import process_knowledge_base_document


DEFAULT_SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "knowledge-base"
    / "麦玲玲AI_Agent_FAQ库_粤语_RAG.txt"
)


def describe_source(source_path: Path) -> dict[str, Any]:
    """Return stable storage metadata without touching application state."""
    content = source_path.read_bytes()
    return {
        "filename": source_path.name,
        "file_size_bytes": len(content),
        "file_hash": hashlib.sha256(content).hexdigest(),
        "mime_type": "text/plain",
    }


def build_direct_faq_configurations(
    configurations: dict[str, Any] | None,
    document_uuid: str | None,
) -> dict[str, Any]:
    """Bind a vector document or enable local search without other changes."""
    if document_uuid:
        return {
            **(configurations or {}),
            "direct_knowledge_document_uuids": [document_uuid],
            "direct_local_faq_enabled": False,
        }
    return {
        **(configurations or {}),
        "direct_knowledge_document_uuids": [],
        "direct_local_faq_enabled": True,
    }


def is_retryable_document_status(status: object) -> bool:
    """Return whether an orphaned or failed ingest can be safely resumed."""
    return status in {"pending", "failed"}


async def _get_or_create_completed_document(
    *,
    source_path: Path,
    descriptor: dict[str, Any],
    organization_id: int,
    user_id: int,
    provider_id: str,
):
    existing = await db_client.get_document_by_hash(
        descriptor["file_hash"], organization_id
    )
    if existing is not None:
        if existing.processing_status == "completed":
            return existing
        if not is_retryable_document_status(existing.processing_status):
            raise SystemExit(
                "An existing FAQ document is still not ready "
                f"(status: {existing.processing_status})."
            )
        document = existing
        metadata = document.custom_metadata or {}
        s3_key = metadata.get("s3_key")
        if not isinstance(s3_key, str) or not s3_key:
            raise SystemExit("Existing FAQ document has no storage key to retry.")
    else:
        document_uuid = str(uuid.uuid4())
        s3_key = (
            f"knowledge_base/{organization_id}/{document_uuid}/{descriptor['filename']}"
        )
        document = await db_client.create_document(
            organization_id=organization_id,
            created_by=user_id,
            filename=descriptor["filename"],
            file_size_bytes=descriptor["file_size_bytes"],
            file_hash=descriptor["file_hash"],
            mime_type=descriptor["mime_type"],
            custom_metadata={
                "s3_key": s3_key,
                "source": "yishui_cantonese_faq",
            },
            document_uuid=document_uuid,
            retrieval_mode="chunked",
        )

    uploaded = await storage_fs.aupload_file(str(source_path), s3_key)
    if not uploaded:
        raise SystemExit("Could not upload the FAQ source to configured storage.")

    await process_knowledge_base_document(
        {},
        document.id,
        s3_key,
        organization_id,
        provider_id,
        retrieval_mode="chunked",
    )
    processed = await db_client.get_document_by_id(document.id)
    if processed is None or processed.processing_status != "completed":
        raise SystemExit("FAQ document processing did not complete successfully.")
    return processed


async def provision(args: argparse.Namespace) -> str:
    source_path = args.source.resolve()
    if not source_path.is_file():
        raise SystemExit(f"FAQ source file not found: {source_path}")
    descriptor = describe_source(source_path)

    workflow = await db_client.get_workflow(args.workflow_id, args.organization_id)
    if workflow is None:
        raise SystemExit(f"Workflow {args.workflow_id} not found")
    user = await db_client.get_user_by_id(args.user_id)
    if user is None:
        raise SystemExit(f"User {args.user_id} not found")
    if not await db_client.is_user_member_of_organization(
        args.user_id, args.organization_id
    ):
        raise SystemExit("User is not a member of the requested organization")

    document_uuid: str | None = None
    if args.mode == "vector":
        document = await _get_or_create_completed_document(
            source_path=source_path,
            descriptor=descriptor,
            organization_id=args.organization_id,
            user_id=args.user_id,
            provider_id=str(user.provider_id),
        )
        document_uuid = document.document_uuid

    draft = await db_client.get_draft_version(args.workflow_id)
    source = draft or workflow.released_definition
    if source is None:
        raise SystemExit("Workflow has no published definition to configure")
    configurations = build_direct_faq_configurations(
        source.workflow_configurations,
        document_uuid,
    )
    await db_client.save_workflow_draft(
        args.workflow_id,
        workflow_definition=source.workflow_json,
        workflow_configurations=configurations,
    )
    await db_client.publish_workflow_draft(args.workflow_id)
    return document_uuid or "local-faq"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest the Yishui Cantonese FAQ for direct voice retrieval."
    )
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument(
        "--mode",
        choices=("local", "vector"),
        default="local",
        help="Use local lexical search (default) or ingest vector embeddings.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    document_uuid = asyncio.run(provision(parse_args()))
    print(f"Yishui FAQ is ready: {document_uuid}")
