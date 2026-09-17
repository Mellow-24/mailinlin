#!/usr/bin/env python3
"""Apply the measured low-latency model settings without exposing provider keys."""

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

MAX_PROFILE_SAVE_ATTEMPTS = 2


def _with_low_latency_profile(
    configuration: OrganizationAIModelConfigurationV2 | None,
    *,
    llm_model: str,
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
    if pipeline.llm.provider != ServiceProviders.DASHSCOPE.value:
        raise SystemExit("The active LLM provider is not DashScope")

    pipeline.llm.model = llm_model
    pipeline.llm.enable_thinking = False
    # Streaming still starts at the first token; this only raises the ceiling
    # enough for the third-answer synthesis to be genuinely useful.
    pipeline.llm.max_tokens = 320
    pipeline.llm.temperature = 0.1
    return updated


async def apply(organization_id: int, llm_model: str) -> None:
    try:
        await mutate_organization_ai_model_configuration_v2(
            organization_id,
            lambda configuration: _with_low_latency_profile(
                configuration,
                llm_model=llm_model,
            ),
            max_attempts=MAX_PROFILE_SAVE_ATTEMPTS,
        )
    except OrganizationConfigurationConflictError:
        raise SystemExit(
            "Model configuration changed while applying the performance profile. "
            "Please run the script again."
        ) from None
    print(
        f"Applied low-latency LLM profile to organization {organization_id}: "
        f"model={llm_model}, thinking=off, max_tokens=320. "
        "Provider credentials were preserved and not printed."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--llm-model", default="qwen-flash")
    args = parser.parse_args()
    asyncio.run(apply(args.organization_id, args.llm_model))


if __name__ == "__main__":
    main()
