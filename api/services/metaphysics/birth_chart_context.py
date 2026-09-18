"""Build auditable birth-calendar facts before the LLM writes an answer.

Calendar conversion and Four-Pillars charting are deterministic operations.
They must not be inferred by the language model or retrieved from FAQ prose.
The resulting text is an internal system message; interpretation remains a
traditional-cultural reference and is deliberately separated from calculation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


BIRTH_FACTS_MARKER = "[YISHUI_INTERNAL_BIRTH_FACTS]"

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_NUMBER_TOKEN = r"[0-9零〇○一二兩两三四五六七八九十]{1,4}"
_FULL_DATE_RE = re.compile(
    rf"(?<![0-9])(?P<year>{_NUMBER_TOKEN})\s*(?:年|[-/.])\s*"
    rf"(?P<month>{_NUMBER_TOKEN})\s*(?:月|[-/.])\s*"
    rf"(?P<day>{_NUMBER_TOKEN})\s*(?:日|號|号)?"
)
_YEAR_RE = re.compile(rf"(?<![0-9])(?P<year>{_NUMBER_TOKEN})\s*年")
_CLOCK_TIME_RE = re.compile(
    rf"(?P<period>凌晨|朝早|早上|中午|下晝|下午|夜晚|晚上)?\s*"
    rf"(?P<hour>{_NUMBER_TOKEN})\s*(?:點|点|時|时|鐘|钟)"
    rf"(?:(?P<half>半)|\s*(?P<minute>{_NUMBER_TOKEN})\s*(?:分)?)?"
)
_EXPLICIT_BIRTH_RE = re.compile(
    r"(?:出生|出世|生於|生于|生日|生辰|八字|四柱|命盤|命盘|"
    r"生肖|屬|属)"
)
_EXPLICIT_BIRTH_YEAR_RE = re.compile(
    rf"(?:出生|出世|生於|生于|生日).{{0,10}}{_NUMBER_TOKEN}\s*年"
    rf"|{_NUMBER_TOKEN}\s*年.{{0,6}}(?:出生|出世|生人)"
)
_DATE_SELECTION_RE = re.compile(
    r"(?:擇日|择日|吉日|結婚|结婚|婚禮|婚礼|搬屋|搬家|入伙)"
)
_TERSE_YEAR_ANSWER_RE = re.compile(
    rf"^(?:我(?:係|是)?\s*)?(?P<year>{_NUMBER_TOKEN})\s*年"
    rf"(?:出世|出生)?[\s。！，,]*$"
)
_TRADITIONAL_TRANSLATION = str.maketrans(
    {"龙": "龍", "马": "馬", "鸡": "雞", "猪": "豬", "腊": "臘"}
)


def _traditional(value: str) -> str:
    return value.translate(_TRADITIONAL_TRANSLATION)


@dataclass(frozen=True)
class BirthDetails:
    year: int
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int = 0

    @property
    def has_date(self) -> bool:
        return self.month is not None and self.day is not None

    @property
    def has_time(self) -> bool:
        return self.hour is not None


def _parse_number(value: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    if "十" in normalized:
        left, _, right = normalized.partition("十")
        tens = 1 if not left else _CN_DIGITS.get(left)
        ones = 0 if not right else _CN_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    digits = [_CN_DIGITS.get(char) for char in normalized]
    if any(digit is None for digit in digits):
        return None
    return int("".join(str(digit) for digit in digits))


def _expand_birth_year(raw_year: int, current_year: int) -> int:
    if raw_year >= 1000:
        return raw_year
    if raw_year < 0 or raw_year > 99:
        return raw_year
    return (2000 if raw_year <= current_year % 100 else 1900) + raw_year


def _parse_clock_time(text: str) -> tuple[int | None, int]:
    match = _CLOCK_TIME_RE.search(text)
    if not match:
        return None, 0
    hour = _parse_number(match.group("hour"))
    if hour is None:
        return None, 0
    minute = (
        30
        if match.group("half")
        else _parse_number(match.group("minute") or "0")
    )
    if minute is None or not 0 <= minute <= 59:
        return None, 0
    period = match.group("period") or ""
    if period == "凌晨" and hour == 12:
        hour = 0
    elif period in {"中午", "下晝", "下午", "夜晚", "晚上"} and hour < 12:
        hour += 12
    if not 0 <= hour <= 23:
        return None, 0
    return hour, minute


def _hong_kong_year() -> int:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).year


def extract_birth_details(
    text: str, *, current_year: int | None = None
) -> BirthDetails | None:
    """Extract the latest stated Gregorian birth details from conversation text.

    A complete date is preferred. A year-only answer is retained but never
    converted to a zodiac because January/February can belong to another lunar
    year. Two-digit years use the current century only up to the current year's
    suffix; later suffixes resolve to the previous century.
    """

    current_year = current_year or _hong_kong_year()
    normalized = unicodedata.normalize("NFKC", text)
    has_birth_context = bool(_EXPLICIT_BIRTH_RE.search(normalized))
    if _DATE_SELECTION_RE.search(normalized) and not has_birth_context:
        return None
    date_matches = list(_FULL_DATE_RE.finditer(normalized))
    if date_matches:
        match = date_matches[-1]
        raw_year = _parse_number(match.group("year"))
        month = _parse_number(match.group("month"))
        day = _parse_number(match.group("day"))
        if raw_year is None or month is None or day is None:
            return None
        year = _expand_birth_year(raw_year, current_year)
        try:
            datetime(year, month, day)
        except ValueError:
            return BirthDetails(year=year, month=month, day=day)
        if year > current_year:
            return None
        trailing_context = normalized[match.end() :]
        hour, minute = _parse_clock_time(trailing_context)
        if hour is None:
            hour, minute = _parse_clock_time(normalized)
        return BirthDetails(year, month, day, hour, minute)

    latest_line = normalized.strip().splitlines()[-1].strip()
    terse_year = _TERSE_YEAR_ANSWER_RE.fullmatch(latest_line)
    if not _EXPLICIT_BIRTH_YEAR_RE.search(normalized) and not terse_year:
        return None
    year_matches = list(_YEAR_RE.finditer(normalized))
    if not year_matches:
        return None
    raw_year = _parse_number(year_matches[-1].group("year"))
    if raw_year is None:
        return None
    return BirthDetails(year=_expand_birth_year(raw_year, current_year))


def _load_solar_class() -> Any:
    # Lazy import keeps ordinary conversation available if a deployment has
    # not yet installed the optional, pinned calendar package.
    from lunar_python import Solar

    return Solar


def calculate_birth_facts(details: BirthDetails) -> dict[str, str]:
    """Return calculation-only facts under an explicit, fixed convention."""

    if not details.has_date:
        return {"status": "year_only", "year": str(details.year)}
    try:
        valid_date = datetime(details.year, details.month or 0, details.day or 0)
    except ValueError:
        return {"status": "invalid_date"}

    # Noon is used only for lunar-date/zodiac conversion when time is absent.
    # A Four-Pillars chart is never emitted from this placeholder time.
    calculation_hour = details.hour if details.hour is not None else 12
    Solar = _load_solar_class()
    solar = Solar.fromYmdHms(
        valid_date.year,
        valid_date.month,
        valid_date.day,
        calculation_hour,
        details.minute,
        0,
    )
    lunar = solar.getLunar()
    result = {
        "status": "complete_date",
        "solar_date": valid_date.date().isoformat(),
        "lunar_date": (
            f"{lunar.getYear()}年"
            f"{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
        ),
        "lunar_year_ganzhi": _traditional(lunar.getYearInGanZhi()),
        "popular_zodiac": _traditional(lunar.getYearShengXiao()),
    }
    if details.has_time:
        eight_char = lunar.getEightChar()
        # Sect 2 keeps the late-Zi-hour day pillar on the civil date. This is
        # explicitly disclosed because schools differ around 23:00.
        eight_char.setSect(2)
        result.update(
            {
                "status": "complete_datetime",
                "solar_time": f"{details.hour:02d}:{details.minute:02d}",
                "bazi_year_ganzhi": _traditional(eight_char.getYear()),
                "bazi_zodiac": _traditional(lunar.getYearShengXiaoExact()),
                "four_pillars": " ".join(
                    (
                        eight_char.getYear(),
                        eight_char.getMonth(),
                        eight_char.getDay(),
                        eight_char.getTime(),
                    )
                ),
            }
        )
    return result


def build_birth_facts_context(
    text: str, *, current_year: int | None = None
) -> str:
    """Format deterministic facts as one replaceable, internal system message."""

    details = extract_birth_details(text, current_year=current_year)
    if details is None:
        return ""
    try:
        facts = calculate_birth_facts(details)
    except (ImportError, ModuleNotFoundError):
        return (
            f"{BIRTH_FACTS_MARKER}\n"
            "曆法規則引擎目前不可用。禁止根據公曆年份猜生肖、年柱或八字；"
            "如果需要精確結果，只可說明目前無法完成經校驗排盤。"
        )

    header = (
        f"{BIRTH_FACTS_MARKER}\n"
        "以下係曆法規則引擎輸出嘅計算事實，優先級高過 FAQ 同模型猜測。"
        "只可用來解釋，絕對唔可改寫、反推或聲稱用戶自相矛盾。\n"
    )
    status = facts.get("status")
    if status == "year_only":
        return (
            header
            + f"目前只知公曆出生年份：{facts['year']}年。"
            "單憑年份無法判定一月至二月出生者嘅農曆生肖，"
            "禁止用年份除以十二直接斷定；如仍在收集資料，應問完整公曆出生日期。"
        )
    if status == "invalid_date":
        return (
            header
            + "用戶說出嘅公曆日期無效或無法組成真實日期。"
            "唔好排盤或猜生肖；如仍在收集資料，只問一次完整公曆出生日期。"
        )

    lines = [
        header.rstrip("\n"),
        f"公曆出生日期：{facts['solar_date']}。",
        f"農曆日期：{facts['lunar_date']}。",
        (
            "日常生肖口徑（按農曆正月初一切換）："
            f"{facts['lunar_year_ganzhi']}年，屬{facts['popular_zodiac']}。"
        ),
    ]
    if status == "complete_date":
        lines.append(
            "用戶尚未提供數字化當地出生時間，禁止輸出完整四柱、"
            "時柱、十神、大運起運歲數或精確日主結論。"
        )
        return "\n".join(lines)

    lines.extend(
        (
            f"用作排盤嘅出生時間：{facts['solar_time']}（當地民用鐘）。",
            (
                "四柱年柱口徑（年、月柱按精確節氣時刻）："
                f"{facts['bazi_year_ganzhi']}，年支屬{facts['bazi_zodiac']}。"
            ),
            f"四柱（年 月 日 時）：{facts['four_pillars']}。",
            (
                "排盤約定：報告嘅當地民用時間視作 UTC+8，年月柱按節氣，"
                "晚子時23:00後日柱仍按當日（Sect 2），未校正真太陽時。"
                "若出生地不在 UTC+8、位於節氣或23:00邊界，或用戶指定其他流派，"
                "必須先說明可能變盤，唔可作精確結論。"
            ),
        )
    )
    if facts["popular_zodiac"] != facts["bazi_zodiac"]:
        lines.append(
            "本例日常農曆生肖同八字年柱年支不同，係春節同立春邊界不同所致，"
            "不代表任何一方資料有錯。回答時必須分開話明兩個口徑。"
        )
    return "\n".join(lines)


def replace_birth_facts_context(
    messages: list[dict[str, Any]], context: str
) -> list[dict[str, Any]]:
    """Replace the previous ephemeral calculation instead of growing history."""

    retained = [
        message
        for message in messages
        if not (
            isinstance(message, dict)
            and message.get("role") == "system"
            and str(message.get("content", "")).startswith(BIRTH_FACTS_MARKER)
        )
    ]
    if context:
        retained.append({"role": "system", "content": context})
    return retained


__all__ = [
    "BIRTH_FACTS_MARKER",
    "BirthDetails",
    "build_birth_facts_context",
    "calculate_birth_facts",
    "extract_birth_details",
    "replace_birth_facts_context",
]
