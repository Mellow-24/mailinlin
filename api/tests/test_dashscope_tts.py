"""Pure unit tests for the DashScope CosyVoice raw WebSocket service."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

import aiohttp
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame

from api.services.dashscope.tts import DashScopeTTSService

_CLOSE = object()


class MockWebSocket:
    """Small aiohttp WebSocket stand-in backed by an async queue."""

    def __init__(self) -> None:
        self.closed = False
        self.sent: list[dict] = []
        self._messages: asyncio.Queue = asyncio.Queue()

    async def send_json(self, message: dict) -> None:
        if self.closed:
            raise ConnectionError("mock websocket is closed")
        self.sent.append(message)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self._messages.put(_CLOSE)

    def exception(self):
        return None

    async def feed_json(self, message: dict) -> None:
        await self._messages.put(
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(message))
        )

    async def feed_binary(self, audio: bytes) -> None:
        await self._messages.put(
            SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=audio)
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self._messages.get()
        if message is _CLOSE:
            raise StopAsyncIteration
        return message


class MockSession:
    """Caller-owned aiohttp session stand-in that never touches the network."""

    def __init__(self, websocket: MockWebSocket) -> None:
        self.closed = False
        self.websocket = websocket
        self.connect_calls: list[tuple[str, dict]] = []

    async def ws_connect(self, url: str, **kwargs):
        self.connect_calls.append((url, kwargs))
        return self.websocket

    async def close(self) -> None:
        self.closed = True


async def _wait_for_sent(websocket: MockWebSocket, count: int) -> None:
    async with asyncio.timeout(1.0):
        while len(websocket.sent) < count:
            await asyncio.sleep(0)


def _task_event(task_id: str, event: str, **header_fields) -> dict:
    return {
        "header": {"task_id": task_id, "event": event, **header_fields},
        "payload": {},
    }


def _service(websocket: MockWebSocket, **kwargs) -> DashScopeTTSService:
    service = DashScopeTTSService(
        api_key="test-key",
        base_url="wss://example.test/api-ws/v1/inference",
        aiohttp_session=MockSession(websocket),
        **kwargs,
    )
    # These tests exercise the provider protocol directly, without constructing
    # a full Pipecat pipeline/StartFrame.
    service._sample_rate = service._init_sample_rate
    service.audio_context_available = lambda context_id: context_id == "context-1"
    service.append_to_audio_context = AsyncMock()
    service.remove_audio_context = AsyncMock()
    service.stop_all_metrics = AsyncMock()
    service.stop_ttfb_metrics = AsyncMock()
    service.start_tts_usage_metrics = AsyncMock()
    return service


class TestDashScopeTTSService(IsolatedAsyncioTestCase):
    async def test_prewarm_is_safe_to_overlap_and_reuse(self) -> None:
        websocket = MockWebSocket()
        service = _service(websocket)

        await asyncio.gather(service.prewarm(), service.prewarm())
        await service.prewarm()

        self.assertEqual(len(service._session.connect_calls), 1)
        await service._disconnect(cancel_active=False)

    async def test_primed_task_is_adopted_by_first_utterance(self) -> None:
        websocket = MockWebSocket()
        service = _service(websocket)

        priming = asyncio.create_task(service.prime())
        await _wait_for_sent(websocket, 1)
        task_id = websocket.sent[0]["header"]["task_id"]
        await websocket.feed_json(_task_event(task_id, "task-started"))
        await priming

        self.assertIsNone(service._active_context_id)
        first_text = asyncio.create_task(
            anext(service.run_tts("你好。", "context-1"))
        )
        self.assertIsNone(await first_text)
        self.assertEqual(len(websocket.sent), 2)
        self.assertEqual(websocket.sent[1]["header"]["action"], "continue-task")
        self.assertEqual(service._active_context_id, "context-1")

        await service.flush_audio("context-1")
        await websocket.feed_json(_task_event(task_id, "task-finished"))
        async with asyncio.timeout(1.0):
            while service._active_task_id is not None:
                await asyncio.sleep(0)
        await service._disconnect(cancel_active=False)

    async def test_reuses_connection_for_a_second_conversation_turn(self) -> None:
        websocket = MockWebSocket()
        service = _service(
            websocket,
            model="qwen-audio-3.0-tts-flash",
            voice=(
                "qwen-audio-3.0-tts-flash-yishuiyue-e46ca9514e714a479eeb17e5fbfbfab7"
            ),
        )
        service.audio_context_available = lambda context_id: (
            context_id
            in {
                "context-1",
                "context-2",
            }
        )
        await service._connect()

        first_result = asyncio.create_task(
            anext(service.run_tts("第一轮回答。", "context-1"))
        )
        await _wait_for_sent(websocket, 1)
        first_task_id = websocket.sent[0]["header"]["task_id"]
        await websocket.feed_json(_task_event(first_task_id, "task-started"))
        self.assertIsNone(await first_result)
        await service.flush_audio("context-1")
        await websocket.feed_json(_task_event(first_task_id, "task-finished"))
        async with asyncio.timeout(1.0):
            while service._active_task_id is not None:
                await asyncio.sleep(0)

        second_result = asyncio.create_task(
            anext(service.run_tts("第二轮回答。", "context-2"))
        )
        await _wait_for_sent(websocket, 4)
        second_run_task = websocket.sent[3]
        second_task_id = second_run_task["header"]["task_id"]
        self.assertNotEqual(first_task_id, second_task_id)
        self.assertEqual(
            second_run_task["payload"]["model"],
            "qwen-audio-3.0-tts-flash",
        )
        self.assertIn("yishuiyue", second_run_task["payload"]["parameters"]["voice"])
        await websocket.feed_json(_task_event(second_task_id, "task-started"))
        self.assertIsNone(await second_result)
        await service.flush_audio("context-2")
        await websocket.feed_binary(b"\x01\x02")
        await websocket.feed_json(_task_event(second_task_id, "task-finished"))
        async with asyncio.timeout(1.0):
            while service._active_task_id is not None:
                await asyncio.sleep(0)

        self.assertEqual(len(service._session.connect_calls), 1)
        self.assertTrue(
            any(
                isinstance(call.args[1], TTSAudioRawFrame)
                and call.args[1].context_id == "context-2"
                for call in service.append_to_audio_context.await_args_list
            )
        )
        await service._disconnect(cancel_active=False)

    async def test_streaming_task_sends_protocol_and_emits_pcm_audio(self) -> None:
        websocket = MockWebSocket()
        service = _service(websocket, instruction="  用温柔粤语  ", rate=1.03)
        await service._connect()

        generator = service.run_tts("你好，欢迎来到易水。", "context-1")
        run_result = asyncio.create_task(anext(generator))

        await _wait_for_sent(websocket, 1)
        run_task = websocket.sent[0]
        task_id = run_task["header"]["task_id"]
        self.assertEqual(run_task["header"]["action"], "run-task")
        self.assertEqual(run_task["payload"]["model"], "cosyvoice-v3-flash")
        self.assertEqual(
            run_task["payload"]["parameters"],
            {
                "text_type": "PlainText",
                "voice": "longjiaxin_v3",
                "format": "pcm",
                "sample_rate": 24000,
                "instruction": "用温柔粤语",
                "rate": 1.03,
            },
        )

        await websocket.feed_json(_task_event(task_id, "task-started"))
        self.assertIsNone(await run_result)
        await _wait_for_sent(websocket, 2)
        self.assertEqual(
            websocket.sent[1],
            {
                "header": {
                    "action": "continue-task",
                    "task_id": task_id,
                    "streaming": "duplex",
                },
                "payload": {"input": {"text": "你好，欢迎来到易水。"}},
            },
        )

        await service.flush_audio("context-1")
        self.assertEqual(websocket.sent[2]["header"]["action"], "finish-task")
        self.assertEqual(websocket.sent[2]["payload"]["input"], {})

        await websocket.feed_json(
            {
                "header": {"task_id": task_id, "event": "result-generated"},
                "payload": {"output": {"type": "sentence-synthesis"}},
            }
        )
        # The first odd byte is buffered and joined with the following chunk,
        # so each frame contains complete signed 16-bit PCM samples.
        await websocket.feed_binary(b"\x01")
        await websocket.feed_binary(b"\x02\x03\x04")
        await websocket.feed_json(_task_event(task_id, "task-finished"))
        async with asyncio.timeout(1.0):
            while service._active_task_id is not None:
                await asyncio.sleep(0)

        appended_frames = [
            call.args[1] for call in service.append_to_audio_context.await_args_list
        ]
        audio_frames = [
            frame for frame in appended_frames if isinstance(frame, TTSAudioRawFrame)
        ]
        self.assertEqual(len(audio_frames), 1)
        self.assertEqual(audio_frames[0].audio, b"\x01\x02\x03\x04")
        self.assertEqual(audio_frames[0].sample_rate, 24000)
        self.assertEqual(audio_frames[0].context_id, "context-1")
        self.assertTrue(
            any(isinstance(frame, TTSStoppedFrame) for frame in appended_frames)
        )
        service.remove_audio_context.assert_awaited_once_with("context-1")

        session = service._session
        await service._disconnect(cancel_active=False)
        self.assertTrue(websocket.closed)
        # The injected session is caller-owned and must remain open.
        self.assertIsNotNone(session)
        self.assertFalse(session.closed)

    async def test_user_interruption_sends_cancel_and_discards_late_audio(self) -> None:
        websocket = MockWebSocket()
        service = _service(websocket)
        await service._connect()

        generator = service.run_tts("这是一段将被打断的语音。", "context-1")
        run_result = asyncio.create_task(anext(generator))
        await _wait_for_sent(websocket, 1)
        task_id = websocket.sent[0]["header"]["task_id"]
        await websocket.feed_json(_task_event(task_id, "task-started"))
        self.assertIsNone(await run_result)

        interruption = asyncio.create_task(
            service.on_audio_context_interrupted("context-1")
        )
        await _wait_for_sent(websocket, 3)
        cancel_task = websocket.sent[2]
        self.assertEqual(cancel_task["header"]["action"], "finish-task")
        self.assertEqual(cancel_task["header"]["task_id"], task_id)
        self.assertEqual(cancel_task["payload"]["input"], {"directive": "cancel"})

        await websocket.feed_binary(b"\x01\x02")
        await websocket.feed_json(_task_event(task_id, "task-finished"))
        await interruption

        self.assertFalse(
            any(
                isinstance(call.args[1], TTSAudioRawFrame)
                for call in service.append_to_audio_context.await_args_list
            )
        )
        service.remove_audio_context.assert_not_awaited()
        self.assertIsNone(service._active_task_id)
        await service._disconnect(cancel_active=False)

    async def test_task_failure_unblocks_run_tts_without_network_retry(self) -> None:
        websocket = MockWebSocket()
        service = _service(websocket)
        await service._connect()

        generator = service.run_tts("测试", "context-1")
        first_result = asyncio.create_task(anext(generator))
        await _wait_for_sent(websocket, 1)
        task_id = websocket.sent[0]["header"]["task_id"]
        await websocket.feed_json(
            _task_event(
                task_id,
                "task-failed",
                error_code="InvalidParameter",
                error_message="unsupported voice",
            )
        )

        self.assertIsNone(await first_result)
        appended_frames = [
            call.args[1] for call in service.append_to_audio_context.await_args_list
        ]
        error_frames = [
            frame for frame in appended_frames if isinstance(frame, ErrorFrame)
        ]
        self.assertEqual(len(error_frames), 1)
        self.assertIn("InvalidParameter", error_frames[0].error)
        self.assertEqual(
            sum(isinstance(frame, TTSStoppedFrame) for frame in appended_frames), 1
        )
        self.assertIsNone(service._active_task_id)
        self.assertEqual(len(service._session.connect_calls), 1)
        await generator.aclose()
        await service._disconnect(cancel_active=False)


class TestDashScopeTTSValidation(TestCase):
    def test_push_to_talk_priming_does_not_pause_transcription_frames(self) -> None:
        service = _service(MockWebSocket())
        self.assertTrue(service._pause_frame_processing)

        service.enable_task_priming()

        self.assertTrue(service._auto_prime_tasks)
        self.assertFalse(service._pause_frame_processing)

    def test_format_validation_and_blank_instruction(self) -> None:
        websocket = MockWebSocket()
        with self.assertRaisesRegex(ValueError, "only supports raw PCM"):
            _service(websocket, audio_format="mp3")

        service = _service(websocket, instruction="   ")
        parameters = service._build_run_task("task-id")["payload"]["parameters"]
        self.assertNotIn("instruction", parameters)
        self.assertNotIn("rate", parameters)

        with self.assertRaisesRegex(ValueError, "rate must be between"):
            _service(websocket, rate=2.1)
