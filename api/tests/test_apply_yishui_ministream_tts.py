"""Regression tests for the credential-safe MiniStream TTS switcher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api.db.organization_configuration_client import (
    OrganizationConfigurationConflictError,
)
from api.schemas.ai_model_configuration import (
    BYOKAIModelConfiguration,
    BYOKPipelineAIModelConfiguration,
    DograhManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
)
from api.services.configuration.registry import (
    DashScopeEmbeddingsConfiguration,
    DashScopeLLMConfiguration,
    DashScopeSTTConfiguration,
    DashScopeTTSConfiguration,
    MiniStreamTTSConfiguration,
)
from scripts import apply_yishui_ministream_tts as switcher


def _pipeline_configuration() -> OrganizationAIModelConfigurationV2:
    return OrganizationAIModelConfigurationV2(
        mode="byok",
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration(
                llm=DashScopeLLMConfiguration(
                    api_key="existing-llm-secret",
                    model="qwen-flash",
                    temperature=0.1,
                ),
                tts=DashScopeTTSConfiguration(
                    api_key="existing-tts-secret",
                    model="qwen-audio-3.0-tts-flash",
                    voice="existing-voice",
                ),
                stt=DashScopeSTTConfiguration(
                    api_key="existing-stt-secret",
                    model="fun-asr-realtime-2026-02-28",
                ),
                embeddings=DashScopeEmbeddingsConfiguration(
                    api_key="existing-embedding-secret",
                    model="qwen3.7-text-embedding",
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_apply_replaces_only_pipeline_tts_and_does_not_print_the_key(
    monkeypatch, capsys
) -> None:
    existing = _pipeline_configuration()
    original_pipeline = existing.byok.pipeline.model_dump(mode="json")
    supplied_key = "ministream-test-secret"
    captured: dict[str, object] = {}

    async def mutate(organization_id, update, *, max_attempts):
        captured["organization_id"] = organization_id
        captured["max_attempts"] = max_attempts
        return update(existing)

    monkeypatch.setenv("MINISTREAM_TTS_API_KEY", supplied_key)
    monkeypatch.setattr(
        switcher,
        "mutate_organization_ai_model_configuration_v2",
        mutate,
    )

    await switcher.apply(11)

    updated = await switcher._replace_tts_with_retry(
        11,
        api_key=supplied_key,
    )
    assert captured == {
        "organization_id": 11,
        "max_attempts": switcher.MAX_MINISTREAM_SAVE_ATTEMPTS,
    }
    assert isinstance(updated.byok.pipeline.tts, MiniStreamTTSConfiguration)
    assert updated.byok.pipeline.tts.model == "ministream-tts"
    assert updated.byok.pipeline.tts.language == "yue"
    assert updated.byok.pipeline.tts.generation_mode == "preset_voice"
    assert updated.byok.pipeline.tts.voice_preset_key == "mailinlin"
    assert updated.byok.pipeline.tts.buffer == "off"
    assert updated.byok.pipeline.tts.sample_rate == 16_000
    assert updated.byok.pipeline.tts.playback_rate == 1.0
    assert updated.byok.pipeline.llm.model_dump(mode="json") == original_pipeline["llm"]
    assert updated.byok.pipeline.stt.model_dump(mode="json") == original_pipeline["stt"]
    assert (
        updated.byok.pipeline.embeddings.model_dump(mode="json")
        == original_pipeline["embeddings"]
    )
    assert existing.byok.pipeline.model_dump(mode="json") == original_pipeline
    assert supplied_key not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_apply_rejects_a_non_pipeline_configuration_before_upserting(
    monkeypatch,
) -> None:
    non_pipeline_configuration = OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(api_key="managed-secret"),
    )

    async def mutate(_organization_id, update, *, max_attempts):
        return update(non_pipeline_configuration)

    monkeypatch.setenv("MINISTREAM_TTS_API_KEY", "ministream-test-secret")
    monkeypatch.setattr(
        switcher,
        "mutate_organization_ai_model_configuration_v2",
        mutate,
    )

    with pytest.raises(SystemExit, match="BYOK pipeline"):
        await switcher.apply(11)


@pytest.mark.asyncio
async def test_apply_requires_the_environment_key_before_loading_configuration(
    monkeypatch,
) -> None:
    mutate = AsyncMock()
    monkeypatch.delenv("MINISTREAM_TTS_API_KEY", raising=False)
    monkeypatch.setattr(
        switcher,
        "mutate_organization_ai_model_configuration_v2",
        mutate,
    )

    with pytest.raises(SystemExit, match="MINISTREAM_TTS_API_KEY"):
        await switcher.apply(11)

    mutate.assert_not_awaited()


def test_parser_does_not_accept_an_api_key_argument(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "apply_yishui_ministream_tts.py",
            "--organization-id",
            "11",
            "--api-key",
            "must-not-be-a-cli-argument",
        ],
    )

    with pytest.raises(SystemExit) as exception_info:
        switcher._parse_args()

    captured = capsys.readouterr()
    assert "must-not-be-a-cli-argument" not in captured.out
    assert "must-not-be-a-cli-argument" not in captured.err
    assert "Invalid command-line arguments" in str(exception_info.value)


@pytest.mark.asyncio
async def test_apply_sanitizes_a_persistence_exception_that_contains_the_key(
    monkeypatch, capsys
) -> None:
    supplied_key = "ministream-test-secret"
    persistence_error = RuntimeError(f"database rejected {supplied_key}")
    monkeypatch.setenv("MINISTREAM_TTS_API_KEY", supplied_key)
    monkeypatch.setattr(
        switcher,
        "mutate_organization_ai_model_configuration_v2",
        AsyncMock(side_effect=persistence_error),
    )

    with pytest.raises(SystemExit) as exception_info:
        await switcher.apply(11)

    captured = capsys.readouterr()
    assert supplied_key not in str(exception_info.value)
    assert supplied_key not in captured.out
    assert supplied_key not in captured.err
    assert "Unable to save MiniStream TTS configuration" in str(exception_info.value)


@pytest.mark.asyncio
async def test_apply_reports_a_bounded_retry_conflict_without_leaking_the_key(
    monkeypatch, capsys
) -> None:
    supplied_key = "ministream-test-secret"
    monkeypatch.setenv("MINISTREAM_TTS_API_KEY", supplied_key)
    monkeypatch.setattr(
        switcher,
        "mutate_organization_ai_model_configuration_v2",
        AsyncMock(
            side_effect=OrganizationConfigurationConflictError("changed concurrently")
        ),
    )

    with pytest.raises(SystemExit) as exception_info:
        await switcher.apply(11)

    captured = capsys.readouterr()
    assert "run the switcher again" in str(exception_info.value)
    assert supplied_key not in str(exception_info.value)
    assert supplied_key not in captured.out
    assert supplied_key not in captured.err
