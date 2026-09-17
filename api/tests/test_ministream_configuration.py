import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pydantic import TypeAdapter, ValidationError

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    MiniStreamTTSConfiguration,
    ServiceProviders,
    ServiceType,
    TTSConfig,
)
from api.services.pipecat.service_factory import create_tts_service


_TRIAL_TLS_FINGERPRINT_SHA256 = (
    "9DC0B038C26CF47B35748C4717A50529A9D80636C21E9E170CCB24233144E4FD"
)


def test_ministream_tts_configuration_defaults_and_tts_only_registration():
    config = MiniStreamTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.MINISTREAM
    assert config.model == "ministream-tts"
    assert config.language == "yue"
    assert config.generation_mode == "preset_voice"
    assert config.voice_preset_key == "mailinlin"
    assert config.buffer == "off"
    assert config.buffer_idle_ms == 250
    assert config.max_generate_length == 500
    assert config.websocket_url == "wss://120.209.217.11:30700/ministream-ws/tts"
    assert config.http_base_url == "https://120.209.217.11:30700/ministream-api"
    assert config.sample_rate == 16_000
    assert config.playback_rate == 1.0
    assert config.verify_ssl is True
    assert config.tls_certificate_fingerprint_sha256 == _TRIAL_TLS_FINGERPRINT_SHA256
    assert config.model_json_schema()["title"] == "MiniStream"
    assert (
        REGISTRY[ServiceType.TTS][ServiceProviders.MINISTREAM]
        is MiniStreamTTSConfiguration
    )
    assert all(
        ServiceProviders.MINISTREAM not in REGISTRY[service_type]
        for service_type in (
            ServiceType.LLM,
            ServiceType.STT,
            ServiceType.EMBEDDINGS,
            ServiceType.REALTIME,
        )
    )


def test_ministream_tts_config_is_accepted_by_tts_discriminated_union():
    config = TypeAdapter(TTSConfig).validate_python(
        {"provider": "ministream", "api_key": "test-key"}
    )

    assert isinstance(config, MiniStreamTTSConfiguration)


def test_ministream_rejects_all_unverified_tls_configurations():
    with pytest.raises(ValidationError, match="verify_ssl=True"):
        MiniStreamTTSConfiguration(
            api_key="test-key",
            verify_ssl=False,
        )

    with pytest.raises(ValidationError, match="verify_ssl=True"):
        MiniStreamTTSConfiguration(
            api_key="test-key",
            websocket_url="wss://custom.example/ministream-ws/tts",
            verify_ssl=False,
        )

    config = MiniStreamTTSConfiguration(
        api_key="test-key",
        websocket_url="wss://custom.example/ministream-ws/tts",
        verify_ssl=True,
    )

    assert config.verify_ssl is True


def test_ministream_rejects_unverified_custom_http_base_url():
    with pytest.raises(ValidationError, match="verify_ssl=True"):
        MiniStreamTTSConfiguration(
            api_key="test-key",
            http_base_url="https://custom.example/ministream-api",
            verify_ssl=False,
        )

    config = MiniStreamTTSConfiguration(
        api_key="test-key",
        http_base_url="https://custom.example/ministream-api",
        verify_ssl=True,
    )

    assert config.verify_ssl is True


def test_ministream_requires_tls_schemes_for_every_endpoint():
    with pytest.raises(ValidationError, match="WSS"):
        MiniStreamTTSConfiguration(
            api_key="test-key",
            websocket_url="ws://custom.example/ministream-ws/tts",
        )

    with pytest.raises(ValidationError, match="HTTPS"):
        MiniStreamTTSConfiguration(
            api_key="test-key",
            http_base_url="http://custom.example/ministream-api",
        )


def test_ministream_key_validation_only_checks_key_presence():
    validator = UserConfigurationValidator()

    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        assert (
            validator._validate_service(
                MiniStreamTTSConfiguration(api_key="test-key"),
                "tts",
            )
            == []
        )

    mock_get.assert_not_called()


def test_ministream_factory_forwards_streaming_contract_via_local_import(monkeypatch):
    fake_service = Mock()
    fake_module = ModuleType("api.services.ministream.tts")
    fake_module.MiniStreamTTSService = fake_service
    monkeypatch.setitem(sys.modules, "api.services.ministream.tts", fake_module)
    user_config = SimpleNamespace(tts=MiniStreamTTSConfiguration(api_key="test-key"))

    create_tts_service(
        user_config,
        SimpleNamespace(transport_in_sample_rate=16_000),
    )

    fake_service.assert_called_once()
    kwargs = fake_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["model"] == "ministream-tts"
    assert kwargs["websocket_url"] == "wss://120.209.217.11:30700/ministream-ws/tts"
    assert kwargs["language"] == "yue"
    assert kwargs["generation_mode"] == "preset_voice"
    assert kwargs["voice_preset_key"] == "mailinlin"
    assert kwargs["buffer"] == "off"
    assert kwargs["buffer_idle_ms"] == 250
    assert kwargs["max_generate_length"] == 500
    assert kwargs["sample_rate"] == 16_000
    assert kwargs["playback_rate"] == 1.0
    assert kwargs["verify_ssl"] is True
    assert kwargs["tls_certificate_fingerprint_sha256"] == _TRIAL_TLS_FINGERPRINT_SHA256
    assert len(kwargs["text_filters"]) == 1
    assert kwargs["skip_aggregator_types"] == ["recording_router", "recording"]
    assert "http_base_url" not in kwargs
