"""Regression tests for the one-final-turn / one-LLM direct voice path."""

import asyncio
from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    LLMContextFrame,
    TranscriptionFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.direct_final_transcript_processor import (
    DirectFinalTranscriptProcessor,
    is_consultation_turn,
)
from api.services.workflow.initial_context import (
    VOICE_DEMO_DATE_MARKER,
    build_voice_demo_date_instruction,
)


class TestDirectFinalTranscriptProcessor(IsolatedAsyncioTestCase):
    def test_current_date_and_relative_years_are_explicit(self) -> None:
        instruction = build_voice_demo_date_instruction(
            datetime(2026, 9, 3, 12, tzinfo=UTC)
        )
        for expected in (
            "2026-09-03",
            "『今年』係 2026 年",
            "『舊年／去年』係 2025 年",
            "『下年／明年』係 2027 年",
            "其他年份",
            "出生年份",
            "FAQ 係 2026 年版本",
        ):
            self.assertIn(expected, instruction)

    def test_date_rolls_over_at_hong_kong_midnight_not_utc_midnight(self) -> None:
        before = build_voice_demo_date_instruction(
            datetime(2026, 12, 31, 15, 59, tzinfo=UTC)
        )
        after = build_voice_demo_date_instruction(
            datetime(2026, 12, 31, 16, 0, tzinfo=UTC)
        )
        self.assertIn("2026-12-31", before)
        self.assertIn("『今年』係 2026 年", before)
        self.assertIn("2027-01-01", after)
        self.assertIn("『今年』係 2027 年", after)

    async def test_clock_refreshes_for_chat_and_consultation_without_extra_llm_calls(
        self,
    ) -> None:
        persona = {"role": "system", "content": "你係玲玲師傅 AI。"}
        old_reply = {"role": "assistant", "content": "今年係2025年。"}
        context = LLMContext(messages=[persona, old_reply])
        processor = DirectFinalTranscriptProcessor(context, consultation_questions=3)
        processor.push_frame = AsyncMock()
        instructions = [
            build_voice_demo_date_instruction(datetime(year, 9, 3, tzinfo=UTC))
            for year in (2026, 2027)
        ]
        try:
            with patch(
                "api.services.pipecat.direct_final_transcript_processor."
                "build_voice_demo_date_instruction",
                side_effect=instructions,
            ):
                for text, instruction in zip(
                    ("你好啊，玲玲師傅！", "我想問今年運程"), instructions
                ):
                    processor._pending_texts = [text]
                    await processor._commit_pending()
                    clock_messages = [
                        message for message in context.messages
                        if message.get("role") == "system"
                        and message.get("content", "").startswith(VOICE_DEMO_DATE_MARKER)
                    ]
                    self.assertEqual(clock_messages, [{"role": "system", "content": instruction}])
                    self.assertEqual(context.messages[-2], clock_messages[0])
                    self.assertEqual(context.messages[-1], {"role": "user", "content": text})
            self.assertIn(persona, context.messages)
            self.assertIn(old_reply, context.messages)
            self.assertEqual(processor.push_frame.await_count, 2)
            self.assertEqual(processor.consultation_turn_count, 1)
        finally:
            await processor.cleanup()

    def test_turn_classifier_keeps_greetings_and_unrelated_chat_outside_consultation(
        self,
    ) -> None:
        for text in (
            "你好啊，玲玲姐！",
            "你好啊，玲玲師傅！",
            "hello 師傅",
            "今日天氣幾好",
            "幫我寫一段程式碼",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_consultation_turn(text))

        for text in (
            "我想睇下今年運程",
            "我想問睡房風水",
            "我係95年出世",
            "我屬狗",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_consultation_turn(text))

    async def test_greeting_does_not_advance_or_pollute_consultation_intake(
        self,
    ) -> None:
        context = LLMContext()
        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=0.01,
            consultation_questions=3,
        )
        processor.push_frame = AsyncMock()

        try:
            await processor.process_frame(
                TranscriptionFrame(
                    text="你好啊，玲玲姐！",
                    user_id="visitor",
                    timestamp="2026-09-02T00:00:01Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.sleep(0.03)

            self.assertEqual(processor.user_turn_count, 1)
            self.assertEqual(processor.consultation_turn_count, 0)
            self.assertEqual(processor.consultation_text, "")
            self.assertFalse(processor.current_turn_is_consultation)
            ordinary_marker = next(
                message["content"]
                for message in context.messages
                if message.get("role") == "system"
                and str(message.get("content", "")).startswith(
                    processor._FLOW_MARKER
                )
            )
            self.assertIn("普通對話", ordinary_marker)
            self.assertIn("禁止牽強解讀", ordinary_marker)

            context.add_message(
                {"role": "assistant", "content": "你好呀，今日有咩想了解？"}
            )
            await processor.process_frame(
                TranscriptionFrame(
                    text="我想睇下今年事業運",
                    user_id="visitor",
                    timestamp="2026-09-02T00:00:02Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.sleep(0.03)

            self.assertEqual(processor.user_turn_count, 2)
            self.assertEqual(processor.consultation_turn_count, 1)
            self.assertEqual(processor.consultation_text, "我想睇下今年事業運")
            consultation_marker = next(
                message["content"]
                for message in context.messages
                if message.get("role") == "system"
                and str(message.get("content", "")).startswith(
                    processor._FLOW_MARKER
                )
            )
            self.assertIn("第1问", consultation_marker)
            self.assertIn("第2问", consultation_marker)
        finally:
            await processor.cleanup()

    async def test_consultation_text_accumulates_user_answers_in_order(self) -> None:
        context = LLMContext()
        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=0.01,
            consultation_questions=3,
        )
        processor.push_frame = AsyncMock()

        try:
            for index, text in enumerate(("我想睇今年運程", "我屬馬"), start=1):
                await processor.process_frame(
                    TranscriptionFrame(
                        text=text,
                        user_id="visitor",
                        timestamp=f"2026-09-02T00:00:0{index}Z",
                        finalized=True,
                    ),
                    FrameDirection.DOWNSTREAM,
                )
                await asyncio.sleep(0.03)

            self.assertEqual(
                getattr(processor, "consultation_text", None),
                "我想睇今年運程\n我屬馬",
            )
        finally:
            await processor.cleanup()

    async def test_default_debounce_emits_one_llm_frame_within_quarter_second(
        self,
    ) -> None:
        context = LLMContext()
        processor = DirectFinalTranscriptProcessor(context)
        processor.push_frame = AsyncMock()

        try:
            await processor.process_frame(
                TranscriptionFrame(
                    text="客廳財位點擺？",
                    user_id="visitor",
                    timestamp="2026-09-01T00:00:00Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.sleep(0.25)

            llm_frames = [
                call.args[0]
                for call in processor.push_frame.await_args_list
                if isinstance(call.args[0], LLMContextFrame)
            ]
            self.assertEqual(len(llm_frames), 1)
        finally:
            await processor.cleanup()

    async def test_multiple_final_chunks_become_one_llm_context_frame(self) -> None:
        context = LLMContext()
        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=0.02,
        )
        processor.push_frame = AsyncMock()

        first = TranscriptionFrame(
            text="我想睇下睡房，",
            user_id="visitor",
            timestamp="2026-09-01T00:00:00Z",
            finalized=True,
        )
        second = TranscriptionFrame(
            text="應該點樣擺？",
            user_id="visitor",
            timestamp="2026-09-01T00:00:01Z",
            finalized=True,
        )
        await processor.process_frame(first, FrameDirection.DOWNSTREAM)
        await processor.process_frame(second, FrameDirection.DOWNSTREAM)
        # Provider retry of the same final must not create a second request.
        await processor.process_frame(second, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

        llm_frames = [
            call.args[0]
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMContextFrame)
        ]
        self.assertEqual(len(llm_frames), 1)
        self.assertEqual(
            [message for message in context.messages if message.get("role") == "user"],
            [
                {
                    "role": "user",
                    "content": "我想睇下睡房， 應該點樣擺？",
                }
            ],
        )
        await processor.cleanup()

    async def test_client_turn_boundary_commits_split_finals_once(self) -> None:
        context = LLMContext()
        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=5.0,
        )
        processor.push_frame = AsyncMock()

        try:
            for index, text in enumerate(("我想睇今年運程，", "主要想問事業。")):
                await processor.process_frame(
                    TranscriptionFrame(
                        text=text,
                        user_id="visitor",
                        timestamp=f"2026-09-21T00:00:0{index}Z",
                        finalized=True,
                    ),
                    FrameDirection.DOWNSTREAM,
                )

            await processor.commit_client_turn()
            await asyncio.sleep(0.25)

            llm_frames = [
                call.args[0]
                for call in processor.push_frame.await_args_list
                if isinstance(call.args[0], LLMContextFrame)
            ]
            self.assertEqual(len(llm_frames), 1)
            self.assertEqual(
                context.messages[-1],
                {"role": "user", "content": "我想睇今年運程， 主要想問事業。"},
            )
        finally:
            await processor.cleanup()

    async def test_in_flight_response_suppresses_late_duplicate_turn(self) -> None:
        context = LLMContext()
        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=5.0,
        )
        processor.push_frame = AsyncMock()

        try:
            first = TranscriptionFrame(
                text="我係九五年出世。",
                user_id="visitor",
                timestamp="2026-09-21T00:00:01Z",
                finalized=True,
            )
            await processor.process_frame(first, FrameDirection.DOWNSTREAM)
            await processor.commit_client_turn()
            await asyncio.sleep(0.25)

            late = TranscriptionFrame(
                text="我係九五年出世。",
                user_id="visitor",
                timestamp="2026-09-21T00:00:02Z",
                finalized=True,
            )
            await processor.process_frame(late, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)
            self.assertEqual(
                sum(
                    isinstance(call.args[0], LLMContextFrame)
                    for call in processor.push_frame.await_args_list
                ),
                1,
            )

            await processor.process_frame(
                BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
            )
            next_turn = TranscriptionFrame(
                text="再講下財運。",
                user_id="visitor",
                timestamp="2026-09-21T00:00:03Z",
                finalized=True,
            )
            await processor.process_frame(next_turn, FrameDirection.DOWNSTREAM)
            await processor.commit_client_turn()
            await asyncio.sleep(0.25)
            self.assertEqual(
                sum(
                    isinstance(call.args[0], LLMContextFrame)
                    for call in processor.push_frame.await_args_list
                ),
                2,
            )
        finally:
            await processor.cleanup()

    async def test_three_question_flow_advances_then_stops_asking(self) -> None:
        context = LLMContext(
            messages=[
                {
                    "role": "assistant",
                    "content": "今日想問咩呢？",
                }
            ]
        )
        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=0.01,
            consultation_questions=3,
        )
        processor.push_frame = AsyncMock()

        expected_stage_terms = (
            ("对第1问的回答", "第2问"),
            ("对第2问的回答", "最后的第3问"),
            ("对第3问的回答", "三问已完成", "禁止"),
            ("三问咨询已经完成", "禁止再进行资料收集式提问"),
            ("三问咨询已经完成", "禁止再进行资料收集式提问"),
        )
        user_texts = (
            "我想睡得好一點。",
            "床頭靠窗。",
            "房門對住床尾。",
            "如果不能搬床怎麼做。",
            "為什麼要先做這一步。",
        )

        for index, (user_text, stage_terms) in enumerate(
            zip(user_texts, expected_stage_terms),
            start=1,
        ):
            await processor.process_frame(
                TranscriptionFrame(
                    text=user_text,
                    user_id="visitor",
                    timestamp=f"2026-09-01T00:00:0{index}Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.sleep(0.03)

            markers = [
                message
                for message in context.messages
                if message.get("role") == "system"
                and str(message.get("content", "")).startswith(
                    processor._FLOW_MARKER
                )
            ]
            self.assertEqual(len(markers), 1)
            for stage_term in stage_terms:
                self.assertIn(stage_term, markers[0]["content"])

            # Mirror the shared assistant aggregator before the next user turn.
            context.add_message(
                {"role": "assistant", "content": f"模擬回答 {index}"}
            )

        llm_frames = [
            call.args[0]
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMContextFrame)
        ]
        self.assertEqual(len(llm_frames), len(user_texts))
        self.assertEqual(
            [
                message["content"]
                for message in context.messages
                if message.get("role") == "user"
            ],
            list(user_texts),
        )
        await processor.cleanup()

    async def test_context_preparer_runs_before_the_single_llm_context_frame(
        self,
    ) -> None:
        context = LLMContext()
        calls: list[tuple[int, str, int]] = []

        async def prepare_context(user_turn: int, user_text: str) -> None:
            calls.append((user_turn, user_text, len(context.messages)))

        processor = DirectFinalTranscriptProcessor(
            context,
            debounce_seconds=0.01,
            context_preparer=prepare_context,
        )
        processor.push_frame = AsyncMock()

        await processor.process_frame(
            TranscriptionFrame(
                text="客廳財位點擺？",
                user_id="visitor",
                timestamp="2026-09-01T00:00:00Z",
                finalized=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.03)

        llm_frames = [
            call.args[0]
            for call in processor.push_frame.await_args_list
            if isinstance(call.args[0], LLMContextFrame)
        ]
        self.assertEqual(calls, [(1, "客廳財位點擺？", 0)])
        self.assertEqual(len(llm_frames), 1)
        await processor.cleanup()


if __name__ == "__main__":
    import unittest

    unittest.main()
