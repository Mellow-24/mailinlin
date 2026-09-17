"""Tests for safe optimistic writes of organization model configuration v2."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.db.organization_configuration_client import (
    OrganizationConfigurationConflictError,
)
from api.schemas.ai_model_configuration import (
    DograhManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
)
from api.services.configuration import ai_model_configuration as model_configuration


def _configuration() -> OrganizationAIModelConfigurationV2:
    return OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(api_key="test-service-key"),
    )


@pytest.mark.asyncio
async def test_model_configuration_snapshot_carries_the_row_revision(monkeypatch) -> None:
    configuration = _configuration()
    revision = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    row = SimpleNamespace(
        value=configuration.model_dump(mode="json", exclude_none=True),
        updated_at=revision,
        last_validated_at=revision,
    )
    monkeypatch.setattr(
        model_configuration.db_client,
        "get_configuration",
        AsyncMock(return_value=row),
    )

    snapshot = await model_configuration.get_organization_ai_model_configuration_v2_snapshot(
        42
    )

    assert snapshot.exists is True
    assert snapshot.updated_at == revision
    assert snapshot.configuration == configuration


@pytest.mark.asyncio
async def test_model_configuration_save_uses_the_snapshot_as_a_compare_and_swap_guard(
    monkeypatch,
) -> None:
    configuration = _configuration()
    revision = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    snapshot = model_configuration.OrganizationAIModelConfigurationV2Snapshot(
        configuration=configuration,
        exists=True,
        updated_at=revision,
        last_validated_at=revision,
    )
    upsert = AsyncMock()
    monkeypatch.setattr(model_configuration.db_client, "upsert_configuration", upsert)

    await model_configuration.upsert_organization_ai_model_configuration_v2(
        42,
        configuration,
        expected_snapshot=snapshot,
    )

    assert upsert.await_args.args[:3] == (
        42,
        "MODEL_CONFIGURATION_V2",
        configuration.model_dump(mode="json", exclude_none=True),
    )
    assert upsert.await_args.kwargs["expected_exists"] is True
    assert upsert.await_args.kwargs["expected_updated_at"] == revision


@pytest.mark.asyncio
async def test_field_local_mutation_reloads_once_after_a_conflict(monkeypatch) -> None:
    first = model_configuration.OrganizationAIModelConfigurationV2Snapshot(
        configuration=_configuration(),
        exists=True,
        updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        last_validated_at=None,
    )
    second = model_configuration.OrganizationAIModelConfigurationV2Snapshot(
        configuration=_configuration(),
        exists=True,
        updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
        last_validated_at=None,
    )
    get_snapshot = AsyncMock(side_effect=[first, second])
    upsert = AsyncMock(
        side_effect=[
            OrganizationConfigurationConflictError("changed concurrently"),
            None,
        ]
    )
    monkeypatch.setattr(
        model_configuration,
        "get_organization_ai_model_configuration_v2_snapshot",
        get_snapshot,
    )
    monkeypatch.setattr(
        model_configuration,
        "upsert_organization_ai_model_configuration_v2",
        upsert,
    )

    def update_speed(
        configuration: OrganizationAIModelConfigurationV2 | None,
    ) -> OrganizationAIModelConfigurationV2:
        assert configuration is not None
        updated = configuration.model_copy(deep=True)
        assert updated.dograh is not None
        updated.dograh.speed = 1.2
        return updated

    updated = await model_configuration.mutate_organization_ai_model_configuration_v2(
        42,
        update_speed,
        max_attempts=2,
    )

    assert updated.dograh is not None
    assert updated.dograh.speed == 1.2
    assert get_snapshot.await_count == 2
    assert upsert.await_count == 2
    assert upsert.await_args_list[0].kwargs["expected_snapshot"] is first
    assert upsert.await_args_list[1].kwargs["expected_snapshot"] is second


@pytest.mark.asyncio
async def test_model_configuration_route_returns_409_for_a_stale_snapshot(
    monkeypatch,
) -> None:
    from api.routes import organization as organization_routes

    configuration = _configuration()
    snapshot = model_configuration.OrganizationAIModelConfigurationV2Snapshot(
        configuration=configuration,
        exists=True,
        updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        last_validated_at=None,
    )

    class FakeValidator:
        async def validate(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(
        organization_routes,
        "get_organization_ai_model_configuration_v2_snapshot",
        AsyncMock(return_value=snapshot),
        raising=False,
    )
    monkeypatch.setattr(
        organization_routes,
        "UserConfigurationValidator",
        lambda: FakeValidator(),
    )
    monkeypatch.setattr(
        organization_routes,
        "upsert_organization_ai_model_configuration_v2",
        AsyncMock(
            side_effect=OrganizationConfigurationConflictError(
                "changed concurrently"
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await organization_routes.save_model_configuration_v2(
            configuration,
            user=SimpleNamespace(
                selected_organization_id=42,
                provider_id="provider-42",
            ),
        )

    assert exc_info.value.status_code == 409
    assert "Refresh" in str(exc_info.value.detail)
