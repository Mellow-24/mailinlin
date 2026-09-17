#!/usr/bin/env python3
"""Synthesize a JSON list of scripts with an existing DashScope cloned voice."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clone_dashscope_voice import (
    DEFAULT_API_BASE,
    DEFAULT_TARGET_MODEL,
    DashScopeRequestError,
    _synthesize,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", required=True)
    parser.add_argument("--scripts", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-model", default=DEFAULT_TARGET_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    return parser.parse_args()


def _load_scripts(path: Path) -> list[dict[str, str]]:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Scripts JSON must be a non-empty list")
    scripts: list[dict[str, str]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Script {index} must be an object")
        label = str(item.get("label", "")).strip()
        text = str(item.get("text", "")).strip()
        title = str(item.get("title", label)).strip()
        if not label or not text:
            raise ValueError(f"Script {index} requires label and text")
        if not re.fullmatch(r"[a-z0-9-]+", label):
            raise ValueError(f"Script label must use lowercase ASCII, digits, or hyphens: {label}")
        scripts.append({"label": label, "title": title, "text": text})
    return scripts


def main() -> int:
    args = _parse_args()
    scripts_path = args.scripts.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not scripts_path.is_file():
        print(f"Scripts file not found: {scripts_path}", file=sys.stderr)
        return 2

    try:
        scripts = _load_scripts(scripts_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.getenv("DASHSCOPE_API_KEY") or getpass.getpass("DashScope API key: ")
    if not api_key.strip():
        print("DashScope API key is required", file=sys.stderr)
        return 2

    generated: list[dict[str, Any]] = []
    try:
        for script in scripts:
            destination = output_dir / f"{script['label']}.wav"
            result = _synthesize(
                api_base=args.api_base,
                api_key=api_key,
                target_model=args.target_model,
                voice=args.voice,
                text=script["text"],
                destination=destination,
            )
            generated.append(
                {
                    **script,
                    "path": str(destination),
                    "request_id": result.get("request_id"),
                    "usage": result.get("usage"),
                }
            )
            print(f"Downloaded {script['label']}: {destination}")
    except DashScopeRequestError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_model": args.target_model,
        "voice": args.voice,
        "scripts_file": str(scripts_path),
        "samples": generated,
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
