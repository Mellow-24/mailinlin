"""Regression coverage for restoring the Yishui MiniStream rate to 1.0."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api.schemas.ai_model_configuration import (
    BYOKAIModelConfiguration,
    BYOKPipelineAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
)
from api.services.configuration.registry import (
    DashScopeLLMConfiguration,
    DashScopeSTTConfiguration,
    MiniStreamTTSConfiguration,
)
from scripts import set_yishui_ministream_tts_playback_rate as speed_setter


def _configuration() -> OrganizationAIModelConfigurationV2:
    return OrganizationAIModelConfigurationV2(
        mode="byok",
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration(
                llm=DashScopeLLMConfiguration(api_key="llm-secret", model="qwen-flash"),
                tts=MiniStreamTTSConfiguration(
                    api_key="tts-secret",
                    playback_rate=1.1,
                ),
                stt=DashScopeSTTConfiguration(api_key="stt-secret"),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_apply_changes_only_existing_ministream_playback_rate(monkeypatch) -> None:
    existing = _configuration()
    original_llm = existing.byok.pipeline.llm.model_dump(mode="json")
    original_stt = existing.byok.pipeline.stt.model_dump(mode="json")

    async def mutate(organization_id, update, *, max_attempts):
        assert organization_id == 11
        assert max_attempts == speed_setter.MAX_SAVE_ATTEMPTS
        return update(existing)

    monkeypatch.setattr(
        speed_setter,
        "mutate_organization_ai_model_configuration_v2",
        mutate,
    )

    updated = await speed_setter.apply(11)

    assert isinstance(updated.byok.pipeline.tts, MiniStreamTTSConfiguration)
    assert updated.byok.pipeline.tts.playback_rate == 1.0
    assert updated.byok.pipeline.tts.api_key == "tts-secret"
    assert updated.byok.pipeline.llm.model_dump(mode="json") == original_llm
    assert updated.byok.pipeline.stt.model_dump(mode="json") == original_stt
    assert existing.byok.pipeline.tts.playback_rate == 1.1
