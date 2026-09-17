#!/usr/bin/env python3
"""Bridge a Qwen3 cloned voice to Qwen-Audio-TTS and synthesize Cantonese.

Qwen3-TTS accepts reference audio inline but its VC model is Mandarin-oriented.
Qwen-Audio-TTS supports Cantonese but voice enrollment requires a public URL.
This script generates a short, temporary Alibaba OSS-hosted reference from the
existing Qwen3 voice, enrolls that URL into Qwen-Audio-TTS, then synthesizes a
Hong Kong Cantonese sample. The API key is never persisted.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clone_dashscope_voice import (
    DEFAULT_API_BASE,
    DashScopeRequestError,
    _download,
    _request_json,
    _synthesize,
)


SOURCE_MODEL = "qwen3-tts-vc-2026-01-22"
TARGET_MODEL = "qwen-audio-3.0-tts-flash"
BRIDGE_TEXT = (
    "你好，我是易水人工智能语音助手。感谢你参加这次声音测试。"
    "接下来我会用自然、平稳、清楚的方式，为你介绍家居布置、空间整理，"
    "以及传统文化方面的参考建议。"
)
INSTRUCTION = "請用自然地道嘅香港廣東話表達，語氣溫和親切，語速稍慢，避免普通話口音。"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-voice", required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="yishuiyue")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    return parser.parse_args()


def _create_qwen_audio_voice(
    *, api_base: str, api_key: str, public_audio_url: str, prefix: str
) -> tuple[str, dict[str, Any]]:
    result = _request_json(
        f"{api_base.rstrip('/')}/services/audio/tts/customization",
        {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": TARGET_MODEL,
                "prefix": prefix,
                "url": public_audio_url,
                "language_hints": ["zh"],
                "max_prompt_audio_length": 20,
                "enable_preprocess": False,
                "enable_volume_normalization": "false",
            },
        },
        api_key,
    )
    try:
        voice_id = result["output"]["voice_id"]
    except (KeyError, TypeError) as exc:
        raise DashScopeRequestError(
            f"Qwen-Audio voice creation returned no output.voice_id: "
            f"{json.dumps(result, ensure_ascii=False)}"
        ) from exc
    return voice_id, result


def _synthesize_cantonese(
    *, api_base: str, api_key: str, voice_id: str, text: str, destination: Path
) -> dict[str, Any]:
    result = _request_json(
        f"{api_base.rstrip('/')}/services/audio/tts/SpeechSynthesizer",
        {
            "model": TARGET_MODEL,
            "input": {
                "text": text,
                "voice": voice_id,
                "format": "wav",
                "sample_rate": 24000,
                "language_hints": ["zh"],
                "instruction": INSTRUCTION,
                "enable_aigc_tag": True,
            },
        },
        api_key,
    )
    try:
        audio_url = result["output"]["audio"]["url"]
    except (KeyError, TypeError) as exc:
        raise DashScopeRequestError(
            f"Cantonese synthesis returned no output.audio.url: "
            f"{json.dumps(result, ensure_ascii=False)}"
        ) from exc
    _download(audio_url, destination)
    return result


def main() -> int:
    args = _parse_args()
    text_path = args.text_file.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not text_path.is_file():
        print(f"Text file not found: {text_path}", file=sys.stderr)
        return 2
    if len(args.prefix) > 10 or not args.prefix.isalnum():
        print("Prefix must be alphanumeric and no longer than 10 characters", file=sys.stderr)
        return 2
    text = "\n".join(line.strip() for line in text_path.read_text(encoding="utf-8").splitlines() if line.strip())
    output_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("DASHSCOPE_API_KEY") or getpass.getpass("DashScope API key: ")
    if not api_key.strip():
        print("DashScope API key is required", file=sys.stderr)
        return 2

    bridge_path = output_dir / "bridge-reference.wav"
    cantonese_path = output_dir / "cantonese-fengshui-qwen-audio.wav"
    try:
        bridge_result = _synthesize(
            api_base=args.api_base,
            api_key=api_key,
            target_model=SOURCE_MODEL,
            voice=args.source_voice,
            text=BRIDGE_TEXT,
            destination=bridge_path,
        )
        bridge_url = bridge_result["output"]["audio"]["url"]
        print(f"Generated bridge reference: {bridge_path}")

        voice_id, enrollment_result = _create_qwen_audio_voice(
            api_base=args.api_base,
            api_key=api_key,
            public_audio_url=bridge_url,
            prefix=args.prefix,
        )
        print(f"Created Qwen-Audio Cantonese voice: {voice_id}")

        synthesis_result = _synthesize_cantonese(
            api_base=args.api_base,
            api_key=api_key,
            voice_id=voice_id,
            text=text,
            destination=cantonese_path,
        )
        print(f"Downloaded Cantonese sample: {cantonese_path}")
    except (DashScopeRequestError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_model": SOURCE_MODEL,
        "source_voice": args.source_voice,
        "target_model": TARGET_MODEL,
        "target_voice": voice_id,
        "instruction": INSTRUCTION,
        "text_file": str(text_path),
        "text": text,
        "bridge_reference": str(bridge_path),
        "output": str(cantonese_path),
        "bridge_request_id": bridge_result.get("request_id"),
        "enrollment_request_id": enrollment_result.get("request_id"),
        "synthesis_request_id": synthesis_result.get("request_id"),
        "usage": synthesis_result.get("usage"),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
