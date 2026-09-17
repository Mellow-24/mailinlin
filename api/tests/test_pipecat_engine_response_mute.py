"""Regression tests for non-interruptible response generation windows."""

from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)

from api.services.workflow.pipecat_engine import PipecatEngine


def _engine(*, allow_interrupt: bool) -> PipecatEngine:
    engine = PipecatEngine(
        workflow=SimpleNamespace(),
        call_context_vars={},
    )
    engine._current_node = SimpleNamespace(allow_interrupt=allow_interrupt)
    return engine


@pytest.mark.asyncio
async def test_non_interruptible_node_mutes_until_generated_audio_finishes():
    engine = _engine(allow_interrupt=False)

    assert await engine.should_mute_user(LLMFullResponseStartFrame()) is True
    await engine.handle_llm_text_frame("你好")
    assert await engine.should_mute_user(LLMFullResponseEndFrame()) is True
    assert await engine.should_mute_user(BotStartedSpeakingFrame()) is True
    assert await engine.should_mute_user(BotStoppedSpeakingFrame()) is False


@pytest.mark.asyncio
async def test_empty_response_releases_generation_mute_at_end_frame():
    engine = _engine(allow_interrupt=False)

    assert await engine.should_mute_user(LLMFullResponseStartFrame()) is True
    assert await engine.should_mute_user(LLMFullResponseEndFrame()) is False


@pytest.mark.asyncio
async def test_interruptible_node_remains_unmuted_while_llm_generates():
    engine = _engine(allow_interrupt=True)

    assert await engine.should_mute_user(LLMFullResponseStartFrame()) is False
