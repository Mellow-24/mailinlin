"""Unit tests for MiniStream's raw WebSocket streaming TTS service.

These tests use only in-memory socket and decoder doubles.  They deliberately
exercise the boundary where provider MP3 chunks become Pipecat PCM frames, so
the service never needs a real MiniStream credential or network connection.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame

from api.services.ministream.tts import MiniStreamTTSService


_CLOSE = object()
_TOKEN = "secret-token-that-must-not-leak"
_TRIAL_TLS_FINGERPRINT_SHA256 = (
    "9DC0B038C26CF47B35748C4717A50529A9D80636C21E9E170CCB24233144E4FD"
)
_TRIAL_WEBSOCKET_URL = "wss://120.209.217.11:30700/ministream-ws/tts"


class FakeWebSocket:
    """Small aiohttp-compatible WebSocket double backed by an async queue."""

    def __init__(self) -> None:
        self.closed = False
        self.close_code: int | None = None
        self.sent_json: list[dict[str, object]] = []
        self._messages: asyncio.Queue[object] = asyncio.Queue()

    async def send_json(self, message: dict[str, object]) -> None:
        if self.closed:
            raise ConnectionError("fake socket is closed")
        self.sent_json.append(message)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self._messages.put(_CLOSE)

    def exception(self):
        return None

    async def feed_json(self, message: dict[str, object]) -> None:
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


class FakeSession:
    """Caller-owned session double that records the WebSocket handshake."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self.closed = False
        self.websocket = websocket
        self._connection_results: list[FakeWebSocket | BaseException] = []
        self.connect_calls: list[tuple[str, dict[str, object]]] = []

    def queue_connection_result(self, result: FakeWebSocket | BaseException) -> None:
        self._connection_results.append(result)

    async def ws_connect(self, url: str, **kwargs):
        self.connect_calls.append((url, kwargs))
        result = (
            self._connection_results.pop(0)
            if self._connection_results
            else self.websocket
        )
        if isinstance(result, BaseException):
            raise result
        self.websocket = result
        return result

    async def close(self) -> None:
        self.closed = True


class _FakeDecoderStdout:
    def __init__(self) -> None:
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()

    async def read(self, _size: int) -> bytes:
        return await self._chunks.get()

    def feed(self, chunk: bytes) -> None:
        self._chunks.put_nowait(chunk)

    def finish(self) -> None:
        self._chunks.put_nowait(b"")


class _FakeDecoderStdin:
    def __init__(self, decoder: "FakeDecoder") -> None:
        self._decoder = decoder
        self.closed = False
        self.writes: list[bytes] = []

    def write(self, chunk: bytes) -> None:
        self.writes.append(chunk)
        # A fake incremental decoder: one MP3 input chunk immediately makes one
        # signed-16 PCM chunk available to the decoder stdout reader.
        self._decoder.stdout.feed(b"\x01\x02\x03\x04")

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._decoder.stdout.finish()

    async def wait_closed(self) -> None:
        return None


class FakeDecoder:
    """Minimal subprocess-shaped incremental MP3 decoder double."""

    def __init__(self) -> None:
        self.stdout = _FakeDecoderStdout()
        self.stdin = _FakeDecoderStdin(self)
        self.returncode: int | None = None
        self.terminated = False

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.stdin.close()

    def kill(self) -> None:
        self.terminate()


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1.0):
        while not predicate():
            await asyncio.sleep(0)


async def _request(service: MiniStreamTTSService, text: str, context_id: str) -> None:
    results = [frame async for frame in service.run_tts(text, context_id)]
    assert results == [None]


class _RetryableHandshakeFailure(RuntimeError):
    """Small status-bearing failure shaped like aiohttp's handshake error."""

    status = 429


def _service(
    websocket: FakeWebSocket,
    *,
    decoder_factory: Callable[[], FakeDecoder] | None = None,
    websocket_url: str = "wss://ministream.test/ministream-ws/tts",
    verify_ssl: bool = True,
    tls_certificate_fingerprint_sha256: str | None = None,
) -> tuple[MiniStreamTTSService, FakeSession]:
    service_kwargs = dict(
        api_key=_TOKEN,
        model="ministream-tts",
        websocket_url=websocket_url,
        language="yue",
        generation_mode="preset_voice",
        voice_preset_key="mailinlin",
        buffer="off",
        buffer_idle_ms=250,
        max_generate_length=500,
        sample_rate=16_000,
        playback_rate=1.5,
        verify_ssl=verify_ssl,
        aiohttp_session=FakeSession(websocket),
        decoder_factory=decoder_factory,
    )
    if tls_certificate_fingerprint_sha256 is not None:
        service_kwargs["tls_certificate_fingerprint_sha256"] = (
            tls_certificate_fingerprint_sha256
        )
    service = MiniStreamTTSService(**service_kwargs)
    # These tests exercise the provider protocol directly, without a complete
    # Pipecat pipeline/StartFrame.  The service normally receives these hooks
    # from the base class when the pipeline creates its audio context.
    service._sample_rate = 16_000
    service.audio_context_available = lambda _context_id: True
    service.append_to_audio_context = AsyncMock()
    service.remove_audio_context = AsyncMock()
    service.start_tts_usage_metrics = AsyncMock()
    service.stop_all_metrics = AsyncMock()
    return service, service._session


def test_service_settings_are_complete_before_pipecat_startup() -> None:
    service, _session = _service(FakeWebSocket())

    assert service._settings.model == "ministream-tts"
    assert service._settings.voice is None
    assert service._settings.language is None


@pytest.mark.anyio
async def test_prime_opens_and_reuses_the_authenticated_websocket() -> None:
    websocket = FakeWebSocket()
    service, session = _service(websocket)
    try:
        await service.prime()
        await service.prime()

        assert len(session.connect_calls) == 1
        _url, kwargs = session.connect_calls[0]
        assert kwargs["headers"]["Authorization"] == f"Bearer {_TOKEN}"
    finally:
        await service._disconnect()


def test_service_rejects_unverified_tls_even_for_the_trial_endpoint() -> None:
    with pytest.raises(ValueError, match="verify_ssl=True"):
        _service(FakeWebSocket(), verify_ssl=False)


@pytest.mark.anyio
async def test_fixed_trial_websocket_uses_the_configured_certificate_pin() -> None:
    websocket = FakeWebSocket()
    service, session = _service(
        websocket,
        websocket_url=_TRIAL_WEBSOCKET_URL,
        tls_certificate_fingerprint_sha256=_TRIAL_TLS_FINGERPRINT_SHA256,
    )
    try:
        await service.prime()

        tls_policy = session.connect_calls[0][1]["ssl"]
        assert isinstance(tls_policy, aiohttp.Fingerprint)
        assert tls_policy.fingerprint == bytes.fromhex(_TRIAL_TLS_FINGERPRINT_SHA256)
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_1013_waits_before_later_synthesis_without_replaying_started_request() -> (
    None
):
    first_socket = FakeWebSocket()
    replacement_socket = FakeWebSocket()
    service, session = _service(first_socket, decoder_factory=FakeDecoder)
    backoff_delays: list[float] = []

    async def record_backoff(delay: float) -> None:
        backoff_delays.append(delay)

    service._sleep = record_backoff
    try:
        await _request(service, "第一句已开始。", "ctx-1")
        first_request_id = str(first_socket.sent_json[0]["request_id"])
        await first_socket.feed_json({"type": "start", "request_id": first_request_id})
        await _wait_until(lambda: service._requests[first_request_id].provider_started)

        session.queue_connection_result(replacement_socket)
        first_socket.close_code = 1013
        await first_socket.close()
        await _wait_until(lambda: service._websocket is None)
        await _wait_until(lambda: service.remove_audio_context.await_count == 1)

        await _request(service, "第二句稍后发送。", "ctx-2")

        assert backoff_delays == [0.25]
        assert len(session.connect_calls) == 2
        assert [message["text"] for message in first_socket.sent_json] == [
            "第一句已开始。"
        ]
        assert [message["text"] for message in replacement_socket.sent_json] == [
            "第二句稍后发送。"
        ]
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_handshake_429_uses_a_capped_backoff_for_later_requests() -> None:
    websocket = FakeWebSocket()
    service, session = _service(websocket)
    for _ in range(4):
        session.queue_connection_result(_RetryableHandshakeFailure("busy"))
    backoff_delays: list[float] = []

    async def record_backoff(delay: float) -> None:
        backoff_delays.append(delay)

    service._sleep = record_backoff
    try:
        for request_number in range(5):
            await _request(
                service,
                f"第{request_number + 1}句。",
                f"ctx-{request_number + 1}",
            )

        assert backoff_delays == [0.25, 0.5, 1.0, 1.0]
        assert len(session.connect_calls) == 5
        assert [message["text"] for message in websocket.sent_json] == ["第5句。"]
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_connection_failure_does_not_chain_provider_details_into_tracebacks() -> (
    None
):
    service, session = _service(FakeWebSocket())
    session.ws_connect = AsyncMock(
        side_effect=RuntimeError(f"provider leaked {_TOKEN}")
    )
    try:
        with pytest.raises(ConnectionError) as raised:
            await service._connect()

        assert _TOKEN not in str(raised.value)
        assert raised.value.__cause__ is None
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_authentication_header_and_synthesis_payload_keep_token_out_of_url() -> (
    None
):
    websocket = FakeWebSocket()
    service, session = _service(websocket)
    try:
        await _request(service, "你好。", "ctx-1")

        assert len(session.connect_calls) == 1
        websocket_url, kwargs = session.connect_calls[0]
        parsed_url = urlsplit(websocket_url)
        assert _TOKEN not in websocket_url
        assert parsed_url.path.startswith("/ministream-ws/tts/")
        assert parsed_url.path.rsplit("/", 1)[-1]
        assert parse_qs(parsed_url.query) == {
            "language": ["yue"],
            "generation_mode": ["preset_voice"],
            "voice_preset_key": ["mailinlin"],
            "buffer": ["off"],
            "buffer_idle_ms": ["250"],
            "max_generate_length": ["500"],
        }
        assert kwargs["headers"] == {
            "Authorization": f"Bearer {_TOKEN}",
            "User-Agent": "Dograh-MiniStream-TTS/1.0",
        }
        assert kwargs["ssl"] is True
        assert websocket.sent_json[0]["type"] == "synthesize"
        assert websocket.sent_json[0]["text"] == "你好。"
        assert websocket.sent_json[0]["request_id"]
        assert _TOKEN not in json.dumps(websocket.sent_json[0])
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_binary_mp3_is_emitted_as_pcm_before_provider_end() -> None:
    websocket = FakeWebSocket()
    decoders: list[FakeDecoder] = []
    service, _session = _service(
        websocket,
        decoder_factory=lambda: decoders.append(FakeDecoder()) or decoders[-1],
    )
    try:
        await _request(service, "第一句。", "ctx-1")
        request_id = str(websocket.sent_json[0]["request_id"])

        await websocket.feed_json({"type": "start", "request_id": request_id})
        await websocket.feed_binary(b"mp3-chunk")
        await _wait_until(lambda: service.append_to_audio_context.await_count == 1)

        frame = service.append_to_audio_context.await_args.args[1]
        assert isinstance(frame, TTSAudioRawFrame)
        assert frame.audio == b"\x01\x02\x03\x04"
        assert frame.sample_rate == 16_000
        assert frame.num_channels == 1
        assert frame.context_id == "ctx-1"
        assert not decoders[0].stdin.closed
        assert request_id in service._requests

        await websocket.feed_json({"type": "end", "request_id": request_id})
        await _wait_until(lambda: request_id not in service._requests)
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_interruption_closes_socket_before_late_binary_audio_can_leak() -> None:
    websocket = FakeWebSocket()
    decoders: list[FakeDecoder] = []
    service, _session = _service(
        websocket,
        decoder_factory=lambda: decoders.append(FakeDecoder()) or decoders[-1],
    )
    try:
        await _request(service, "會被打斷。", "ctx-1")
        request_id = str(websocket.sent_json[0]["request_id"])
        await websocket.feed_json({"type": "start", "request_id": request_id})
        await _wait_until(lambda: bool(decoders))

        await service.on_audio_context_interrupted("ctx-1")
        await websocket.feed_binary(b"late-mp3")
        await asyncio.sleep(0)

        assert websocket.closed
        assert decoders[0].terminated
        assert request_id not in service._requests
        service.append_to_audio_context.assert_not_awaited()
        service.remove_audio_context.assert_not_awaited()
        service.stop_all_metrics.assert_awaited_once()
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_cancelled_send_disposes_a_decoder_that_started_concurrently() -> None:
    websocket = FakeWebSocket()
    decoders: list[FakeDecoder] = []
    service, _session = _service(
        websocket,
        decoder_factory=lambda: decoders.append(FakeDecoder()) or decoders[-1],
    )
    metrics_blocked = asyncio.Event()

    async def block_metrics(_text: str) -> None:
        await metrics_blocked.wait()

    service.start_tts_usage_metrics = block_metrics
    generator = service.run_tts("取消途中。", "ctx-1")
    try:
        pending = asyncio.create_task(anext(generator))
        await _wait_until(lambda: len(websocket.sent_json) == 1)
        request_id = str(websocket.sent_json[0]["request_id"])
        await websocket.feed_json({"type": "start", "request_id": request_id})
        await _wait_until(lambda: bool(decoders))

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

        assert request_id not in service._requests
        assert decoders[0].terminated
    finally:
        metrics_blocked.set()
        await generator.aclose()
        await service._disconnect()


@pytest.mark.anyio
async def test_flushed_context_waits_for_all_its_requests_before_stopping() -> None:
    websocket = FakeWebSocket()
    service, _session = _service(websocket, decoder_factory=FakeDecoder)
    try:
        await _request(service, "第一句。", "ctx-1")
        first_id = str(websocket.sent_json[0]["request_id"])
        await _request(service, "第二句。", "ctx-1")
        second_id = next(iter(service._context_requests["ctx-1"] - {first_id}))
        assert [message["request_id"] for message in websocket.sent_json] == [first_id]

        await service.flush_audio("ctx-1")
        await websocket.feed_json({"type": "start", "request_id": first_id})
        await websocket.feed_json({"type": "end", "request_id": first_id})
        await _wait_until(lambda: first_id not in service._requests)
        await _wait_until(lambda: len(websocket.sent_json) == 2)

        service.remove_audio_context.assert_not_awaited()
        assert not any(
            isinstance(call.args[1], TTSStoppedFrame)
            for call in service.append_to_audio_context.await_args_list
        )

        await websocket.feed_json({"type": "start", "request_id": second_id})
        await websocket.feed_json({"type": "end", "request_id": second_id})
        await _wait_until(lambda: second_id not in service._requests)
        service.remove_audio_context.assert_awaited_once_with("ctx-1")
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_provider_error_is_sanitized_before_it_reaches_the_audio_context() -> (
    None
):
    websocket = FakeWebSocket()
    service, _session = _service(websocket, decoder_factory=FakeDecoder)
    try:
        await _request(service, "會失敗。", "ctx-1")
        request_id = str(websocket.sent_json[0]["request_id"])

        await websocket.feed_json(
            {
                "type": "error",
                "request_id": request_id,
                "message": f"provider diagnostic accidentally echoed {_TOKEN}",
            }
        )
        await _wait_until(lambda: service.remove_audio_context.await_count == 1)

        error_frames = [
            call.args[1]
            for call in service.append_to_audio_context.await_args_list
            if isinstance(call.args[1], ErrorFrame)
        ]
        assert len(error_frames) == 1
        assert _TOKEN not in error_frames[0].error
        assert _TOKEN not in str(error_frames[0].exception)
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_provider_error_without_request_id_fails_pending_contexts_safely() -> (
    None
):
    websocket = FakeWebSocket()
    service, _session = _service(websocket, decoder_factory=FakeDecoder)
    try:
        await _request(service, "會失敗。", "ctx-1")

        await websocket.feed_json(
            {"type": "error", "message": f"provider diagnostic echoed {_TOKEN}"}
        )
        await _wait_until(lambda: service.remove_audio_context.await_count == 1)

        error_frame = next(
            call.args[1]
            for call in service.append_to_audio_context.await_args_list
            if isinstance(call.args[1], ErrorFrame)
        )
        assert _TOKEN not in error_frame.error
        assert _TOKEN not in str(error_frame.exception)
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_audio_context_completion_discards_a_silent_request_before_reuse() -> (
    None
):
    """A late provider start must not revive Pipecat's timed-out context."""
    websocket = FakeWebSocket()
    decoders: list[FakeDecoder] = []
    service, _session = _service(
        websocket,
        decoder_factory=lambda: decoders.append(FakeDecoder()) or decoders[-1],
    )
    available_contexts = {"ctx-1"}
    service.audio_context_available = lambda context_id: (
        context_id in available_contexts
    )
    try:
        await _request(service, "服務端沒有回應。", "ctx-1")
        request_id = str(websocket.sent_json[0]["request_id"])

        # Pipecat removes the context before calling this completion callback.
        available_contexts.clear()
        await service.on_audio_context_completed("ctx-1")

        # A later turn may recreate the same context ID.  The old request and
        # unaddressable socket stream must already be gone before that happens.
        available_contexts.add("ctx-1")
        await websocket.feed_json({"type": "start", "request_id": request_id})
        await websocket.feed_binary(b"late-mp3")
        await asyncio.sleep(0)

        assert websocket.closed
        assert request_id not in service._requests
        assert decoders == []
        service.append_to_audio_context.assert_not_awaited()
        service.stop_all_metrics.assert_awaited_once()
    finally:
        await service._disconnect()


@pytest.mark.anyio
async def test_queued_request_start_cannot_steal_active_binary_audio() -> None:
    """Only the sent FIFO head may claim MiniStream's untagged MP3 chunks."""
    websocket = FakeWebSocket()
    service, _session = _service(websocket, decoder_factory=FakeDecoder)
    try:
        await _request(service, "第一句。", "ctx-a")
        first_id = str(websocket.sent_json[0]["request_id"])

        await _request(service, "第二句。", "ctx-b")
        second_id = next(iter(service._context_requests["ctx-b"]))

        # MiniStream's binary chunks do not carry a request ID.  Even if a
        # second start event appears before the first end, it cannot switch the
        # active binary route away from the FIFO head.
        await websocket.feed_json({"type": "start", "request_id": first_id})
        await websocket.feed_json({"type": "start", "request_id": second_id})
        await websocket.feed_binary(b"first-request-mp3")
        await _wait_until(lambda: service.append_to_audio_context.await_count == 1)

        first_frame = service.append_to_audio_context.await_args.args[1]
        assert isinstance(first_frame, TTSAudioRawFrame)
        assert first_frame.context_id == "ctx-a"
        assert [message["request_id"] for message in websocket.sent_json] == [first_id]

        await websocket.feed_json({"type": "end", "request_id": first_id})
        await _wait_until(lambda: first_id not in service._requests)
        await _wait_until(lambda: len(websocket.sent_json) == 2)
        assert websocket.sent_json[1]["request_id"] == second_id
    finally:
        await service._disconnect()
