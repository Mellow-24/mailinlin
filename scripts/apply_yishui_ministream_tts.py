"""Switch only an existing Yishui BYOK pipeline's TTS to MiniStream.

The MiniStream credential is deliberately read from the process environment,
never a command-line argument, so it cannot appear in shell history or a
process listing. Existing LLM, STT, and embeddings settings remain unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import NoReturn

from api.db.organization_configuration_client import (
    OrganizationConfigurationConflictError,
)
from api.schemas.ai_model_configuration import (
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.ai_model_configuration import (
    check_for_masked_keys_in_ai_model_configuration_v2,
    mutate_organization_ai_model_configuration_v2,
)
from api.services.configuration.registry import MiniStreamTTSConfiguration

MAX_MINISTREAM_SAVE_ATTEMPTS = 2


def _read_api_key() -> str:
    api_key = os.getenv("MINISTREAM_TTS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MINISTREAM_TTS_API_KEY is required")
    return api_key


def _with_ministream_tts(
    configuration: OrganizationAIModelConfigurationV2 | None,
    *,
    api_key: str,
) -> OrganizationAIModelConfigurationV2:
    if configuration is None:
        raise SystemExit("The organization has no V2 model configuration")
    if (
        configuration.mode != "byok"
        or configuration.byok is None
        or configuration.byok.mode != "pipeline"
        or configuration.byok.pipeline is None
    ):
        raise SystemExit("The active model configuration is not a BYOK pipeline")

    updated = configuration.model_copy(deep=True)
    pipeline = updated.byok.pipeline
    if pipeline is None:  # Defensive: the discriminator validation above proves this.
        raise SystemExit("The active BYOK configuration has no pipeline")

    try:
        pipeline.tts = MiniStreamTTSConfiguration(api_key=api_key)
        check_for_masked_keys_in_ai_model_configuration_v2(updated)
        effective = compile_ai_model_configuration_v2(updated)
    except ValueError:
        raise SystemExit(
            "The active model configuration could not be safely compiled"
        ) from None

    if not isinstance(effective.tts, MiniStreamTTSConfiguration):
        raise SystemExit("MiniStream TTS configuration could not be compiled")
    return updated


async def _replace_tts_with_retry(
    organization_id: int,
    *,
    api_key: str,
) -> OrganizationAIModelConfigurationV2:
    """Apply the TTS-only mutation from a fresh config snapshot on each retry."""
    return await mutate_organization_ai_model_configuration_v2(
        organization_id,
        lambda configuration: _with_ministream_tts(configuration, api_key=api_key),
        max_attempts=MAX_MINISTREAM_SAVE_ATTEMPTS,
    )


async def apply(organization_id: int) -> None:
    api_key = _read_api_key()
    try:
        updated = await _replace_tts_with_retry(organization_id, api_key=api_key)
    except SystemExit:
        raise
    except OrganizationConfigurationConflictError:
        raise SystemExit(
            "Model configuration changed while updating TTS. Please run the switcher again."
        ) from None
    except Exception:
        raise SystemExit("Unable to save MiniStream TTS configuration") from None

    tts = updated.byok.pipeline.tts
    print(
        "Switched only the TTS configuration for organization "
        f"{organization_id} to MiniStream: model={tts.model}, "
        f"language={tts.language}, preset={tts.voice_preset_key}. "
        "Existing LLM, STT, and embeddings settings were preserved; "
        "the MiniStream API key was not printed."
    )


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject unsupported flags without echoing their potentially secret values."""

    def error(self, message: str) -> NoReturn:
        raise SystemExit("Invalid command-line arguments")


def _parse_args() -> argparse.Namespace:
    parser = _SafeArgumentParser(
        description="Switch an existing Yishui BYOK pipeline to MiniStream TTS."
    )
    parser.add_argument("--organization-id", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(apply(args.organization_id))


if __name__ == "__main__":
    main()
