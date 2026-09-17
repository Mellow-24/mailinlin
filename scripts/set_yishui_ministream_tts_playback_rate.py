#!/usr/bin/env python3
"""Restore the existing Yishui MiniStream TTS playback rate to natural speed.

This mutates only the saved ``playback_rate`` field.  In particular, it does
not need to read, accept, print, or replace the already configured TTS key.
"""

from __future__ import annotations

import argparse
import asyncio

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


TARGET_PLAYBACK_RATE = 1.0
MAX_SAVE_ATTEMPTS = 2


def _with_natural_playback_rate(
    configuration: OrganizationAIModelConfigurationV2 | None,
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
    if pipeline is None or not isinstance(pipeline.tts, MiniStreamTTSConfiguration):
        raise SystemExit("The active pipeline does not use MiniStream TTS")

    pipeline.tts = pipeline.tts.model_copy(
        update={"playback_rate": TARGET_PLAYBACK_RATE}
    )
    try:
        check_for_masked_keys_in_ai_model_configuration_v2(updated)
        effective = compile_ai_model_configuration_v2(updated)
    except ValueError:
        raise SystemExit(
            "The active model configuration could not be safely compiled"
        ) from None
    if (
        not isinstance(effective.tts, MiniStreamTTSConfiguration)
        or effective.tts.playback_rate != TARGET_PLAYBACK_RATE
    ):
        raise SystemExit("MiniStream TTS playback rate could not be compiled")
    return updated


async def apply(organization_id: int) -> OrganizationAIModelConfigurationV2:
    """Persist the rate-only change from a fresh configuration snapshot."""
    try:
        return await mutate_organization_ai_model_configuration_v2(
            organization_id,
            _with_natural_playback_rate,
            max_attempts=MAX_SAVE_ATTEMPTS,
        )
    except SystemExit:
        raise
    except OrganizationConfigurationConflictError:
        raise SystemExit(
            "Model configuration changed while updating TTS. Please run the rate setter again."
        ) from None
    except Exception:
        raise SystemExit("Unable to save MiniStream TTS playback rate") from None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore the saved Yishui MiniStream TTS playback rate to 1.0."
    )
    parser.add_argument("--organization-id", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(apply(args.organization_id))
    print(
        f"Set MiniStream TTS playback rate to {TARGET_PLAYBACK_RATE:.1f} "
        f"for organization {args.organization_id}; existing model and key were preserved."
    )


if __name__ == "__main__":
    main()
