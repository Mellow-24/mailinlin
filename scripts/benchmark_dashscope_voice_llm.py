#!/usr/bin/env python3
"""Measure first-token latency for candidate DashScope voice-chat models."""

from __future__ import annotations

import argparse
import asyncio
import time

from openai import AsyncOpenAI

from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)


DEFAULT_MODELS = ("qwen3.7-flash", "qwen-flash", "qwen-turbo")
SYNTHETIC_SYSTEM_PROMPT = (
    "你是一个低延迟的香港粤语客户服务助手。回答必须简短、自然、清晰，"
    "先回应重点，再提出最多一个澄清问题。不要输出列表、网址或符号。"
    + "遵循客户服务规范并保持回答简短清晰。" * 100
)


async def _measure(client: AsyncOpenAI, model: str, messages: list[dict]) -> None:
    started = time.perf_counter()
    first_event_s: float | None = None
    first_content_s: float | None = None
    completion_tokens = 0

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=96,
            stream=True,
            extra_body={"enable_thinking": False},
        )
        async for chunk in stream:
            now = time.perf_counter()
            if first_event_s is None:
                first_event_s = now - started
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and (delta.content or delta.tool_calls) and first_content_s is None:
                first_content_s = now - started
            usage = getattr(chunk, "usage", None)
            if usage and usage.completion_tokens:
                completion_tokens = usage.completion_tokens
        total_s = time.perf_counter() - started
        print(
            f"{model}: first_event={first_event_s or 0:.3f}s "
            f"first_content={first_content_s or 0:.3f}s total={total_s:.3f}s "
            f"completion_tokens={completion_tokens}"
        )
    except Exception as exc:
        print(f"{model}: ERROR {type(exc).__name__}: {exc}")


async def run(organization_id: int, models: list[str]) -> None:
    resolved = await get_resolved_ai_model_configuration(organization_id=organization_id)
    llm = resolved.effective.llm
    messages = [
        {"role": "system", "content": SYNTHETIC_SYSTEM_PROMPT},
        {"role": "assistant", "content": "你好，我係易水 AI 顧問。今日想問咩呢？"},
        {"role": "user", "content": "我想睇下睡房應該點樣擺。"},
    ]

    client = AsyncOpenAI(
        api_key=llm.api_key,
        base_url=llm.base_url,
        max_retries=0,
        timeout=30.0,
    )
    try:
        for model in models:
            await _measure(client, model, messages)
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, default=1)
    parser.add_argument("--model", action="append", dest="models")
    args = parser.parse_args()
    asyncio.run(
        run(
            organization_id=args.organization_id,
            models=args.models or list(DEFAULT_MODELS),
        )
    )


if __name__ == "__main__":
    main()
