"""Regression coverage for serialized organization-configuration writes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from api.db.models import OrganizationConfigurationModel
from api.db.organization_configuration_client import (
    OrganizationConfigurationClient,
    OrganizationConfigurationConflictError,
)


class _FakeResult:
    def __init__(self, row: SimpleNamespace) -> None:
        self.row = row

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(first=lambda: self.row)


class _LockAwareSession:
    def __init__(
        self,
        row: SimpleNamespace | None,
        *,
        commit_error: Exception | None = None,
    ) -> None:
        self.row = row
        self.commit_error = commit_error
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.added = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self.row)

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, row: SimpleNamespace) -> None:
        assert row is self.row or row is self.added

    def add(self, row: SimpleNamespace) -> None:
        self.added = row


@pytest.mark.asyncio
async def test_upsert_locks_existing_configuration_before_replacing_full_json() -> None:
    existing = SimpleNamespace(
        value={
            "byok": {
                "pipeline": {
                    "llm": {"model": "qwen-flash"},
                    "tts": {"provider": "ministream", "voice_preset_key": "mailinlin"},
                }
            }
        },
        updated_at=None,
        last_validated_at=None,
    )
    session = _LockAwareSession(existing)
    client = OrganizationConfigurationClient.__new__(OrganizationConfigurationClient)
    client.async_session = lambda: session
    replacement = {
        "byok": {
            "pipeline": {
                "llm": {"model": "qwen-flash-with-new-instructions"},
                "tts": {"provider": "ministream", "voice_preset_key": "mailinlin"},
            }
        }
    }

    stored = await client.upsert_configuration(
        organization_id=11,
        key="MODEL_CONFIGURATION_V2",
        value=replacement,
    )

    assert stored is existing
    assert session.committed is True
    assert session.rolled_back is False
    assert len(session.statements) == 1
    statement = session.statements[0]
    assert statement._for_update_arg is not None
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled
    assert existing.value["byok"]["pipeline"]["tts"] == {
        "provider": "ministream",
        "voice_preset_key": "mailinlin",
    }
    assert existing.value == replacement
    assert statement.get_final_froms() == [OrganizationConfigurationModel.__table__]


@pytest.mark.asyncio
async def test_upsert_rejects_a_stale_expected_revision_without_overwriting_json() -> None:
    stale_revision = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    current_revision = datetime(2026, 9, 1, 8, 1, tzinfo=UTC)
    existing = SimpleNamespace(
        value={"tts": {"provider": "ministream", "voice_preset_key": "mailinlin"}},
        updated_at=current_revision,
        last_validated_at=None,
    )
    session = _LockAwareSession(existing)
    client = OrganizationConfigurationClient.__new__(OrganizationConfigurationClient)
    client.async_session = lambda: session

    with pytest.raises(OrganizationConfigurationConflictError):
        await client.upsert_configuration(
            organization_id=11,
            key="MODEL_CONFIGURATION_V2",
            value={"tts": {"provider": "dashscope"}},
            expected_updated_at=stale_revision,
            expected_exists=True,
        )

    assert session.committed is False
    assert session.rolled_back is False
    assert existing.value == {
        "tts": {"provider": "ministream", "voice_preset_key": "mailinlin"}
    }


@pytest.mark.asyncio
async def test_upsert_without_an_expected_revision_keeps_other_configuration_behavior() -> None:
    existing = SimpleNamespace(
        value={"theme": "dark"},
        updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        last_validated_at=None,
    )
    session = _LockAwareSession(existing)
    client = OrganizationConfigurationClient.__new__(OrganizationConfigurationClient)
    client.async_session = lambda: session

    stored = await client.upsert_configuration(
        organization_id=11,
        key="ORGANIZATION_PREFERENCES",
        value={"theme": "light"},
    )

    assert stored is existing
    assert session.committed is True
    assert existing.value == {"theme": "light"}


@pytest.mark.asyncio
async def test_upsert_maps_a_concurrent_first_insert_to_a_stale_snapshot_conflict() -> None:
    session = _LockAwareSession(
        None,
        commit_error=IntegrityError("insert", {}, RuntimeError("duplicate key")),
    )
    client = OrganizationConfigurationClient.__new__(OrganizationConfigurationClient)
    client.async_session = lambda: session

    with pytest.raises(OrganizationConfigurationConflictError):
        await client.upsert_configuration(
            organization_id=11,
            key="MODEL_CONFIGURATION_V2",
            value={"mode": "byok"},
            expected_exists=False,
        )

    assert session.committed is False
    assert session.rolled_back is True
