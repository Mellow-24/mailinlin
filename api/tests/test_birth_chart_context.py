"""Regression coverage for deterministic zodiac and Four-Pillars context."""

from api.services.metaphysics.birth_chart_context import (
    BIRTH_FACTS_MARKER,
    BirthDetails,
    build_birth_facts_context,
    calculate_birth_facts,
    extract_birth_details,
    replace_birth_facts_context,
)


def test_extracts_arabic_and_spoken_chinese_birth_details() -> None:
    assert extract_birth_details(
        "我係2003年2月1號下午3點半出世", current_year=2026
    ) == BirthDetails(2003, 2, 1, 15, 30)
    assert extract_birth_details(
        "我嘅生辰係零零三年二月一日", current_year=2026
    ) == BirthDetails(2003, 2, 1)


def test_year_only_is_not_converted_to_a_zodiac() -> None:
    context = build_birth_facts_context("我係2003年出世", current_year=2026)

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
        "我嘅八字資料係2003年2月1日中午12點", current_year=2026
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
