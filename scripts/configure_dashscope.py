#!/usr/bin/env python3
"""Configure an organization-wide Alibaba DashScope BYOK pipeline.

The API key is intentionally accepted only from ``DASHSCOPE_API_KEY`` or
standard input/getpass. It is never accepted as a command-line argument, which
keeps it out of shell history and process listings.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from api.db.organization_configuration_client import (
    OrganizationConfigurationConflictError,
)
from api.schemas.ai_model_configuration import (
    BYOKAIModelConfiguration,
    BYOKPipelineAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.ai_model_configuration import (
    check_for_masked_keys_in_ai_model_configuration_v2,
    get_organization_ai_model_configuration_v2_snapshot,
    upsert_organization_ai_model_configuration_v2,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.options import (
    DASHSCOPE_COMPATIBLE_BASE_URL,
    DASHSCOPE_EMBEDDING_DIMENSION,
    DASHSCOPE_WEBSOCKET_URL,
)
from api.services.configuration.registry import (
    DashScopeEmbeddingsConfiguration,
    DashScopeLLMConfiguration,
    DashScopeSTTConfiguration,
    DashScopeTTSConfiguration,
)

DEFAULT_CANTONESE_TTS_INSTRUCTION = (
    "請全程用自然香港廣東話，保留參考音頻嘅語氣同停頓，"
    "語速自然略快，句尾放鬆，避免普通話口音。"
)


def _read_api_key() -> str:
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        if sys.stdin.isatty():
            key = getpass.getpass("DashScope API key: ").strip()
        else:
            key = sys.stdin.readline().strip()
    if not key:
        raise SystemExit("DashScope API key is required")
    return key


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--llm-model", default="qwen-flash")
    parser.add_argument(
        "--tts-model",
        default="qwen-audio-3.0-tts-plus",
    )
    parser.add_argument(
        "--tts-voice",
        default=(
            "qwen-audio-3.0-tts-plus-"
            "yueplus-09f56beac201460299dcd235020285a9"
        ),
    )
    parser.add_argument(
        "--tts-instruction",
        default=DEFAULT_CANTONESE_TTS_INSTRUCTION,
        help="Natural-language dialect/style instruction sent to DashScope TTS.",
    )
    parser.add_argument("--tts-rate", type=float, default=1.15)
    parser.add_argument("--stt-model", default="fun-asr-realtime-2026-02-28")
    parser.add_argument("--embedding-model", default="qwen3.7-text-embedding")
    return parser.parse_args()


async def _configure(args: argparse.Namespace, api_key: str) -> None:
    snapshot = await get_organization_ai_model_configuration_v2_snapshot(
        args.organization_id
    )
    configuration = OrganizationAIModelConfigurationV2(
        mode="byok",
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration(
                llm=DashScopeLLMConfiguration(
                    api_key=api_key,
                    model=args.llm_model,
                    base_url=DASHSCOPE_COMPATIBLE_BASE_URL,
                    temperature=0.1,
                ),
                tts=DashScopeTTSConfiguration(
                    api_key=api_key,
                    model=args.tts_model,
                    voice=args.tts_voice,
                    websocket_url=DASHSCOPE_WEBSOCKET_URL,
                    sample_rate=24000,
                    audio_format="pcm",
                    instruction=args.tts_instruction,
                    rate=args.tts_rate,
                ),
                stt=DashScopeSTTConfiguration(
                    api_key=api_key,
                    model=args.stt_model,
                    language="zh",
                    websocket_url=DASHSCOPE_WEBSOCKET_URL,
                    sample_rate=16000,
                    audio_format="pcm",
                ),
                embeddings=DashScopeEmbeddingsConfiguration(
                    api_key=api_key,
                    model=args.embedding_model,
                    base_url=DASHSCOPE_COMPATIBLE_BASE_URL,
                    dimensions=DASHSCOPE_EMBEDDING_DIMENSION,
                ),
            ),
        ),
    )

    check_for_masked_keys_in_ai_model_configuration_v2(configuration)
    effective = compile_ai_model_configuration_v2(configuration)
    await UserConfigurationValidator().validate(
        effective,
        organization_id=args.organization_id,
        created_by="local-configuration-script",
    )
    try:
        await upsert_organization_ai_model_configuration_v2(
            args.organization_id,
            configuration,
            expected_snapshot=snapshot,
        )
    except OrganizationConfigurationConflictError:
        raise SystemExit(
            "Model configuration changed while configuring DashScope. "
            "Please run the script again."
        ) from None

    print(
        "Configured DashScope pipeline for organization "
        f"{args.organization_id}: LLM={args.llm_model}, "
        f"TTS={args.tts_model}, STT={args.stt_model}, "
        f"Embedding={args.embedding_model}."
    )
    print("No inference request was made; the API key was not printed.")


def main() -> None:
    args = _parse_args()
    api_key = _read_api_key()
    asyncio.run(_configure(args, api_key))


if __name__ == "__main__":
    main()
