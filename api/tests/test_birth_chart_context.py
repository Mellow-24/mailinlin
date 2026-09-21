"""Regression coverage for deterministic zodiac and Four-Pillars context."""

from datetime import datetime
from zoneinfo import ZoneInfo

from api.services.metaphysics.birth_chart_context import (
    BIRTH_FACTS_MARKER,
    BirthDetails,
    build_birth_facts_context,
    calculate_birth_facts,
    calculate_current_year_facts,
    extract_birth_details,
    extract_stated_zodiac,
    replace_birth_facts_context,
)


HONG_KONG = ZoneInfo("Asia/Hong_Kong")
SEPTEMBER_2026 = datetime(2026, 9, 21, 12, 0, tzinfo=HONG_KONG)


def test_extracts_arabic_and_spoken_chinese_birth_details() -> None:
    assert extract_birth_details(
        "我係2003年2月1號下午3點半出世", current_year=2026
    ) == BirthDetails(2003, 2, 1, 15, 30)
    assert extract_birth_details(
        "我嘅生辰係零零三年二月一日", current_year=2026
    ) == BirthDetails(2003, 2, 1)


def test_year_only_is_not_converted_to_a_zodiac() -> None:
    context = build_birth_facts_context(
        "我係2003年出世", current_year=2026, now=SEPTEMBER_2026
    )

    assert context.startswith(BIRTH_FACTS_MARKER)
    assert "無法判定" in context
    assert "屬羊" not in context


def test_ordinary_zodiac_uses_lunar_new_year_not_gregorian_year() -> None:
    before = calculate_birth_facts(BirthDetails(2003, 1, 31))
    after = calculate_birth_facts(BirthDetails(2003, 2, 1))

    assert before["popular_zodiac"] == "馬"
    assert after["popular_zodiac"] == "羊"


def test_chart_distinguishes_lunar_zodiac_from_li_chun_year_pillar() -> None:
    facts = calculate_birth_facts(BirthDetails(2003, 2, 1, 12, 0))
    context = build_birth_facts_context(
        "我嘅八字資料係2003年2月1日中午12點",
        current_year=2026,
        now=SEPTEMBER_2026,
    )

    assert facts["popular_zodiac"] == "羊"
    assert facts["bazi_zodiac"] == "馬"
    assert facts["four_pillars"] == "壬午 癸丑 乙巳 壬午"
    assert "春節同立春邊界不同" in context


def test_non_birth_dates_do_not_create_birth_context() -> None:
    assert (
        extract_birth_details("我想2026年10月1日結婚擇日", current_year=2026)
        is None
    )
    assert extract_birth_details("我想問2026年運程", current_year=2026) is None
    assert (
        extract_birth_details("我屬羊，想問2026年運程", current_year=2026)
        is None
    )


def test_current_year_is_calculated_and_not_guessed_by_the_model() -> None:
    facts = calculate_current_year_facts(SEPTEMBER_2026)

    assert facts["lunar_year_ganzhi"] == "丙午"
    assert facts["popular_zodiac"] == "馬"
    assert facts["flow_year_ganzhi"] == "丙午"
    assert facts["flow_year_zodiac"] == "馬"


def test_sheep_is_not_labelled_benming_in_2026_horse_year() -> None:
    assert extract_stated_zodiac("我屬羊，想問今年運程") == "羊"
    assert extract_stated_zodiac("我係屬羊嘅") == "羊"

    context = build_birth_facts_context(
        "我屬羊，想問今年運程", now=SEPTEMBER_2026
    )

    assert "當前日常農曆生肖年（正月初一換年）：丙午年，肖馬" in context
    assert "用戶本輪自述生肖：屬羊" in context
    assert "所以唔係本命年" in context
    assert "馬羊" in context


def test_horse_is_labelled_benming_in_2026_horse_year() -> None:
    context = build_birth_facts_context(
        "我屬馬，想問今年運程", now=SEPTEMBER_2026
    )

    assert "用戶本輪自述生肖：屬馬" in context
    assert "所以係本命年" in context


def test_current_context_separates_lunar_new_year_and_li_chun() -> None:
    between_boundaries = datetime(2026, 2, 10, 12, 0, tzinfo=HONG_KONG)
    facts = calculate_current_year_facts(between_boundaries)
    context = build_birth_facts_context("想問今年運程", now=between_boundaries)

    assert facts["popular_zodiac"] == "蛇"
    assert facts["flow_year_zodiac"] == "馬"
    assert "兩個口徑不同" in context


def test_replace_birth_context_removes_the_previous_calculation() -> None:
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "system", "content": f"{BIRTH_FACTS_MARKER}\nold"},
        {"role": "user", "content": "question"},
    ]

    updated = replace_birth_facts_context(
        messages, f"{BIRTH_FACTS_MARKER}\nnew"
    )

    assert updated == [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "question"},
        {"role": "system", "content": f"{BIRTH_FACTS_MARKER}\nnew"},
    ]
