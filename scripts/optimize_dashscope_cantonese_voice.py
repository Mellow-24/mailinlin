#!/usr/bin/env python3
"""Directly enroll original audio and generate Cantonese naturalness A/B samples."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dashscope.utils.oss_utils import OssUtils

from clone_dashscope_voice import (
    DEFAULT_API_BASE,
    DashScopeRequestError,
    _download,
    _request_json,
)


INSTRUCTION = "請用自然香港廣東話，保留參考音頻嘅語氣、停頓同語速，句尾放鬆，避免普通話口音。"
CANDIDATES = (
    ("qwen-audio-3.0-tts-flash", "yueflash", "qwen-audio-flash.wav"),
    ("qwen-audio-3.0-tts-plus", "yueplus", "qwen-audio-plus.wav"),
    ("cosyvoice-v3.5-plus", "yuecosy", "cosyvoice-v3.5-plus.wav"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument(
        "--only-model",
        choices=[model for model, _, _ in CANDIDATES],
        help="Generate only one candidate model instead of the full A/B set.",
    )
    parser.add_argument(
        "--free-prosody",
        action="store_true",
        help="Let the model infer pace, pitch, and volume from the reference.",
    )
    return parser.parse_args()


def _upload_reference(audio_path: Path, api_key: str) -> tuple[str, str]:
    oss_uri, certificate = OssUtils.upload(
        # DashScope temporary resources are model-bound. Enrollment requests
        # use the voice-enrollment model even though target_model is a TTS model.
        model="voice-enrollment",
        file_path=str(audio_path),
        api_key=api_key,
    )
    del certificate
    return oss_uri, "same-account-temporary-oss"


def _request_json_with_oss_resolution(
    url: str, payload: dict[str, Any], api_key: str
) -> dict[str, Any]:
    """Send an SDK temporary oss:// resource to a same-account model call."""
    response = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-OssResourceResolve": "enable",
            "User-Agent": "Dograh-Voice-Clone/1.0",
        },
        timeout=240,
    )
    if response.status_code != 200:
        raise DashScopeRequestError(
            f"DashScope HTTP {response.status_code}: {response.text}"
        )
    return response.json()


def _enroll_voice(
    *, api_base: str, api_key: str, model: str, prefix: str, reference_url: str
) -> tuple[str, dict[str, Any]]:
    result = _request_json_with_oss_resolution(
        f"{api_base.rstrip('/')}/services/audio/tts/customization",
        {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": model,
                "prefix": prefix,
                "url": reference_url,
                "language_hints": ["zh"],
                "max_prompt_audio_length": 30,
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
            f"Voice enrollment for {model} returned no output.voice_id: "
            f"{json.dumps(result, ensure_ascii=False)}"
        ) from exc
    return voice_id, result


def _synthesize(
    *,
    api_base: str,
    api_key: str,
    model: str,
    voice_id: str,
    text: str,
    destination: Path,
    free_prosody: bool,
) -> dict[str, Any]:
    input_payload: dict[str, Any] = {
        "text": text,
        "voice": voice_id,
        "format": "wav",
        "sample_rate": 24000,
        "seed": 42,
        "language_hints": ["zh"],
        "instruction": INSTRUCTION,
    }
    if not free_prosody:
        input_payload.update({"rate": 0.95, "pitch": 1.0, "volume": 50})
    if model.startswith("qwen-audio-"):
        input_payload["enable_aigc_tag"] = True
    result = _request_json(
        f"{api_base.rstrip('/')}/services/audio/tts/SpeechSynthesizer",
        {"model": model, "input": input_payload},
        api_key,
    )
    try:
        audio_url = result["output"]["audio"]["url"]
    except (KeyError, TypeError) as exc:
        raise DashScopeRequestError(
            f"Synthesis for {model} returned no output.audio.url: "
            f"{json.dumps(result, ensure_ascii=False)}"
        ) from exc
    _download(audio_url, destination)
    return result


def main() -> int:
    args = _parse_args()
    audio_path = args.audio.expanduser().resolve()
    text_path = args.text_file.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not audio_path.is_file() or not text_path.is_file():
        print("Audio and text files must exist", file=sys.stderr)
        return 2
    text = "\n".join(
        line.strip()
        for line in text_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.getenv("DASHSCOPE_API_KEY") or getpass.getpass("DashScope API key: ")
    if not api_key.strip():
        print("DashScope API key is required", file=sys.stderr)
        return 2

    try:
        reference_url, upload_mode = _upload_reference(audio_path, api_key)
        print(f"Uploaded original reference through DashScope OSS ({upload_mode})")
        outputs: list[dict[str, Any]] = []
        candidates = (
            tuple(item for item in CANDIDATES if item[0] == args.only_model)
            if args.only_model
            else CANDIDATES
        )
        for model, prefix, filename in candidates:
            voice_id, enrollment = _enroll_voice(
                api_base=args.api_base,
                api_key=api_key,
                model=model,
                prefix=prefix,
                reference_url=reference_url,
            )
            print(f"Created {model} voice: {voice_id}")
            destination = output_dir / filename
            synthesis = _synthesize(
                api_base=args.api_base,
                api_key=api_key,
                model=model,
                voice_id=voice_id,
                text=text,
                destination=destination,
                free_prosody=args.free_prosody,
            )
            print(f"Downloaded {model}: {destination}")
            outputs.append(
                {
                    "model": model,
                    "voice": voice_id,
                    "path": str(destination),
                    "enrollment_request_id": enrollment.get("request_id"),
                    "synthesis_request_id": synthesis.get("request_id"),
                    "usage": synthesis.get("usage"),
                }
            )
    except Exception as exc:
        # OssUtils raises SDK-specific exceptions; keep the message while never
        # serializing the upload certificate or API key.
        print(str(exc), file=sys.stderr)
        return 1

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reference_audio": str(audio_path),
        "reference_uploaded_directly": True,
        "upload_mode": upload_mode,
        "text_file": str(text_path),
        "text": text,
        "instruction": INSTRUCTION,
        "free_prosody": args.free_prosody,
        "outputs": outputs,
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
