#!/usr/bin/env python3
"""Apply the Cantonese voice instruction without re-entering provider keys."""

from __future__ import annotations

import argparse
import asyncio

from api.db.organization_configuration_client import (
    OrganizationConfigurationConflictError,
)
from api.schemas.ai_model_configuration import OrganizationAIModelConfigurationV2
from api.services.configuration.ai_model_configuration import (
    mutate_organization_ai_model_configuration_v2,
)
from api.services.configuration.registry import ServiceProviders

CANTONESE_TTS_INSTRUCTION = (
    "請全程用自然香港廣東話，保留參考音頻嘅語氣同停頓，"
    "語速自然略快，句尾放鬆，避免普通話口音。"
)
YISHUI_TTS_MODEL = "qwen-audio-3.0-tts-plus"
YISHUI_TTS_VOICE = (
    "qwen-audio-3.0-tts-plus-yueplus-09f56beac201460299dcd235020285a9"
)
YISHUI_TTS_RATE = 1.15
MAX_PROFILE_SAVE_ATTEMPTS = 2


def _with_cantonese_profile(
    configuration: OrganizationAIModelConfigurationV2 | None,
) -> OrganizationAIModelConfigurationV2:
    if configuration is None:
        raise SystemExit("The organization has no V2 model configuration")
    byok = configuration.byok
    if configuration.mode != "byok" or byok is None or byok.mode != "pipeline":
        raise SystemExit("The active model configuration is not a BYOK pipeline")

    updated = configuration.model_copy(deep=True)
    pipeline = updated.byok.pipeline if updated.byok is not None else None
    if pipeline is None:
        raise SystemExit("The active BYOK configuration has no pipeline")
    tts = pipeline.tts
    if tts.provider != ServiceProviders.DASHSCOPE.value:
        raise SystemExit("The active TTS provider is not DashScope")
    tts.model = YISHUI_TTS_MODEL
    tts.voice = YISHUI_TTS_VOICE
    tts.instruction = CANTONESE_TTS_INSTRUCTION
    tts.rate = YISHUI_TTS_RATE
    return updated


async def apply(organization_id: int) -> None:
    try:
        await mutate_organization_ai_model_configuration_v2(
            organization_id,
            _with_cantonese_profile,
            max_attempts=MAX_PROFILE_SAVE_ATTEMPTS,
        )
    except OrganizationConfigurationConflictError:
        raise SystemExit(
            "Model configuration changed while applying the Cantonese profile. "
            "Please run the script again."
        ) from None
    print(
        f"Applied Cantonese TTS profile to organization {organization_id}: "
        f"model={YISHUI_TTS_MODEL}, rate={YISHUI_TTS_RATE}; "
        "provider credentials were preserved and not printed."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(apply(args.organization_id))


if __name__ == "__main__":
    main()
