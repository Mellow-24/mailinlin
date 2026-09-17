"""Regression coverage for the dedicated Yishui voice-demo embed token."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import provision_yishui_voice_demo as provisioner


def _args(tmp_path) -> SimpleNamespace:
    manifest = tmp_path / "greetings-04.json"
    manifest.write_text(
        json.dumps(
            {
                "asset_id": "greetings-04-ministream",
                "url": "/voice-demo/warm-audio/greetings-04.wav",
                "spoken_text": "新嘅開場白。",
                "duration_ms": 4321,
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        workflow_id=7,
        organization_id=11,
        user_id=13,
        allowed_domain=[],
        opening_manifest=manifest,
    )


def _db_client(tokens, *, created_token, updated_token) -> SimpleNamespace:
    return SimpleNamespace(
        get_organization_by_id=AsyncMock(return_value=object()),
        get_user_by_id=AsyncMock(return_value=object()),
        is_user_member_of_organization=AsyncMock(return_value=True),
        get_workflow=AsyncMock(return_value=object()),
        get_embed_tokens_by_workflow=AsyncMock(return_value=tokens),
        create_embed_token=AsyncMock(return_value=created_token),
        update_embed_token=AsyncMock(return_value=updated_token),
    )


@pytest.mark.parametrize(
    "audio_url",
    [
        "/\\evil.example/opening.wav",
        "//evil.example/opening.wav",
        "///evil.example/opening.wav",
        "/voice-demo/warm-audio/greetings-04.wav?signature=not-public",
        "/voice-demo/warm-audio/greetings-04.wav#fragment",
    ],
)
def test_manifest_rejects_nonlocal_or_credential_bearing_audio_urls(
    tmp_path, audio_url
) -> None:
    manifest = tmp_path / "greetings-04.json"
    manifest.write_text(
        json.dumps(
            {
                "asset_id": "greetings-04-ministream",
                "url": audio_url,
                "spoken_text": "新嘅開場白。",
                "duration_ms": 4321,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="public audio URL"):
        provisioner.load_opening_asset_manifest(manifest)


@pytest.mark.asyncio
async def test_provision_creates_dedicated_demo_token_without_mutating_unrelated_active_token(
    tmp_path, monkeypatch
) -> None:
    unrelated_settings = {"widgetType": "chat"}
    unrelated_token = SimpleNamespace(
        id=31,
        token="unrelated-token",
        is_active=True,
        settings=unrelated_settings,
    )
    created_token = SimpleNamespace(token="dedicated-demo-token")
    db_client = _db_client(
        [unrelated_token],
        created_token=created_token,
        updated_token=unrelated_token,
    )
    monkeypatch.setattr(provisioner, "db_client", db_client)

    token = await provisioner.provision(_args(tmp_path))

    assert token == "dedicated-demo-token"
    db_client.update_embed_token.assert_not_awaited()
    db_client.create_embed_token.assert_awaited_once()
    assert unrelated_token.settings == unrelated_settings
    assert (
        db_client.create_embed_token.await_args.kwargs["settings"]["directVoiceDemo"]
        is True
    )
    settings = db_client.create_embed_token.await_args.kwargs["settings"]
    assert settings["clientOpeningAssetId"] == "greetings-04-ministream"
    assert (
        settings["clientOpeningAudioUrl"] == "/voice-demo/warm-audio/greetings-04.wav"
    )
    assert settings["clientOpeningTranscript"] == "新嘅開場白。"
    assert settings["clientOpeningDurationMs"] == 4321


@pytest.mark.asyncio
async def test_provision_updates_existing_dedicated_demo_token_before_unrelated_active_token(
    tmp_path, monkeypatch
) -> None:
    unrelated_token = SimpleNamespace(
        id=31,
        token="unrelated-token",
        is_active=True,
        settings={"widgetType": "chat"},
    )
    existing_demo_token = SimpleNamespace(
        id=32,
        token="existing-demo-token",
        is_active=True,
        settings={"directVoiceDemo": True},
    )
    db_client = _db_client(
        [unrelated_token, existing_demo_token],
        created_token=SimpleNamespace(token="new-demo-token"),
        updated_token=SimpleNamespace(token="existing-demo-token"),
    )
    monkeypatch.setattr(provisioner, "db_client", db_client)

    token = await provisioner.provision(_args(tmp_path))

    assert token == "existing-demo-token"
    db_client.create_embed_token.assert_not_awaited()
    db_client.update_embed_token.assert_awaited_once()
    assert db_client.update_embed_token.await_args.args == (32, 11)
    settings = db_client.update_embed_token.await_args.kwargs["settings"]
    assert settings["clientOpeningAssetId"] == "greetings-04-ministream"
    assert (
        settings["clientOpeningAudioUrl"] == "/voice-demo/warm-audio/greetings-04.wav"
    )
    assert settings["clientOpeningTranscript"] == "新嘅開場白。"
    assert settings["clientOpeningDurationMs"] == 4321
