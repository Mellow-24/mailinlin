"""Shared workflow-run context rules and trusted demo time context."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY

FAST_OPENING_CONTEXT_KEY = "fast_opening"
CLIENT_OPENING_CONTEXT_KEY = "client_opening"
CLIENT_OPENING_TRANSCRIPT_CONTEXT_KEY = "client_opening_transcript"
DIRECT_VOICE_DEMO_CONTEXT_KEY = "direct_voice_demo"
GREETING_OVERRIDE_CONTEXT_KEY = "greeting_override"
VOICE_DEMO_DATE_MARKER = "[YISHUI_INTERNAL_CURRENT_DATE]"
VOICE_DEMO_TIME_POLICY = (
    "時間基準——高優先級：以系統每輪提供嘅香港時區當前公曆日期為準，"
    "唔可以用訓練資料、舊對話或之前答錯嘅年份當成今年。"
    "用戶明確問其他年份，就按該年回答，唔好擅自改動出生年份或歷史事件年份。"
    "目前演示 FAQ 係 2026 年版本，當中冇另列年份嘅流年內容同『今年』指 2026 年，"
    "唔可以當成其他年份嘅預測，亦唔可以聲稱資料已自動更新。"
    "公曆年份同農曆、立春換年分界要分清；冇曆法依據就唔好編造精確分界。"
)


def build_voice_demo_date_instruction(now: datetime | None = None) -> str:
    """Build a fresh, server-owned clock reference without a network request."""
    hong_kong = ZoneInfo("Asia/Hong_Kong")
    if now is not None and now.utcoffset() is None:
        raise ValueError("Demo clock must be timezone-aware")
    today = (now or datetime.now(hong_kong)).astimezone(hong_kong).date()
    return (
        f"{VOICE_DEMO_DATE_MARKER}\n"
        f"香港時區（Asia/Hong_Kong）當前公曆日期：{today.isoformat()}。"
        f"『今年』係 {today.year} 年；『舊年／去年』係 {today.year - 1} 年；"
        f"『下年／明年』係 {today.year + 1} 年。\n"
        f"{VOICE_DEMO_TIME_POLICY}\n"
        "只喺問題涉及時間時自然使用日期，唔好每次報日期或讀出內部標記。"
    )


# These values describe or authorize the run itself. External context may add
# prompt variables, but it must never supply or replace run-owned metadata.
RESERVED_INITIAL_CONTEXT_KEYS = frozenset(
    {
        "provider",
        "runtime_configuration",
        FAST_OPENING_CONTEXT_KEY,
        CLIENT_OPENING_CONTEXT_KEY,
        CLIENT_OPENING_TRANSCRIPT_CONTEXT_KEY,
        DIRECT_VOICE_DEMO_CONTEXT_KEY,
        MPS_CORRELATION_ID_CONTEXT_KEY,
    }
)


def merge_external_initial_context(
    initial_context: Mapping[str, Any] | None,
    external_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge external variables without accepting reserved run-owned keys."""
    merged = dict(initial_context or {})
    if not external_context:
        return merged

    merged.update(
        {
            key: value
            for key, value in external_context.items()
            if key not in RESERVED_INITIAL_CONTEXT_KEYS
        }
    )
    return merged
