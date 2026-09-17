"""Pure unit tests for the raw DashScope streaming ASR protocol."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from pipecat.frames.frames import (
    ErrorFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from api.services.dashscope.stt import (
    DashScopeSegmentation,
    DashScopeSTTError,
    DashScopeSTTService,
)


class FakeWebSocket:
    """Small aiohttp-compatible WebSocket double with an async receive iterator."""

    def __init__(self, incoming=None):
        self.closed = False
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []
        self.close_calls: list[int] = []
        self._incoming = list(incoming or [])
        self._exception = None

    async def send_str(self, value: str):
        self.sent_text.append(json.loads(value))

    async def send_bytes(self, value: bytes):
        self.sent_bytes.append(value)

    async def close(self, code=1000):
        self.close_calls.append(code)
        self.closed = True

    def exception(self):
        return self._exception

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        value = self._incoming.pop(0)
        if isinstance(value, str):
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=value)
        return value


class FakeSession:
    """ClientSession double that records the WebSocket handshake arguments."""

    def __init__(self, websocket):
        self.closed = False
        self.websocket = websocket
        self.ws_connect_calls: list[tuple[str, dict]] = []

    async def ws_connect(self, url, **kwargs):
        self.ws_connect_calls.append((url, kwargs))
        return self.websocket

    async def close(self):
        self.closed = True


def _service(**kwargs):
    return DashScopeSTTService(api_key="test-dashscope-key", **kwargs)


def _event(task_id, event, **header_fields):
    return {
        "header": {"task_id": task_id, "event": event, **header_fields},
        "payload": {},
    }


def _result(task_id, text, *, sentence_id=1, sentence_end=False, **sentence_fields):
    return {
        "header": {"task_id": task_id, "event": "result-generated"},
        "payload": {
            "output": {
                "sentence": {
                    "sentence_id": sentence_id,
                    "sentence_end": sentence_end,
                    "text": text,
                    **sentence_fields,
                }
            },
            "usage": {"duration": 1} if sentence_end else None,
        },
    }


def test_run_task_preserves_snapshot_model_and_configures_cantonese_as_zh():
    context = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "麦玲玲、風水、流年"}],
        }
    ]
    service = _service(
        model="fun-asr-realtime-2026-02-28",
        sample_rate=16000,
        format="pcm",
        # Fun-ASR accepts one hint; Cantonese is covered by Chinese (zh).
        language_hints=["zh", "en"],
        vocabulary_id="vocab-feng-shui",
        context=context,
        segmentation=DashScopeSegmentation(max_sentence_silence=600),
    )
    service._reset_task_state()

    message = service._build_run_task_message()
    payload = message["payload"]
    parameters = payload["parameters"]

    assert payload["model"] == "fun-asr-realtime-2026-02-28"
    assert parameters["format"] == "pcm"
    assert parameters["sample_rate"] == 16000
    assert parameters["language_hints"] == ["zh"]
    assert parameters["vocabulary_id"] == "vocab-feng-shui"
    assert parameters["semantic_punctuation_enabled"] is False
    assert parameters["max_sentence_silence"] == 600
    assert payload["input"]["context"] == context

    # Construction takes a defensive copy of caller-owned context.
    context[0]["content"][0]["text"] = "changed"
    assert payload["input"]["context"][0]["content"][0]["text"] != "changed"


@pytest.mark.asyncio
async def test_websocket_handshake_sends_workspace_header_and_resolves_placeholder():
    websocket = FakeWebSocket()
    session = FakeSession(websocket)
    service = _service(
        workspace_id="ws-demo-123",
        base_url=(
            "wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        ),
        aiohttp_session=session,
    )

    await service._open_websocket()

    url, kwargs = session.ws_connect_calls[0]
    assert url == ("wss://ws-demo-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference")
    assert kwargs["headers"] == {
        "Authorization": "bearer test-dashscope-key",
        "X-DashScope-WorkSpace": "ws-demo-123",
    }
    assert kwargs["heartbeat"] == 20.0


@pytest.mark.asyncio
async def test_run_task_binary_audio_and_finish_task_protocol():
    websocket = FakeWebSocket()
    service = _service()
    service._reset_task_state()
    service._websocket = websocket

    await service._send_json(service._build_run_task_message())
    service._task_started.set()
    frames = [frame async for frame in service.run_stt(b"\x01\x02\x03\x04")]
    service._task_finished.set()
    await service._finish_task()

    assert websocket.sent_text[0]["header"]["action"] == "run-task"
    assert websocket.sent_bytes == [b"\x01\x02\x03\x04"]
    assert frames == [None]
    assert websocket.sent_text[1] == {
        "header": {
            "action": "finish-task",
            "task_id": service.task_id,
            "streaming": "duplex",
        },
        "payload": {"input": {}},
    }


@pytest.mark.asyncio
async def test_deferred_start_gates_first_audio_until_provider_is_ready():
    websocket = FakeWebSocket()
    service = _service()
    service.enable_deferred_start()
    release_connection = asyncio.Event()

    async def finish_connecting():
        await release_connection.wait()
        service._reset_task_state()
        service._websocket = websocket
        service._task_started.set()

    service._connect_task = asyncio.create_task(finish_connecting())
    audio_task = asyncio.create_task(
        anext(service.run_stt(b"\x01\x02\x03\x04"))
    )
    await asyncio.sleep(0)

    assert websocket.sent_bytes == []
    assert audio_task.done() is False

    release_connection.set()
    assert await audio_task is None
    assert websocket.sent_bytes == [b"\x01\x02\x03\x04"]
    await service._disconnect()


@pytest.mark.asyncio
async def test_mock_websocket_emits_deduplicated_partial_and_final_frames():
    service = _service(language_hints=["zh"])
    service._reset_task_state()
    task_id = service.task_id
    assert task_id is not None

    incoming = [
        _event(task_id, "task-started"),
        _result(task_id, "香", sentence_begin=True),
        _result(task_id, "香"),  # duplicate cumulative partial
        _result(task_id, "香港"),
        _result(task_id, "香港", sentence_end=True),
        _result(task_id, "香港", sentence_end=True),  # duplicate final
        _result(task_id, "", sentence_id=0, heartbeat=True),
        _event(task_id, "task-finished"),
    ]
    service._websocket = FakeWebSocket(json.dumps(item) for item in incoming)
    service.push_frame = AsyncMock()
    service.start_processing_metrics = AsyncMock()
    service.stop_processing_metrics = AsyncMock()
    service.emit_stt_usage_metrics = AsyncMock()

    await service._receive_messages()

    frames = [call.args[0] for call in service.push_frame.await_args_list]
    transcript_frames = [
        frame
        for frame in frames
        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
    ]
    assert [type(frame) for frame in transcript_frames] == [
        InterimTranscriptionFrame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
    ]
    assert [frame.text for frame in transcript_frames] == ["香", "香港", "香港"]
    assert all(frame.language is Language.ZH for frame in transcript_frames)
    # broadcast_frame emits one sibling upstream and one downstream.
    assert sum(isinstance(frame, UserStartedSpeakingFrame) for frame in frames) == 2
    assert sum(isinstance(frame, UserStoppedSpeakingFrame) for frame in frames) == 2
    assert service._task_started.is_set()
    assert service._task_finished.is_set()
    service.emit_stt_usage_metrics.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_is_not_suppressed_when_it_matches_last_partial():
    service = _service()
    service._reset_task_state()
    service.push_frame = AsyncMock()
    service.broadcast_frame = AsyncMock()
    service.emit_stt_usage_metrics = AsyncMock()
    service.stop_processing_metrics = AsyncMock()

    await service._handle_result_generated(
        _result(service.task_id, "風水", sentence_id=7)
    )
    await service._handle_result_generated(
        _result(service.task_id, "風水", sentence_id=7, sentence_end=True)
    )

    frames = [call.args[0] for call in service.push_frame.await_args_list]
    assert isinstance(frames[0], InterimTranscriptionFrame)
    assert isinstance(frames[1], TranscriptionFrame)
    assert frames[1].finalized is True
    assert service.broadcast_frame.await_args_list[0].args == (
        UserStartedSpeakingFrame,
    )
    assert service.broadcast_frame.await_args_list[1].args == (
        UserStoppedSpeakingFrame,
    )


@pytest.mark.asyncio
async def test_task_failed_emits_fatal_upstream_error_and_unblocks_waiters():
    service = _service()
    service._reset_task_state()
    service.push_frame = AsyncMock()

    should_continue = await service._handle_server_message(
        _event(
            service.task_id,
            "task-failed",
            error_code="CLIENT_ERROR",
            error_message="invalid vocabulary_id",
        )
    )

    assert should_continue is False
    assert isinstance(service._task_error, DashScopeSTTError)
    assert service._task_started.is_set()
    assert service._task_finished.is_set()
    frame, direction = service.push_frame.await_args.args
    assert isinstance(frame, ErrorFrame)
    assert frame.fatal is True
    assert "invalid vocabulary_id" in frame.error
    assert direction is FrameDirection.UPSTREAM


@pytest.mark.asyncio
async def test_invalid_json_from_websocket_emits_fatal_error_frame():
    service = _service()
    service._reset_task_state()
    service._websocket = FakeWebSocket(["not-json"])
    service.push_frame = AsyncMock()

    await service._receive_messages()

    frame, direction = service.push_frame.await_args.args
    assert isinstance(frame, ErrorFrame)
    assert frame.fatal is True
    assert "invalid JSON" in frame.error
    assert direction is FrameDirection.UPSTREAM
    assert service._task_started.is_set()
    assert service._task_finished.is_set()


@pytest.mark.asyncio
async def test_update_context_uses_continue_task_and_keeps_task_id():
    websocket = FakeWebSocket()
    service = _service()
    service._reset_task_state()
    service._websocket = websocket
    service._task_started.set()
    context = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "你想問居家還是流年？"}],
        }
    ]

    await service.update_context(context)

    message = websocket.sent_text[0]
    assert message["header"] == {
        "action": "continue-task",
        "task_id": service.task_id,
        "streaming": "duplex",
    }
    assert message["payload"]["input"]["context"] == context


@pytest.mark.asyncio
async def test_close_does_not_close_externally_owned_session():
    websocket = FakeWebSocket()
    session = FakeSession(websocket)
    service = _service(aiohttp_session=session)
    service._websocket = websocket

    await service._close_websocket()

    assert websocket.closed is True
    assert websocket.close_calls == [1000]
    assert session.closed is False


def test_segmentation_and_configuration_validation():
    with pytest.raises(ValueError, match="between 200 and 6000"):
        DashScopeSegmentation(max_sentence_silence=199)
    with pytest.raises(ValueError, match="workspace_id is required"):
        _service(base_url="wss://{WorkspaceId}.example/api-ws/v1/inference")
    with pytest.raises(ValueError, match="Unsupported DashScope audio format"):
        _service(format="flac")
