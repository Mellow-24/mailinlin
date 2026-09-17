"""Regression tests for deterministic consultation question limiting."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

import api.services.pipecat.consultation_response_guard_processor as guard_module

ConsultationResponseGuardProcessor = guard_module.ConsultationResponseGuardProcessor
sanitize_intake_reply = guard_module.sanitize_intake_reply


class TestConsultationResponseGuard(IsolatedAsyncioTestCase):
    async def test_greeting_response_bypasses_consultation_guard_and_zodiac_question(
        self,
    ) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 0,
            user_text_provider=lambda: "你好啊，玲玲姐！",
            consultation_response_provider=lambda: False,
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("你好呀，今日有咩想了解？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["你好呀，今日有咩想了解？"])
        self.assertNotIn("生肖", "".join(texts))

    async def test_empty_greeting_response_uses_neutral_not_faq_fallback(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 0,
            user_text_provider=lambda: "你好啊，玲玲姐！",
            consultation_text_provider=lambda: "",
            faq_reference_provider=lambda: (
                "[YISHUI_INTERNAL_FAQ_REFERENCE]\n"
                "粤语参考回答：麦玲玲師傅精於八字同姻緣推算。"
            ),
            consultation_response_provider=lambda: False,
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["你好呀，好高興同你傾偈。今日有咩想了解？"])
        self.assertNotIn("八字", "".join(texts))

    def test_fortune_intake_asks_knowledge_profile_not_space(self) -> None:
        selector = getattr(guard_module, "select_intake_question", None)
        self.assertIsNotNone(selector)
        question = selector(1, "我想睇今年事業同財運")

        self.assertEqual(question, "你係咩生肖？")
        self.assertNotIn("空間", question)

    def test_bazi_intake_collects_birth_date_then_birth_hour(self) -> None:
        selector = getattr(guard_module, "select_intake_question", None)
        self.assertIsNotNone(selector)
        self.assertEqual(
            selector(1, "我想睇八字"),
            "你嘅出生年月日係點？",
        )
        self.assertEqual(
            selector(2, "我想睇八字\n我係1990年5月12日出世"),
            "你大概喺咩時辰出世？",
        )

    def test_explicit_feng_shui_intake_can_ask_a_relevant_direction(self) -> None:
        selector = getattr(guard_module, "select_intake_question", None)
        self.assertIsNotNone(selector)
        self.assertEqual(
            selector(1, "我想睇屋企風水"),
            "你間屋大門大概向邊個方位？",
        )

    def test_faq_fallback_replies_without_a_second_model_call(self) -> None:
        builder = getattr(guard_module, "build_consultation_fallback_reply", None)
        self.assertIsNotNone(builder)
        reference = (
            "[YISHUI_INTERNAL_FAQ_REFERENCE]\n"
            "粤语参考回答：屬馬今年變化較多，做決定要留有餘地。"
        )

        intake = builder(
            1,
            "我想睇今年運程",
            reference,
        )
        final = builder(
            3,
            "我想睇今年運程\n我屬馬\n我係1990年出世",
            reference,
        )

        self.assertIn("屬馬今年變化較多", intake)
        self.assertTrue(intake.endswith("你係咩生肖？"))
        self.assertEqual(final.count("？") + final.count("?"), 0)

    def test_short_birth_year_counts_as_the_collected_year(self) -> None:
        self.assertEqual(
            guard_module.select_intake_question(2, "我屬狗\n我係95年出世"),
            "你今次最想集中睇邊一方面？",
        )

    def test_final_fallback_thanks_does_not_repeat_the_previous_faq(self) -> None:
        reference = (
            "[YISHUI_INTERNAL_FAQ_REFERENCE]\n"
            "粤语参考回答：你屬狗，今年合太歲，運程順。"
        )

        reply = guard_module.build_consultation_fallback_reply(
            3,
            "我想睇運程\n我屬狗\nok，多謝晒你",
            reference,
        )

        self.assertIn("唔使客氣", reply)
        self.assertNotIn("屬狗", reply)

    def test_early_goodbye_or_thanks_never_enters_faq_fallback(self) -> None:
        reference = (
            "[YISHUI_INTERNAL_FAQ_REFERENCE]\n"
            "粤语参考回答：你屬狗，今年合太歲，運程順。"
        )

        for text in ("再見", "ok，多謝晒你啊"):
            with self.subTest(text=text):
                reply = guard_module.build_consultation_fallback_reply(
                    1,
                    text,
                    reference,
                )
                self.assertIn("唔使客氣", reply)
                self.assertNotIn("屬狗", reply)
                self.assertNotIn("？", reply)

    def test_sanitizer_keeps_analysis_and_only_the_final_question(self) -> None:
        raw = (
            "我先按你講睡房風水理解，床頭對門或者近窗會影響休息？"
            "光線同噪音都會令睡眠環境唔穩定。"
            "你平時係咪容易醒？"
            "方便講下床頭朝邊個方向呢？"
        )

        guarded = sanitize_intake_reply(raw, "最近成日瞓得唔好")

        self.assertEqual(guarded.count("？") + guarded.count("?"), 1)
        self.assertNotIn("最近成日瞓得唔好", guarded)
        self.assertNotIn("你提到", guarded)
        self.assertIn("光線同噪音", guarded)
        self.assertIn("床頭朝邊個方向", guarded)
        self.assertNotIn("容易醒", guarded)

    def test_sanitizer_normalizes_question_like_periods_without_duplicates(
        self,
    ) -> None:
        for raw in ("你想先睇邊個空間。", "你想先睇邊個空間."):
            with self.subTest(raw=raw):
                self.assertEqual(
                    sanitize_intake_reply(raw),
                    "你想先睇邊個空間？",
                )

    def test_sanitizer_keeps_how_phrases_in_declarations(self) -> None:
        for raw in (
            "無論如何，床頭靠牆較穩定。",
            "我會講下點樣調整。",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_intake_reply(raw), raw)

    def test_sanitizer_distinguishes_embedded_how_from_direct_requests(
        self,
    ) -> None:
        cases = (
            ("你想像一下如何調整床頭。", "你想像一下如何調整床頭。"),
            ("你可以先看看如何調整床頭。", "你可以先看看如何調整床頭。"),
            ("你想先睇邊個空間。", "你想先睇邊個空間？"),
            ("你可以講下大門方向。", "你可以講下大門方向？"),
        )

        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_intake_reply(raw), expected)

    def test_sanitizer_normalizes_direct_how_question_prefixes(self) -> None:
        for raw, expected in (
            ("請問如何調整床頭。", "請問如何調整床頭？"),
            ("想問點樣調整床頭。", "想問點樣調整床頭？"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_intake_reply(raw), expected)

    async def test_first_two_turns_stream_declarations_and_emit_only_final_question(
        self,
    ) -> None:
        state = {"turn": 1, "user": "我想改善睡房睡眠"}
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: state["turn"],
            user_text_provider=lambda: state["user"],
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("床頭靠窗會影響休息？光線亦會令人難放鬆。"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMTextFrame("你會唔會發夢？床頭朝邊個方向呢？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "光線亦會令人難放鬆。",
                "床頭朝邊個方向呢？",
            ],
        )
        self.assertEqual(sum(text.count("？") + text.count("?") for text in texts), 1)
        self.assertNotIn("發夢", "".join(texts))

    async def test_first_declarative_sentence_is_forwarded_before_response_end(
        self,
    ) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想睇屋企風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("大門保持通爽會令動線舒服。"),
            FrameDirection.DOWNSTREAM,
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            "".join(texts),
            "大門保持通爽會令動線舒服。",
        )

    async def test_ascii_declaration_streams_before_response_end(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("First. Final question?"), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["First."])

        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["First.", "Final question?"])

    async def test_question_like_full_stop_is_held_and_normalized(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想睇屋企風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("你想先睇邊個空間。"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["你想先睇邊個空間？"])

    async def test_direct_request_full_stop_is_held_and_normalized(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想睇屋企風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("你可以講下大門方向。"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["你可以講下大門方向？"])

    async def test_final_question_keeps_connectors_and_full_length(self) -> None:
        question = (
            "請你講清楚大門方向、睡房位置、窗戶朝向、走廊動線、客廳採光、"
            "餐桌擺位、廚房爐灶、書房坐向、浴室位置，或者露台開口同玄關收納"
            "實際點樣安排先最適合你而家住嘅單位？"
        )
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想睇屋企風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame(f"先講背景。{question}"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["先講背景。", question])

    async def test_user_transcript_is_not_echoed_before_intake_analysis(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想改善睡眠？床頭應點擺？",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("床頭靠實牆會較穩定。你間房邊度最困擾？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "床頭靠實牆會較穩定。",
                "你間房邊度最困擾？",
            ],
        )
        self.assertEqual(sum(text.count("？") + text.count("?") for text in texts), 1)

    async def test_trailing_declaration_flushes_before_turn_one_fallback_question(
        self,
    ) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("大門保持通爽會令動線舒服"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["大門保持通爽會令動線舒服", "你係咩生肖？"])

    async def test_turn_two_uses_the_final_intake_fallback_question(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 2,
            user_text_provider=lambda: "",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("採光柔和會令休息舒服"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["採光柔和會令休息舒服", "你係咩生肖？"])

    async def test_ending_intent_suppresses_intake_fallback_question(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我唔想再傾啦，多謝",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(LLMTextFrame("明白"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "明白",
            ],
        )
        self.assertEqual(sum(text.count("？") + text.count("?") for text in texts), 0)

    async def test_action_preference_does_not_suppress_intake_fallback(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我唔想再搬床",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(LLMTextFrame("明白"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "明白",
                "你係咩生肖？",
            ],
        )

    async def test_no_problem_acknowledgements_keep_intake_fallback(self) -> None:
        for user_text in ("冇問題", "没有问题"):
            with self.subTest(user_text=user_text):
                processor = ConsultationResponseGuardProcessor(
                    turn_provider=lambda: 1,
                    user_text_provider=lambda: user_text,
                )
                processor.push_frame = AsyncMock()

                await processor.process_frame(
                    LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMTextFrame("明白"), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
                )

                texts = [
                    call.args[0].text
                    for call in processor.push_frame.await_args_list
                    if isinstance(call.args[0], LLMTextFrame)
                ]
                self.assertIn("你係咩生肖？", texts)

    async def test_standalone_decline_suppresses_intake_fallback(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "唔想繼續",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(LLMTextFrame("明白"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "明白",
            ],
        )

    async def test_goal_phrases_with_stop_terms_keep_intake_fallback(self) -> None:
        for user_text in ("我想停止失眠", "我想結束雜亂", "我想结束噪音"):
            with self.subTest(user_text=user_text):
                processor = ConsultationResponseGuardProcessor(
                    turn_provider=lambda: 1,
                    user_text_provider=lambda: user_text,
                )
                processor.push_frame = AsyncMock()

                await processor.process_frame(
                    LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMTextFrame("明白"), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
                )

                texts = [
                    call.args[0].text
                    for call in processor.push_frame.await_args_list
                    if isinstance(call.args[0], LLMTextFrame)
                ]
                self.assertEqual(texts[-1], "你係咩生肖？")

    async def test_explicit_ending_controls_suppress_intake_fallback(self) -> None:
        for user_text in (
            "再見",
            "停止本次咨询",
            "結束諮詢",
            "冇其他問題",
            "没有其他问题",
        ):
            with self.subTest(user_text=user_text):
                processor = ConsultationResponseGuardProcessor(
                    turn_provider=lambda: 1,
                    user_text_provider=lambda: user_text,
                )
                processor.push_frame = AsyncMock()

                await processor.process_frame(
                    LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMTextFrame("明白"), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
                )

                texts = [
                    call.args[0].text
                    for call in processor.push_frame.await_args_list
                    if isinstance(call.args[0], LLMTextFrame)
                ]
                self.assertFalse(
                    any(text.endswith(("？", "?")) for text in texts)
                )

    async def test_compound_and_ascii_ending_intents_suppress_intake_fallback(
        self,
    ) -> None:
        for user_text in ("我不想继续聊", "我唔想繼續傾", "bye."):
            with self.subTest(user_text=user_text):
                processor = ConsultationResponseGuardProcessor(
                    turn_provider=lambda: 1,
                    user_text_provider=lambda: user_text,
                )
                processor.push_frame = AsyncMock()

                await processor.process_frame(
                    LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMTextFrame("明白"), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
                )

                texts = [
                    call.args[0].text
                    for call in processor.push_frame.await_args_list
                    if isinstance(call.args[0], LLMTextFrame)
                ]
                self.assertFalse(
                    any(text.endswith(("？", "?")) for text in texts)
                )

    async def test_ending_intent_suppresses_model_question(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我唔想繼續啦",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("明白。你仲想補充咩？"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "明白。",
            ],
        )
        self.assertEqual(sum(text.count("？") + text.count("?") for text in texts), 0)

    async def test_ending_intent_requires_a_conversational_control(self) -> None:
        cases = (
            ("好，結束吧", True),
            ("再見", True),
            ("停止本次咨询", True),
            ("結束諮詢", True),
            ("我想停止失眠", False),
            ("我不想再聊睡眠，想講客廳", False),
        )
        for user_text, is_ending in cases:
            with self.subTest(user_text=user_text):
                processor = ConsultationResponseGuardProcessor(
                    turn_provider=lambda: 1,
                    user_text_provider=lambda: user_text,
                )
                processor.push_frame = AsyncMock()

                await processor.process_frame(
                    LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMTextFrame("明白"), FrameDirection.DOWNSTREAM
                )
                await processor.process_frame(
                    LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
                )

                texts = [
                    call.args[0].text
                    for call in processor.push_frame.await_args_list
                    if isinstance(call.args[0], LLMTextFrame)
                ]
                has_question = any(
                    text.endswith(("？", "?")) for text in texts
                )
                self.assertEqual(has_question, not is_ending)

    async def test_unpunctuated_question_tail_replaces_fallback_question(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想睇屋企風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("你想先睇邊個空間"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(texts, ["你想先睇邊個空間？"])

    async def test_same_turn_continuation_does_not_repeat_question(
        self,
    ) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想改善睡房風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("第一個觀察。"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("補充觀察。你想補充邊個位置？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "第一個觀察。",
                "你間屋大門大概向邊個方位？",
                "補充觀察。",
            ],
        )
        self.assertEqual(sum(text.count("？") + text.count("?") for text in texts), 1)

    async def test_function_call_cycle_does_not_consume_same_turn_question_slot(
        self,
    ) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想改善睡房風水",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("我先查一查。你想補充邊個位置？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            FunctionCallsStartedFrame(function_calls=[]), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        pre_tool_texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            pre_tool_texts,
            [
                "我先查一查。",
            ],
        )

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("資料顯示床頭靠牆較穩定。你想再講下床頭方向？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "我先查一查。",
                "資料顯示床頭靠牆較穩定。",
                "你想再講下床頭方向？",
            ],
        )

    async def test_empty_response_emits_local_fallback_instead_of_hanging(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想改善睡眠",
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        initial_texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertTrue(initial_texts)
        self.assertIn("傳統文化參考", "".join(initial_texts))
        self.assertEqual(initial_texts[-1], "你係咩生肖？")

    async def test_tool_only_response_does_not_consume_same_turn_question_slot(
        self,
    ) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想改善睡房風水",
        )
        processor.push_frame = AsyncMock()
        tool_frame = LLMTextFrame("工具結果")
        tool_frame.skip_tts = True

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(tool_frame, FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        initial_texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            initial_texts,
            [
                "工具結果",
            ],
        )

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            LLMTextFrame("補充觀察。你想補充邊個位置？"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        texts = [
            call.args[0].text
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            texts,
            [
                "工具結果",
                "補充觀察。",
                "你想補充邊個位置？",
            ],
        )

    async def test_generated_text_inherits_silent_response_skip_tts(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 1,
            user_text_provider=lambda: "我想改善睡眠",
        )
        processor.push_frame = AsyncMock()
        start = LLMFullResponseStartFrame()
        start.skip_tts = True
        end = LLMFullResponseEndFrame()
        end.skip_tts = True

        await processor.process_frame(start, FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            LLMTextFrame("大門保持通爽會令動線舒服。"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(end, FrameDirection.DOWNSTREAM)

        generated_frames = [
            call.args[0]
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMTextFrame)
        ]
        self.assertEqual(
            [frame.text for frame in generated_frames],
            [
                "大門保持通爽會令動線舒服。",
                "你係咩生肖？",
            ],
        )
        self.assertTrue(all(frame.skip_tts for frame in generated_frames))

    async def test_final_analysis_turn_remains_streaming(self) -> None:
        processor = ConsultationResponseGuardProcessor(
            turn_provider=lambda: 3,
            user_text_provider=lambda: "第三次回答",
        )
        processor.push_frame = AsyncMock()
        first = LLMTextFrame("綜合前文，")
        second = LLMTextFrame("先改善遮光。")

        await processor.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(first, FrameDirection.DOWNSTREAM)
        await processor.process_frame(second, FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM
        )

        forwarded = [call.args[0] for call in processor.push_frame.await_args_list]
        self.assertIn(first, forwarded)
        self.assertIn(second, forwarded)


if __name__ == "__main__":
    import unittest

    unittest.main()
