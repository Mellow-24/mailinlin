#!/usr/bin/env python3
"""Provision the reusable headless voice embed token for the Yishui demo.

Usage::

    set -a
    source api/.env
    set +a
    python -m scripts.provision_yishui_voice_demo \
        --workflow-id 1 \
        --organization-id 1 \
        --user-id 1 \
        --allowed-domain demo.example.com

To capture it directly for a local demo process::

    export YISHUI_VOICE_DEMO_TOKEN="$(python -m \
        scripts.provision_yishui_voice_demo \
        --workflow-id 1 --organization-id 1 --user-id 1)"

``--allowed-domain`` may be repeated. ``localhost`` and ``127.0.0.1`` are
always allowed so the standalone demo can run on any local development port.
On success, stdout contains only the embed token followed by a newline, making
the command safe to use in command substitution or an environment-file helper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from api.db import db_client
from api.utils.url_security import is_browser_safe_local_public_audio_path


LOCAL_ALLOWED_DOMAINS = ("localhost", "127.0.0.1")
DEFAULT_OPENING_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "ui/public/voice-demo/warm-audio/greetings-01.json"
)

HEADLESS_VOICE_SETTINGS = {
    "widgetType": "voice",
    "embedMode": "headless",
    # Allows this push-to-talk page to warm STT behind the fixed greeting. The
    # backend still waits for both greeting completion and transcriber ready
    # before the widget can enable its microphone.
    "fastOpening": True,
    "directVoiceDemo": True,
    # Fun-ASR finalizes after 800ms trailing silence. Keep the sender active a
    # little longer after the user clicks stop so final-only turns are committed.
    "recordingDrainMs": 1200,
    "buttonText": "开始语音咨询",
    "callToActionText": "点击开始，易水 AI 顾问会先向你问好",
    "voiceConnectingText": "正在连接易水 AI 顾问…",
    "voiceEndCallText": "结束咨询",
    "voiceRetryText": "重新连接",
    "voiceReadyTitle": "易水 AI 语音顾问",
    "voiceConnectingSubtext": "正在建立安全的语音连接，请稍候",
    "voiceConnectedTitle": "语音咨询进行中",
    "voiceConnectedSubtext": "点击录音按钮说话，顾问将以语音和文字回复",
    "voiceCallEndedTitle": "本次咨询已结束",
    "voiceCallEndedSubtext": "如需继续，可以重新开始一次语音咨询",
    "voiceConnectionFailedTitle": "连接失败",
    "voiceConnectionFailedSubtext": "请检查麦克风权限和网络后重试",
    "voiceConnectionLostTitle": "连接已断开",
    "voiceConnectionLostSubtext": "网络连接中断，请重新开始咨询",
    "autoStart": False,
}


def load_opening_asset_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load the public opening contract generated beside the WAV asset."""
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Unable to read opening asset manifest: {manifest_path}"
        ) from exc
    if not isinstance(raw_manifest, dict):
        raise SystemExit("Opening asset manifest must be a JSON object")

    asset_id = raw_manifest.get("asset_id")
    audio_url = raw_manifest.get("url")
    transcript = raw_manifest.get("spoken_text")
    duration_ms = raw_manifest.get("duration_ms")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise SystemExit("Opening asset manifest has no asset_id")
    if not is_browser_safe_local_public_audio_path(audio_url):
        raise SystemExit("Opening asset manifest has no public audio URL")
    if not isinstance(transcript, str) or not transcript.strip():
        raise SystemExit("Opening asset manifest has no spoken_text")
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or duration_ms <= 0
    ):
        raise SystemExit("Opening asset manifest has no positive duration_ms")

    return {
        "asset_id": asset_id,
        "url": audio_url,
        "spoken_text": transcript,
        "duration_ms": duration_ms,
    }


def headless_voice_settings(opening_asset: dict[str, Any]) -> dict[str, Any]:
    """Return voice-demo settings bound to the generated asset manifest."""
    settings = dict(HEADLESS_VOICE_SETTINGS)
    settings.update(
        {
            "clientOpeningAssetId": opening_asset["asset_id"],
            "clientOpeningAudioUrl": opening_asset["url"],
            "clientOpeningTranscript": opening_asset["spoken_text"],
            "clientOpeningDurationMs": opening_asset["duration_ms"],
        }
    )
    return settings


def _unique_domains(extra_domains: Sequence[str]) -> list[str]:
    """Return stable, case-insensitive de-duplicated domain entries."""
    domains: list[str] = []
    seen: set[str] = set()
    for candidate in (*LOCAL_ALLOWED_DOMAINS, *extra_domains):
        domain = candidate.strip()
        normalized = domain.casefold()
        if not domain or normalized in seen:
            continue
        seen.add(normalized)
        domains.append(domain)
    return domains


def _is_dedicated_demo_token(token: Any) -> bool:
    """Return whether a token was explicitly created for this voice demo."""
    settings = getattr(token, "settings", None)
    return isinstance(settings, Mapping) and settings.get("directVoiceDemo") is True


def _select_dedicated_demo_token(tokens: Sequence[Any]) -> Any | None:
    """Prefer an active demo token, then preserve a compatible inactive one."""
    active_demo_token = next(
        (
            token
            for token in tokens
            if getattr(token, "is_active", False) and _is_dedicated_demo_token(token)
        ),
        None,
    )
    if active_demo_token is not None:
        return active_demo_token
    return next((token for token in tokens if _is_dedicated_demo_token(token)), None)


async def provision(args: argparse.Namespace) -> str:
    """Create or update the workflow's reusable embed token."""
    opening_asset = load_opening_asset_manifest(args.opening_manifest)
    settings = headless_voice_settings(opening_asset)
    organization = await db_client.get_organization_by_id(args.organization_id)
    if organization is None:
        raise SystemExit(f"Organization {args.organization_id} not found")

    user = await db_client.get_user_by_id(args.user_id)
    if user is None:
        raise SystemExit(f"User {args.user_id} not found")

    if not await db_client.is_user_member_of_organization(
        args.user_id, args.organization_id
    ):
        raise SystemExit(
            f"User {args.user_id} is not a member of organization "
            f"{args.organization_id}"
        )

    workflow = await db_client.get_workflow(
        args.workflow_id, organization_id=args.organization_id
    )
    if workflow is None:
        raise SystemExit(
            f"Workflow {args.workflow_id} not found in organization "
            f"{args.organization_id}"
        )

    allowed_domains = _unique_domains(args.allowed_domain)
    tokens = await db_client.get_embed_tokens_by_workflow(
        args.workflow_id, args.organization_id, active_only=False
    )
    reusable_token = _select_dedicated_demo_token(tokens)

    if reusable_token is None:
        token = await db_client.create_embed_token(
            workflow_id=args.workflow_id,
            organization_id=args.organization_id,
            created_by=args.user_id,
            allowed_domains=allowed_domains,
            settings=settings,
            usage_limit=None,
            expires_at=None,
        )
    else:
        token = await db_client.update_embed_token(
            reusable_token.id,
            args.organization_id,
            allowed_domains=allowed_domains,
            settings=settings,
            usage_limit=None,
            expires_at=None,
            is_active=True,
        )
        if token is None:
            raise SystemExit("Embed token could not be updated")

    return token.token


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision the Yishui headless voice-demo embed token."
    )
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument(
        "--opening-manifest",
        type=Path,
        default=DEFAULT_OPENING_MANIFEST,
        help="Generated opening-audio manifest used for the client preload settings.",
    )
    parser.add_argument(
        "--allowed-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help=(
            "Additional allowed origin host or host:port. Repeat for multiple "
            "domains; localhost and 127.0.0.1 are always included."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Apply the configuration without printing the public embed token.",
    )
    return parser.parse_args()


def main() -> None:
    # DB client create/update helpers log informational records. Suppress them so
    # successful stdout remains machine-readable and contains only the token.
    logger.remove()
    args = _parse_args()
    token = asyncio.run(provision(args))
    if not args.quiet:
        print(token)


if __name__ == "__main__":
    main()
