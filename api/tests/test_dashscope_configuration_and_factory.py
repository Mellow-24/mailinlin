"""Integration-level tests for Dograh's DashScope provider wiring."""

from types import SimpleNamespace

import pytest
from pipecat.services.qwen.llm import QwenLLMService

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    DashScopeEmbeddingsConfiguration,
    DashScopeLLMConfiguration,
    DashScopeSTTConfiguration,
    DashScopeTTSConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.dashscope.stt import DashScopeSTTService
from api.services.dashscope.tts import DashScopeTTSService
from api.services.gen_ai.embedding.dashscope_service import DashScopeEmbeddingService
from api.services.gen_ai.embedding.factory import build_embedding_service
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_stt_service,
    create_tts_service,
    stt_uses_external_turns,
)


def _configuration(api_key: str = "sk-dashscope-test") -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        llm=DashScopeLLMConfiguration(api_key=api_key),
        tts=DashScopeTTSConfiguration(api_key=api_key, instruction="", rate=1.03),
        stt=DashScopeSTTConfiguration(api_key=api_key),
        embeddings=DashScopeEmbeddingsConfiguration(api_key=api_key),
    )


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def test_dashscope_is_registered_for_all_four_pipeline_services():
    for service_type in (
        ServiceType.LLM,
        ServiceType.TTS,
        ServiceType.STT,
        ServiceType.EMBEDDINGS,
    ):
        assert ServiceProviders.DASHSCOPE in REGISTRY[service_type]


@pytest.mark.parametrize(
    ("configuration_class", "custom_fields"),
    [
        (DashScopeLLMConfiguration, ("model",)),
        (DashScopeTTSConfiguration, ("model", "voice")),
        (DashScopeSTTConfiguration, ("model", "language")),
        (DashScopeEmbeddingsConfiguration, ("model",)),
    ],
)
def test_dashscope_dropdown_fields_allow_custom_values(
    configuration_class, custom_fields
):
    properties = configuration_class.model_json_schema()["properties"]
    for field in custom_fields:
        assert properties[field]["allow_custom_input"] is True


@pytest.mark.asyncio
async def test_dashscope_configuration_validation_does_not_probe_openai_models(
    monkeypatch,
):
    def fail_if_openai_client_is_created(*args, **kwargs):
        raise AssertionError("DashScope validation must not call OpenAI /models")

    monkeypatch.setattr(
        "api.services.configuration.check_validity.openai.OpenAI",
        fail_if_openai_client_is_created,
    )

    result = await UserConfigurationValidator().validate(_configuration())

    assert result == {"status": [{"model": "all", "message": "ok"}]}


def test_dashscope_voice_pipeline_factories_use_native_services():
    configuration = _configuration()

    llm = create_llm_service(configuration)
    stt = create_stt_service(configuration, _audio_config())
    tts = create_tts_service(configuration, _audio_config())

    assert isinstance(llm, QwenLLMService)
    assert llm._settings.model == configuration.llm.model
    assert llm._settings.max_tokens == 256
    assert llm._settings.extra == {"extra_body": {"enable_thinking": False}}
    assert llm._client.timeout.connect == 12.0
    assert llm._client.timeout.read == 12.0
    assert llm._client.timeout.write == 12.0
    assert llm._client.timeout.pool == 12.0
    assert llm._client.max_retries == 0
    assert isinstance(stt, DashScopeSTTService)
    assert stt_uses_external_turns(configuration) is True
    assert stt._language_hints == ["zh"]
    assert stt._init_sample_rate == 16000
    assert isinstance(tts, DashScopeTTSService)
    assert tts._settings.voice == "longjiaxin_v3"
    parameters = tts._build_run_task("task-id")["payload"]["parameters"]
    assert "instruction" not in parameters
    assert parameters["rate"] == 1.03


@pytest.mark.asyncio
async def test_dashscope_embedding_factory_selects_1536_dimension_adapter():
    service = await build_embedding_service(
        db_client=SimpleNamespace(),
        provider=ServiceProviders.DASHSCOPE.value,
        api_key="sk-dashscope-test",
        model="qwen3.7-text-embedding",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert isinstance(service, DashScopeEmbeddingService)
    assert service.get_embedding_dimension() == 1536
    assert service._request_kwargs() == {"dimensions": 1536}
