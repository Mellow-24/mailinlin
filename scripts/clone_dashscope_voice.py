#!/usr/bin/env python3
"""Create a DashScope Qwen3-TTS cloned voice and download test samples.

The API key is read with ``getpass`` unless ``DASHSCOPE_API_KEY`` is already
present in the process environment. It is never written to disk.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_TARGET_MODEL = "qwen3-tts-vc-2026-01-22"
DEFAULT_SAMPLES = {
    "mandarin": "你好，我是易水AI语音助手。这是一段用你提供的音频复刻生成的普通话测试声音。",
    "cantonese": "你好，我係易水AI語音助手。呢段係用你提供嘅音頻復刻生成嘅廣東話測試聲音。",
}


class DashScopeRequestError(RuntimeError):
    """A DashScope request failed."""


def _request_json(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Dograh-Voice-Clone/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=240) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DashScopeRequestError(f"DashScope HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise DashScopeRequestError(f"DashScope network error: {exc.reason}") from exc


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "Dograh-Voice-Clone/1.0"})
    try:
        with urlopen(request, timeout=240) as response:
            destination.write_bytes(response.read())
    except (HTTPError, URLError) as exc:
        raise DashScopeRequestError(f"Unable to download generated audio: {exc}") from exc


def _audio_data_url(audio_path: Path) -> str:
    mime_by_suffix = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }
    try:
        mime_type = mime_by_suffix[audio_path.suffix.lower()]
    except KeyError as exc:
        raise ValueError("Reference audio must be WAV, MP3, or M4A") from exc
    encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _create_voice(
    *,
    api_base: str,
    api_key: str,
    audio_path: Path,
    target_model: str,
    preferred_name: str,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": preferred_name,
            "audio": {"data": _audio_data_url(audio_path)},
            "language": "zh",
        },
    }
    result = _request_json(
        f"{api_base.rstrip('/')}/services/audio/tts/customization",
        payload,
        api_key,
    )
    try:
        voice = result["output"]["voice"]
    except (KeyError, TypeError) as exc:
        raise DashScopeRequestError(
            f"Voice creation returned no output.voice: {json.dumps(result, ensure_ascii=False)}"
        ) from exc
    return voice, result


def _synthesize(
    *,
    api_base: str,
    api_key: str,
    target_model: str,
    voice: str,
    text: str,
    destination: Path,
) -> dict[str, Any]:
    payload = {
        "model": target_model,
        "input": {
            "text": text,
            "voice": voice,
            "language_type": "Chinese",
        },
    }
    result = _request_json(
        f"{api_base.rstrip('/')}/services/aigc/multimodal-generation/generation",
        payload,
        api_key,
    )
    try:
        audio = result["output"]["audio"]
    except (KeyError, TypeError) as exc:
        raise DashScopeRequestError(
            f"Synthesis returned no output.audio: {json.dumps(result, ensure_ascii=False)}"
        ) from exc

    if audio.get("data"):
        destination.write_bytes(base64.b64decode(audio["data"]))
    elif audio.get("url"):
        _download(audio["url"], destination)
    else:
        raise DashScopeRequestError(
            f"Synthesis returned neither audio.data nor audio.url: {json.dumps(result, ensure_ascii=False)}"
        )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preferred-name", default="yishui")
    parser.add_argument("--target-model", default=DEFAULT_TARGET_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    audio_path = args.audio.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not audio_path.is_file():
        print(f"Reference audio not found: {audio_path}", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("DASHSCOPE_API_KEY") or getpass.getpass("DashScope API key: ")
    if not api_key.strip():
        print("DashScope API key is required", file=sys.stderr)
        return 2

    try:
        voice, clone_result = _create_voice(
            api_base=args.api_base,
            api_key=api_key,
            audio_path=audio_path,
            target_model=args.target_model,
            preferred_name=args.preferred_name,
        )
        print(f"Created voice: {voice}")

        samples: list[dict[str, Any]] = []
        for label, sample_text in DEFAULT_SAMPLES.items():
            destination = output_dir / f"sample-{label}.wav"
            synthesis_result = _synthesize(
                api_base=args.api_base,
                api_key=api_key,
                target_model=args.target_model,
                voice=voice,
                text=sample_text,
                destination=destination,
            )
            print(f"Downloaded {label} sample: {destination}")
            samples.append(
                {
                    "label": label,
                    "text": sample_text,
                    "path": str(destination),
                    "request_id": synthesis_result.get("request_id"),
                    "usage": synthesis_result.get("usage"),
                }
            )

        metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reference_audio": str(audio_path),
            "target_model": args.target_model,
            "voice": voice,
            "clone_request_id": clone_result.get("request_id"),
            "fallback_mode": clone_result.get("output", {}).get("fallback_mode"),
            "samples": samples,
        }
        metadata_path = output_dir / "result.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Saved metadata: {metadata_path}")
        return 0
    except (DashScopeRequestError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
