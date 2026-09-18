"""Deterministic response guard for the short intake phase of a consultation."""

from __future__ import annotations

import re
from collections.abc import Callable

from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


_SENTENCE_RE = re.compile(r"[^。！？!?.]+[。！？!?.]?")
_TERMINATED_SENTENCE_RE = re.compile(r"[^。！？!?.]+[。！？!?.]")
_MARKDOWN_RE = re.compile(r"(?:\*\*|__|`|^\s*#{1,6}\s*)", re.MULTILINE)
_LIST_PREFIX_RE = re.compile(r"^\s*(?:\d+[.)、]|[-•])\s*", re.MULTILINE)
_ENDING_INTENT_PATTERNS = (
    re.compile(
        r"^(?:我)?(?:(?:唔想|不想)(?:再)?|(?:拒絕|拒绝))"
        r"(?:(?:繼續|继续)(?:傾|講|讲|聊)?|(?:傾|講|讲|聊))"
        r"(?:啦|喇|啊|呀)?(?:[，, ]*(?:多謝|谢谢|thanks))?$"
    ),
    re.compile(
        r"^(?:(?:好|請|请)[，, ]*)?(?:停止|結束|结束)"
        r"(?:(?:本次|這次|这次)?(?:諮詢|咨询))?"
        r"(?:吧|啦|喇|啊|呀)?(?:[，, ]*(?:多謝|谢谢|thanks))?$"
    ),
    re.compile(
        r"^(?:(?:好|多謝|谢谢|thanks)[，, ]*)?"
        r"(?:再見|再见|拜拜|收線|收线|bye|goodbye)(?:啦|喇|啊|呀)?$"
    ),
    re.compile(
        r"^(?:我)?(?:冇|無|没有|沒有)其他"
        r"(?:問題|问题|嘢問|野问|想問|想问)"
        r"(?:啦|喇|啊|呀)?(?:[，, ]*(?:多謝|谢谢|thanks))?$"
    ),
    re.compile(
        r"^(?:唔使|不用|不需要)(?:再)?(?:傾|講|讲|聊)?"
        r"(?:啦|喇|啊|呀|了)?(?:[，, ]*(?:多謝|谢谢|thanks))?$"
    ),
    re.compile(r"^(?:打錯電話|打错电话)(?:啦|喇|啊|呀)?$"),
    re.compile(r"^(?:stop|bye|goodbye|no more questions)$"),
)
_UNPUNCTUATED_QUESTION_CUE_RE = re.compile(
    r"(?:係咪|是否|是不是|有冇|有没有|想唔想|可唔可以|可不可以|"
    r"邊個|边个|邊度|边度|幾多|几多|"
    r"乜嘢|什么|咩|嗎|吗)"
)
_DIRECT_UNPUNCTUATED_QUESTION_START_RE = re.compile(
    r"^(?:"
    r"(?:你|妳|您)(?:想|會|会|可以|可|係|是|有|要|能|願意|愿意)"
    r"|(?:請問|请问|想問|想问|係咪|是否|是不是|有冇|有没有|想唔想|"
    r"可唔可以|可不可以|邊個|边个|邊度|边度|幾多|几多|乜嘢|什么|咩)"
    r")"
)
_DIRECT_HOW_QUESTION_RE = re.compile(
    r"^(?:"
    r"(?:點樣|点样|怎樣|怎样|如何)"
    r"|(?:請問|请问|想問|想问)(?:點樣|点样|怎樣|怎样|如何)"
    r"|(?:你|妳|您)(?:想|會|会|要|能|可以|可|願意|愿意)"
    r"(?:點樣|点样|怎樣|怎样|如何)"
    r")"
)
_DIRECT_UNPUNCTUATED_REQUEST_RE = re.compile(
    r"^(?:你|妳|您)(?:可以|可|能|願意|愿意)(?:先|再)?"
    r"(?:講|讲|說|说|告訴|告诉|分享|補充|补充|描述|介紹|介绍|提供)"
)
_FENG_SHUI_TOPIC_RE = re.compile(
    r"(?:風水|风水|家宅|住宅|屋企|間屋|间屋|大門|大门|坐向|方位|"
    r"睡房|客廳|客厅|廚房|厨房|書房|书房|玄關|玄关|床頭|床头)"
)
_BAZI_TOPIC_RE = re.compile(r"(?:八字|生辰|四柱|命盤|命盘|出生年月日)")
_DATE_SELECTION_TOPIC_RE = re.compile(
    r"(?:擇日|择日|通勝|通胜|吉日|結婚日|结婚日|開業日|开业日|搬屋日|搬家日)"
)
_NAME_TOPIC_RE = re.compile(r"(?:改名|起名|姓名|名學|名学)")
_NUMBER_TOPIC_RE = re.compile(r"(?:號碼|号码|車牌|车牌|電話號|电话号码|手機號|手机号)")
_BIRTH_NUMBER = (
    r"(?:\d{1,4}|[零〇一二兩两三四五六七八九十]{1,4})"
)
_BIRTH_DATE_RE = re.compile(
    rf"(?:{_BIRTH_NUMBER}\s*[年/.-]\s*{_BIRTH_NUMBER}\s*[月/.-]\s*"
    rf"{_BIRTH_NUMBER}\s*(?:日|號|号)?"
    r"|出生年月日|出生日|生日係|生日是)"
)
_BIRTH_HOUR_RE = re.compile(
    r"(?:(?:子|丑|寅|卯|辰|巳|午|未|申|酉|戌|亥)時|"
    rf"(?:凌晨|朝早|早上|中午|下晝|下午|夜晚|晚上)?\s*{_BIRTH_NUMBER}"
    r"\s*(?:點|点|時|时|鐘|钟)|時辰|时辰)"
)
_DIRECTION_RE = re.compile(
    r"(?:坐\s*[東东南西北]|向\s*[東东南西北]|"
    r"(?:正|東|东|西|南|北|東南|东南|西南|東北|东北|西北)\s*(?:方|向))"
)
_FAQ_ANSWER_RE = re.compile(r"粤语参考回答：\s*([^\n]+)")
_GRATITUDE_RE = re.compile(
    r"(?:多謝|多谢|謝謝|谢谢|唔該|唔该|thanks?|thank\s+you)", re.IGNORECASE
)
_CONTINUING_AFTER_GRATITUDE_RE = re.compile(
    r"(?:但|不過|不过|仲想|還想|还想|另外|順便|顺便|想問|想问)"
)
_GREETING_RE = re.compile(
    r"(?:你好|您好|哈囉|哈啰|哈喽|hello|hi|嗨|早晨|早安|早上好|午安|晚上好)",
    re.IGNORECASE,
)


def _clean_spoken_text(text: str) -> str:
    text = _MARKDOWN_RE.sub("", text)
    text = _LIST_PREFIX_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _bounded_declarations(sentences: list[str], limit: int = 150) -> str:
    selected: list[str] = []
    length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if length + len(sentence) > limit:
            if not selected:
                selected.append(sentence[: limit - 1].rstrip("，,;；:：") + "。")
            break
        selected.append(sentence)
        length += len(sentence)
        if len(selected) == 2:
            break
    return "".join(selected)


def _normalized_question(text: str) -> str:
    cleaned = _clean_spoken_text(text)
    if not cleaned:
        return ""
    if cleaned.endswith(("？", "?")):
        return cleaned
    is_direct_question = (
        (
            _DIRECT_UNPUNCTUATED_QUESTION_START_RE.search(cleaned)
            and _UNPUNCTUATED_QUESTION_CUE_RE.search(cleaned)
        )
        or _DIRECT_HOW_QUESTION_RE.search(cleaned)
        or _DIRECT_UNPUNCTUATED_REQUEST_RE.search(cleaned)
    )
    if not is_direct_question:
        return ""
    return cleaned.rstrip("。！？!?.，,;；:：") + "？"


def _last_intake_question(sentences: list[str]) -> str:
    questions = [_normalized_question(sentence) for sentence in sentences]
    return next((question for question in reversed(questions) if question), "")


def _completed_sentences(text: str) -> tuple[list[str], str]:
    matches = list(_TERMINATED_SENTENCE_RE.finditer(text))
    if not matches:
        return [], text
    return [match.group() for match in matches], text[matches[-1].end() :]


def _has_ending_intent(user_text: str) -> bool:
    normalized = _clean_spoken_text(user_text).lower().strip("。！？!?.，, ")
    return any(pattern.fullmatch(normalized) for pattern in _ENDING_INTENT_PATTERNS)


def _has_closing_intent(user_text: str) -> bool:
    """Recognize explicit endings and short standalone thanks at any turn."""

    cleaned = _clean_spoken_text(user_text)
    if _has_ending_intent(cleaned):
        return True
    return bool(
        len(cleaned) <= 40
        and _GRATITUDE_RE.search(cleaned)
        and not _CONTINUING_AFTER_GRATITUDE_RE.search(cleaned)
        and "？" not in cleaned
        and "?" not in cleaned
    )


def _consultation_topic(consultation_text: str) -> str:
    has_feng_shui = bool(_FENG_SHUI_TOPIC_RE.search(consultation_text))
    has_bazi = bool(_BAZI_TOPIC_RE.search(consultation_text))
    if has_feng_shui and has_bazi:
        return "feng_shui_with_bazi"
    if has_feng_shui:
        return "feng_shui"
    if _DATE_SELECTION_TOPIC_RE.search(consultation_text):
        return "date_selection"
    if _BAZI_TOPIC_RE.search(consultation_text):
        return "bazi"
    if _NAME_TOPIC_RE.search(consultation_text):
        return "name"
    if _NUMBER_TOPIC_RE.search(consultation_text):
        return "number"
    return "fortune"


def select_intake_question(turn: int, consultation_text: str) -> str:
    """Choose one useful intake fact from the FAQ's main subject areas."""

    text = _clean_spoken_text(consultation_text)
    topic = _consultation_topic(text)

    if topic == "feng_shui":
        if not _DIRECTION_RE.search(text):
            return "你間屋大門大概向邊個方位？"
        return "你今次最想改善家宅邊一方面？"

    if topic == "feng_shui_with_bazi":
        if not _DIRECTION_RE.search(text):
            return "你間屋大門大概向邊個方位？"
        if not _BIRTH_DATE_RE.search(text) or not _BIRTH_HOUR_RE.search(text):
            return "最後請講一組完整出生資料：公曆日期、當地時間？"
        return "你今次最想改善家宅邊一方面？"

    if topic == "date_selection":
        if turn <= 1:
            return "你今次想為咩事情擇日？"
        return "你心目中大概係邊段日期？"

    if topic == "bazi":
        if not _BIRTH_DATE_RE.search(text):
            return "你嘅完整公曆出生日期係點？"
        if not _BIRTH_HOUR_RE.search(text):
            return "你當地出生時間大概係幾點？"
        return "你今次最想集中睇邊一方面？"

    if topic == "name":
        return "今次係為邊位改名？"

    if topic == "number":
        return "你想睇邊一組號碼？"

    if not _BIRTH_DATE_RE.search(text):
        return "你嘅完整公曆出生日期係點？"
    return "你今次最想集中睇邊一方面？"


def _faq_declarations(reference: str) -> str:
    answers = [_clean_spoken_text(value) for value in _FAQ_ANSWER_RE.findall(reference)]
    sentences: list[str] = []
    for answer in answers:
        sentences.extend(
            sentence.strip()
            for sentence in _SENTENCE_RE.findall(answer)
            if sentence.strip() and not _normalized_question(sentence)
        )
    return _bounded_declarations(sentences, limit=220)


def build_consultation_fallback_reply(
    turn: int,
    consultation_text: str,
    faq_reference: str = "",
) -> str:
    """Produce an immediate no-network reply when the sole LLM call times out."""

    latest_user_text = consultation_text.split("\n")[-1].strip()
    if _has_closing_intent(latest_user_text):
        return (
            "唔使客氣，多謝你今日同我分享。"
            "今次內容只作傳統文化同一般生活參考，祝你一切順利。"
        )

    analysis = _faq_declarations(faq_reference)
    if not analysis:
        analysis = (
            "我會先按你而家講嘅內容作傳統文化參考，"
            "重點合參已知嘅生肖、生辰同生活近況。"
        )
    analysis = analysis.replace("？", "。").replace("?", "。")
    if turn <= 2 and not _has_closing_intent(latest_user_text):
        return f"{analysis}{select_intake_question(turn, consultation_text)}"
    return (
        f"{analysis}綜合現有資料，宜先用安全、低成本同可逆嘅方法逐步調整，"
        "再按實際效果修正；以上只作傳統文化同一般生活參考。"
    )


def build_conversation_fallback_reply(user_text: str) -> str:
    """Return a neutral fallback that never invents a metaphysics reading."""

    cleaned = _clean_spoken_text(user_text)
    if _has_closing_intent(cleaned):
        return "唔使客氣，好高興同你傾偈，祝你今日一切順利。"
    if _GREETING_RE.search(cleaned):
        return "你好呀，好高興同你傾偈。今日有咩想了解？"
    return "明白，我會按你而家想了解嘅內容直接答你，唔會夾硬解讀成風水命理。"


def sanitize_intake_reply(raw_text: str, latest_user_text: str = "") -> str:
    """Keep useful analysis and exactly the final question from an LLM reply."""

    del latest_user_text
    text = _clean_spoken_text(raw_text)
    sentences = [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()]
    declarations = [
        sentence for sentence in sentences if not _normalized_question(sentence)
    ]
    analysis = _bounded_declarations(declarations)
    question = _last_intake_question(sentences)

    result = "".join(part for part in (analysis, question) if part)
    return result or text


class ConsultationResponseGuardProcessor(FrameProcessor):
    """Stream intake declarations while enforcing one final question."""

    def __init__(
        self,
        *,
        turn_provider: Callable[[], int],
        user_text_provider: Callable[[], str],
        consultation_text_provider: Callable[[], str] | None = None,
        faq_reference_provider: Callable[[], str] | None = None,
        consultation_response_provider: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self._turn_provider = turn_provider
        self._user_text_provider = user_text_provider
        self._consultation_text_provider = (
            consultation_text_provider or user_text_provider
        )
        self._faq_reference_provider = faq_reference_provider or (lambda: "")
        self._consultation_response_provider = (
            consultation_response_provider or (lambda: True)
        )
        self._guard_response = False
        self._is_consultation_response = False
        self._guard_turn = 0
        self._text_parts: list[str] = []
        self._pending_text = ""
        self._declarations_forwarded = 0
        self._latest_user_text = ""
        self._consultation_text = ""
        self._faq_reference = ""
        self._question_emitted_turn: int | None = None
        self._text_direction = FrameDirection.DOWNSTREAM
        self._response_skip_tts: bool | None = None
        self._function_calls_started = False
        self._response_active = False
        self._response_had_output = False

    def _reset(self) -> None:
        self._guard_response = False
        self._is_consultation_response = False
        self._guard_turn = 0
        self._text_parts = []
        self._pending_text = ""
        self._declarations_forwarded = 0
        self._latest_user_text = ""
        self._consultation_text = ""
        self._faq_reference = ""
        self._text_direction = FrameDirection.DOWNSTREAM
        self._response_skip_tts = None
        self._function_calls_started = False
        self._response_active = False
        self._response_had_output = False

    def _generated_text_frame(self, text: str) -> LLMTextFrame:
        frame = LLMTextFrame(text)
        frame.skip_tts = self._response_skip_tts
        return frame

    async def _emit_declaration(self, text: str) -> None:
        cleaned = _clean_spoken_text(text)
        if (
            not cleaned
            or _normalized_question(cleaned)
            or self._declarations_forwarded >= 2
        ):
            return
        await self.push_frame(self._generated_text_frame(cleaned), self._text_direction)
        self._declarations_forwarded += 1

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction is not FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            self._reset()
            await self.push_frame(frame, direction)
            return

        if self._response_active and isinstance(frame, FunctionCallsStartedFrame):
            self._function_calls_started = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._reset()
            self._guard_turn = self._turn_provider()
            self._is_consultation_response = self._consultation_response_provider()
            self._guard_response = (
                self._is_consultation_response and self._guard_turn in (1, 2)
            )
            self._response_active = True
            self._text_direction = direction
            self._response_skip_tts = frame.skip_tts
            self._latest_user_text = self._user_text_provider()
            self._consultation_text = self._consultation_text_provider()
            self._faq_reference = self._faq_reference_provider()
            await self.push_frame(frame, direction)
            return

        if self._response_active and isinstance(frame, LLMTextFrame):
            self._response_had_output = True
        if self._guard_response and isinstance(frame, LLMTextFrame):
            if frame.skip_tts:
                await self.push_frame(frame, direction)
            else:
                self._text_parts.append(frame.text)
                self._pending_text += frame.text
                self._text_direction = direction
                sentences, self._pending_text = _completed_sentences(self._pending_text)
                for sentence in sentences:
                    await self._emit_declaration(sentence)
            return

        if self._response_active and isinstance(frame, LLMFullResponseEndFrame):
            if frame.skip_tts is not None:
                self._response_skip_tts = frame.skip_tts
            if (
                not self._guard_response
                and not self._response_had_output
                and not self._function_calls_started
            ):
                await self.push_frame(
                    self._generated_text_frame(
                        (
                            build_consultation_fallback_reply(
                                self._guard_turn,
                                self._consultation_text or self._latest_user_text,
                                self._faq_reference,
                            )
                            if self._is_consultation_response
                            else build_conversation_fallback_reply(
                                self._latest_user_text
                            )
                        )
                    ),
                    self._text_direction,
                )
                self._response_had_output = True

            if not self._guard_response:
                self._reset()
                await self.push_frame(frame, direction)
                return

            text = _clean_spoken_text("".join(self._text_parts))
            ending_intent = _has_closing_intent(self._latest_user_text)
            question = ""
            if text:
                tail_question = _normalized_question(self._pending_text)
                if not tail_question:
                    await self._emit_declaration(self._pending_text)
                sentences = [
                    part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()
                ]
                question = tail_question or _last_intake_question(sentences)
            elif not self._response_had_output and not self._function_calls_started:
                fallback = build_consultation_fallback_reply(
                    self._guard_turn,
                    self._consultation_text or self._latest_user_text,
                    self._faq_reference,
                )
                fallback_sentences = [
                    part.strip()
                    for part in _SENTENCE_RE.findall(fallback)
                    if part.strip()
                ]
                for sentence in fallback_sentences:
                    await self._emit_declaration(sentence)
                question = _last_intake_question(fallback_sentences)

            if (
                not ending_intent
                and not self._function_calls_started
                and (bool(text) or not self._response_had_output)
                and self._question_emitted_turn != self._guard_turn
            ):
                consultation_text = self._consultation_text or self._latest_user_text
                # Keep intake deterministic. This prevents the model from
                # re-asking a guessed zodiac or irrelevant room details.
                question = select_intake_question(
                    self._guard_turn, consultation_text
                )
                if question:
                    await self.push_frame(
                        self._generated_text_frame(question), self._text_direction
                    )
                    self._question_emitted_turn = self._guard_turn
            self._reset()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


__all__ = [
    "ConsultationResponseGuardProcessor",
    "build_conversation_fallback_reply",
    "build_consultation_fallback_reply",
    "sanitize_intake_reply",
    "select_intake_question",
]
