"""Streaming speech recognition through Alibaba Cloud Model Studio.

This module implements the raw DashScope inference WebSocket protocol used by
``fun-asr-realtime`` and ``qwen-audio-3.0-asr-flash-streaming``.  It deliberately
uses :mod:`aiohttp`, which is already part of Dograh's runtime dependency tree,
instead of requiring the DashScope SDK.

Protocol reference:
https://help.aliyun.com/en/model-studio/fun-asr-client-events
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

DEFAULT_DASHSCOPE_WEBSOCKET_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
SUPPORTED_AUDIO_FORMATS = frozenset(
    {"pcm", "wav", "mp3", "opus", "speex", "aac", "amr"}
)


class DashScopeSTTError(RuntimeError):
    """Raised when DashScope rejects or unexpectedly terminates an ASR task."""


@dataclass(frozen=True, slots=True)
class DashScopeSegmentation:
    """Server-side sentence segmentation controls.

    The default favors interactive voice-agent latency: VAD segmentation with
    800 ms of trailing silence.  Set ``semantic_punctuation_enabled`` for
    meeting-style, punctuation-aware segmentation instead.

    Args:
        semantic_punctuation_enabled: Use semantic punctuation rather than VAD
            as the sentence boundary signal.
        max_sentence_silence: VAD silence threshold in milliseconds. DashScope
            accepts values from 200 through 6000.
        multi_threshold_mode_enabled: Prevent very long VAD segments by using
            multiple thresholds. It applies only to VAD segmentation.
    """

    semantic_punctuation_enabled: bool = False
    max_sentence_silence: int = 800
    multi_threshold_mode_enabled: bool = False

    def __post_init__(self) -> None:
        if not 200 <= self.max_sentence_silence <= 6000:
            raise ValueError("max_sentence_silence must be between 200 and 6000 ms")

    def as_parameters(self) -> dict[str, bool | int]:
        """Return the fields expected in ``run-task.payload.parameters``."""

        return {
            "semantic_punctuation_enabled": self.semantic_punctuation_enabled,
            "max_sentence_silence": self.max_sentence_silence,
            "multi_threshold_mode_enabled": self.multi_threshold_mode_enabled,
        }


@dataclass
class DashScopeSTTSettings(STTSettings):
    """Pipecat settings type for :class:`DashScopeSTTService`."""


def _resolve_websocket_url(base_url: str | None, workspace_id: str | None) -> str:
    """Resolve a configured workspace placeholder without guessing its region."""

    url = (base_url or DEFAULT_DASHSCOPE_WEBSOCKET_URL).strip()
    if not url:
        raise ValueError("DashScope WebSocket URL must not be empty")

    placeholders = ("{workspace_id}", "{WorkspaceId}", "{WORKSPACE_ID}")
    if any(token in url for token in placeholders):
        if not workspace_id or not workspace_id.strip():
            raise ValueError(
                "workspace_id is required when the DashScope WebSocket URL "
                "contains a workspace placeholder"
            )
        value = workspace_id.strip()
        for token in placeholders:
            url = url.replace(token, value)

    if not url.startswith(("ws://", "wss://")):
        raise ValueError("DashScope WebSocket URL must use ws:// or wss://")
    return url


def _normalize_language_hints(model: str, hints: Sequence[str] | None) -> list[str]:
    """Normalize language hints to the limits documented for each model family."""

    normalized = list(
        dict.fromkeys(hint.strip() for hint in (hints or []) if hint.strip())
    )
    if model.startswith("fun-asr-"):
        return normalized[:1]
    if model.startswith("qwen-audio-"):
        return normalized[:4]
    return normalized


def _copy_context(context: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Copy context so callers cannot mutate an in-flight task request."""

    if context is None:
        return []
    if isinstance(context, (str, bytes, bytearray)):
        raise TypeError("context must be a sequence of message mappings")
    copied: list[dict[str, Any]] = []
    for message in context:
        if not isinstance(message, Mapping):
            raise TypeError("each context message must be a mapping")
        copied.append(copy.deepcopy(dict(message)))
    # Fail early with a useful configuration error rather than inside send_str.
    try:
        json.dumps(copied, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("context must be JSON serializable") from exc
    return copied


class DashScopeSTTService(STTService):
    """Pipecat STT service for DashScope streaming speech recognition.

    Audio is sent as binary WebSocket frames after DashScope acknowledges the
    ``run-task`` command.  Cumulative partial hypotheses are deduplicated per
    ``sentence_id``; a final result is still emitted even when it has exactly
    the same text as the last partial hypothesis.
    """

    Settings = DashScopeSTTSettings

    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str | None = None,
        base_url: str | None = None,
        model: str = "fun-asr-realtime",
        sample_rate: int = 16000,
        format: str = "pcm",
        language_hints: Sequence[str] | None = None,
        vocabulary_id: str | None = None,
        context: Sequence[Mapping[str, Any]] | None = None,
        segmentation: DashScopeSegmentation | None = None,
        startup_timeout: float = 10.0,
        finish_timeout: float = 5.0,
        aiohttp_session: aiohttp.ClientSession | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the DashScope streaming ASR service.

        Args:
            api_key: Alibaba Cloud Model Studio API key.
            workspace_id: Workspace ID used to fill a placeholder in
                ``base_url``. It is ignored when the URL has no placeholder.
            base_url: Full ``api-ws/v1/inference`` URL. Region-specific URLs may
                contain ``{workspace_id}`` or ``{WorkspaceId}``.
            model: DashScope streaming ASR model ID, including snapshot IDs such
                as ``fun-asr-realtime-2026-02-28``.
            sample_rate: Input audio sample rate in Hz.
            format: Input audio container/codec. Dograh normally sends raw PCM.
            language_hints: Optional ISO language hints. Fun-ASR accepts one and
                Qwen Audio accepts up to four; excess hints are truncated.
            vocabulary_id: Optional precompiled DashScope hot-word list ID.
            context: Optional ASR conversation context messages.
            segmentation: Optional sentence segmentation controls.
            startup_timeout: Seconds to wait for ``task-started``.
            finish_timeout: Seconds to wait for ``task-finished`` after ending.
            aiohttp_session: Optional externally managed session, primarily for
                dependency injection and tests.
            **kwargs: Additional arguments forwarded to Pipecat ``STTService``.
        """

        if not api_key or not api_key.strip():
            raise ValueError("DashScope API key must not be empty")
        if not model or not model.strip():
            raise ValueError("DashScope STT model must not be empty")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")

        audio_format = format.strip().lower()
        if audio_format not in SUPPORTED_AUDIO_FORMATS:
            allowed = ", ".join(sorted(SUPPORTED_AUDIO_FORMATS))
            raise ValueError(
                f"Unsupported DashScope audio format '{format}'. Allowed: {allowed}"
            )
        if startup_timeout <= 0 or finish_timeout <= 0:
            raise ValueError(
                "startup_timeout and finish_timeout must be greater than zero"
            )

        resolved_model = model.strip()
        resolved_hints = _normalize_language_hints(resolved_model, language_hints)
        settings = DashScopeSTTSettings(
            model=resolved_model,
            language=resolved_hints[0] if resolved_hints else None,
        )
        super().__init__(sample_rate=sample_rate, settings=settings, **kwargs)

        self._api_key = api_key.strip()
        self._workspace_id = workspace_id.strip() if workspace_id else None
        self._base_url = _resolve_websocket_url(base_url, self._workspace_id)
        self._model = resolved_model
        self._configured_sample_rate = sample_rate
        self._audio_format = audio_format
        self._language_hints = resolved_hints
        self._vocabulary_id = vocabulary_id.strip() if vocabulary_id else None
        self._context = _copy_context(context)
        self._segmentation = segmentation or DashScopeSegmentation()
        self._startup_timeout = startup_timeout
        self._finish_timeout = finish_timeout

        self._session = aiohttp_session
        self._owns_session = aiohttp_session is None
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._receive_task: asyncio.Task[Any] | None = None
        self._connect_task: asyncio.Task[Any] | None = None
        self._deferred_start = False
        self._send_lock = asyncio.Lock()
        self._task_id: str | None = None
        self._task_started = asyncio.Event()
        self._task_finished = asyncio.Event()
        self._task_error: DashScopeSTTError | None = None
        self._finish_sent = False
        self._closing = False
        self._connected = False
        self._send_error_reported = False

        self._last_interim_by_sentence: dict[str, str] = {}
        self._last_final_by_sentence: dict[str, str] = {}
        self._external_user_turn_open = False

    @property
    def task_id(self) -> str | None:
        """Return the current DashScope task ID, primarily for observability."""

        return self._task_id

    @property
    def audio_format(self) -> str:
        """Return the configured DashScope input audio format."""

        return self._audio_format

    def _is_websocket_open(self) -> bool:
        return self._websocket is not None and not self._websocket.closed

    def enable_deferred_start(self) -> None:
        """Let StartFrame continue while this push-to-talk STT connects.

        ``run_stt`` still waits for the connection task before accepting the
        first audio frame, so audio is never knowingly sent to an unready
        provider. This mode is enabled only for server-approved headless voice
        embeds whose microphone remains muted during the opening greeting.
        """

        self._deferred_start = True

    async def _connect_in_background(self) -> None:
        try:
            await self._connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # _connect already emits a fatal ErrorFrame. Consume the task
            # exception here so an abandoned opening cannot leak a background
            # task warning; the pipeline observer remains the error channel.
            logger.warning(f"Deferred DashScope STT startup failed: {exc}")

    async def _cancel_connect_task(self) -> None:
        task = self._connect_task
        self._connect_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            await self.cancel_task(task)

    def _reset_task_state(self) -> None:
        self._task_id = uuid.uuid4().hex
        self._task_started.clear()
        self._task_finished.clear()
        self._task_error = None
        self._finish_sent = False
        self._closing = False
        self._send_error_reported = False
        self._last_interim_by_sentence.clear()
        self._last_final_by_sentence.clear()
        self._external_user_turn_open = False

    def _build_run_task_message(self) -> dict[str, Any]:
        if not self._task_id:
            raise RuntimeError("DashScope task state has not been initialized")

        parameters: dict[str, Any] = {
            "format": self._audio_format,
            "sample_rate": self.sample_rate or self._configured_sample_rate,
            # DashScope heartbeat packets keep long, silent voice sessions open.
            "heartbeat": True,
            **self._segmentation.as_parameters(),
        }
        if self._language_hints:
            parameters["language_hints"] = self._language_hints
        if self._vocabulary_id:
            parameters["vocabulary_id"] = self._vocabulary_id

        input_payload: dict[str, Any] = {}
        if self._context:
            input_payload["context"] = copy.deepcopy(self._context)

        return {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._model,
                "parameters": parameters,
                "input": input_payload,
            },
        }

    def _build_finish_task_message(self) -> dict[str, Any]:
        if not self._task_id:
            raise RuntimeError("DashScope task state has not been initialized")
        return {
            "header": {
                "action": "finish-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }

    async def _send_json(self, message: Mapping[str, Any]) -> None:
        if not self._is_websocket_open():
            raise ConnectionError("DashScope WebSocket is not connected")
        assert self._websocket is not None
        await self._websocket.send_str(
            json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        )

    async def _open_websocket(self) -> None:
        if self._is_websocket_open():
            return
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        logger.debug(f"Connecting to DashScope STT WebSocket at {self._base_url}")
        headers = {"Authorization": f"bearer {self._api_key}"}
        if self._workspace_id:
            # Legacy/shared DashScope endpoints select the workspace through
            # this header. Workspace-specific hostnames accept it as well.
            headers["X-DashScope-WorkSpace"] = self._workspace_id
        self._websocket = await self._session.ws_connect(
            self._base_url,
            headers=headers,
            heartbeat=20.0,
        )

    async def _connect(self) -> None:
        self._reset_task_state()
        try:
            await self._open_websocket()
            self._receive_task = self.create_task(
                self._receive_messages(), name="dashscope_stt_receive"
            )
            await self._send_json(self._build_run_task_message())
            await asyncio.wait_for(
                self._task_started.wait(), timeout=self._startup_timeout
            )
            if self._task_error:
                raise self._task_error
            self._connected = True
            await self._call_event_handler("on_connected")
            logger.info(f"DashScope STT task started (model={self._model})")
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, DashScopeSTTError)
                else DashScopeSTTError(str(exc))
            )
            if self._task_error is None:
                self._task_error = error
                await self._emit_error(
                    "Failed to start DashScope STT task", error, fatal=True
                )
            await self._disconnect()
            raise error from exc

    async def _close_websocket(self) -> None:
        websocket = self._websocket
        self._websocket = None
        if websocket is not None and not websocket.closed:
            try:
                await websocket.close(code=1000)
            except Exception as exc:  # noqa: BLE001 - cleanup must remain best-effort
                logger.debug(f"Error closing DashScope STT WebSocket: {exc}")

        if self._owns_session and self._session is not None:
            session = self._session
            self._session = None
            if not session.closed:
                try:
                    await session.close()
                except Exception as exc:  # noqa: BLE001 - cleanup must remain best-effort
                    logger.debug(f"Error closing DashScope aiohttp session: {exc}")

    async def _disconnect(self) -> None:
        self._closing = True
        receive_task = self._receive_task
        self._receive_task = None
        if receive_task is not None and receive_task is not asyncio.current_task():
            await self.cancel_task(receive_task)
        await self._close_websocket()
        if self._connected:
            self._connected = False
            await self._call_event_handler("on_disconnected")

    async def _finish_task(self) -> None:
        if not self._task_started.is_set() or not self._is_websocket_open():
            return

        try:
            async with self._send_lock:
                if not self._finish_sent:
                    self._finish_sent = True
                    await self._send_json(self._build_finish_task_message())
            await asyncio.wait_for(
                self._task_finished.wait(), timeout=self._finish_timeout
            )
        except TimeoutError as exc:
            await self._emit_error(
                "Timed out waiting for DashScope task-finished", exc, fatal=False
            )
        except Exception as exc:  # noqa: BLE001 - provider send failures vary by transport
            await self._emit_error(
                "Failed to finish DashScope STT task", exc, fatal=False
            )

    async def update_context(self, context: Sequence[Mapping[str, Any]] | None) -> None:
        """Update context for the current task using ``continue-task``.

        When called before the service starts, the context is simply retained
        for the next ``run-task`` request.
        """

        self._context = _copy_context(context)
        if not self._task_started.is_set() or not self._is_websocket_open():
            return
        if not self._task_id:
            return
        message = {
            "header": {
                "action": "continue-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "input": {"context": copy.deepcopy(self._context)},
            },
        }
        async with self._send_lock:
            await self._send_json(message)

    async def _receive_messages(self) -> None:
        websocket = self._websocket
        if websocket is None:
            return

        try:
            async for message in websocket:
                if message.type is aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise DashScopeSTTError(
                            "DashScope returned an invalid JSON event"
                        ) from exc
                    should_continue = await self._handle_server_message(payload)
                    if not should_continue:
                        return
                elif message.type is aiohttp.WSMsgType.ERROR:
                    exception = websocket.exception()
                    raise DashScopeSTTError(
                        f"DashScope WebSocket error: {exception or 'unknown error'}"
                    )
                elif message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                }:
                    break
                # DashScope ASR server events are JSON text messages. Ping,
                # pong, and any unexpected binary messages require no action.
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - receive failures vary by aiohttp transport
            error = (
                exc
                if isinstance(exc, DashScopeSTTError)
                else DashScopeSTTError(str(exc))
            )
            self._task_error = error
            self._task_started.set()
            self._task_finished.set()
            if not self._closing:
                await self._emit_error(
                    "DashScope STT receive loop failed", error, fatal=True
                )
            return

        if not self._closing and not self._task_finished.is_set():
            error = DashScopeSTTError("DashScope STT WebSocket closed unexpectedly")
            self._task_error = error
            self._task_started.set()
            self._task_finished.set()
            await self._emit_error(str(error), error, fatal=True)

    async def _handle_server_message(self, message: Mapping[str, Any]) -> bool:
        header = message.get("header")
        if not isinstance(header, Mapping):
            raise DashScopeSTTError("DashScope event is missing its header")

        event = header.get("event")
        event_task_id = header.get("task_id")
        if event_task_id and self._task_id and event_task_id != self._task_id:
            logger.warning(
                "Ignoring DashScope STT event for a stale or unknown task ID"
            )
            return True

        if event == "task-started":
            self._task_started.set()
            return True

        if event == "result-generated":
            await self._handle_result_generated(message)
            return True

        if event == "task-finished":
            self._task_finished.set()
            return False

        if event == "task-failed":
            code = str(header.get("error_code") or "UNKNOWN_ERROR")
            detail = str(header.get("error_message") or "Unknown DashScope error")
            error = DashScopeSTTError(f"DashScope STT task failed [{code}]: {detail}")
            self._task_error = error
            # Unblock both startup and graceful-stop waiters.
            self._task_started.set()
            self._task_finished.set()
            await self._emit_error(str(error), error, fatal=True)
            return False

        logger.debug(f"Ignoring unknown DashScope STT event: {event!r}")
        return True

    def _result_language(self) -> Language | None:
        for hint in self._language_hints:
            try:
                return Language(hint)
            except ValueError:
                continue
        return None

    @staticmethod
    def _sentence_key(sentence: Mapping[str, Any]) -> str:
        sentence_id = sentence.get("sentence_id")
        return (
            str(sentence_id) if sentence_id is not None else "__missing_sentence_id__"
        )

    async def _handle_result_generated(self, message: Mapping[str, Any]) -> None:
        payload = message.get("payload")
        if not isinstance(payload, Mapping):
            return
        output = payload.get("output")
        if not isinstance(output, Mapping):
            return
        sentence = output.get("sentence")
        if not isinstance(sentence, Mapping) or sentence.get("heartbeat") is True:
            return

        if sentence.get("sentence_begin") is True:
            await self.start_processing_metrics()

        text = sentence.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        transcript = text.strip()
        sentence_key = self._sentence_key(sentence)
        is_final = sentence.get("sentence_end") is True
        language = self._result_language()
        raw_result = copy.deepcopy(dict(message))

        if is_final:
            if self._last_final_by_sentence.get(sentence_key) == transcript:
                return
        elif self._last_interim_by_sentence.get(sentence_key) == transcript:
            return

        # DashScope's server-side sentence boundaries are more reliable for
        # interactive ASR than browser VAD in rooms with background speech.
        # Broadcast them as Pipecat external-turn frames so each finalized
        # sentence promptly becomes an LLM turn instead of waiting for a long
        # stretch of local silence.
        if not self._external_user_turn_open:
            self._external_user_turn_open = True
            await self.broadcast_frame(UserStartedSpeakingFrame)

        if is_final:
            self._last_final_by_sentence[sentence_key] = transcript
            self._last_interim_by_sentence.pop(sentence_key, None)
            await self.emit_stt_usage_metrics()
            await self.push_frame(
                TranscriptionFrame(
                    text=transcript,
                    user_id=self._user_id,
                    timestamp=time_now_iso8601(),
                    language=language,
                    result=raw_result,
                    finalized=True,
                )
            )
            await self.broadcast_frame(UserStoppedSpeakingFrame)
            self._external_user_turn_open = False
            await self.stop_processing_metrics()
            return

        self._last_interim_by_sentence[sentence_key] = transcript
        await self.push_frame(
            InterimTranscriptionFrame(
                text=transcript,
                user_id=self._user_id,
                timestamp=time_now_iso8601(),
                language=language,
                result=raw_result,
            )
        )

    async def _emit_error(
        self,
        message: str,
        exception: BaseException | None = None,
        *,
        fatal: bool,
    ) -> None:
        error = exception if isinstance(exception, Exception) else None
        frame = ErrorFrame(
            error=f"{message}: {exception}" if exception else message,
            fatal=fatal,
            processor=self,
            exception=error,
        )
        await self.push_frame(frame, FrameDirection.UPSTREAM)
        await self._call_event_handler("on_connection_error", frame.error)

    async def start(self, frame: StartFrame) -> None:
        """Open the WebSocket and wait until DashScope accepts the task."""

        await super().start(frame)
        if self._deferred_start:
            self._connect_task = self.create_task(
                self._connect_in_background(), name="dashscope_stt_connect"
            )
            return
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        """Flush final recognition results, then close owned resources."""

        await self._cancel_connect_task()
        await self._finish_task()
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame) -> None:
        """Immediately cancel the receive task and close the WebSocket."""

        await self._cancel_connect_task()
        await super().cancel(frame)
        await self._disconnect()

    async def cleanup(self) -> None:
        """Idempotently release WebSocket and session resources."""

        await self._cancel_connect_task()
        await self._disconnect()
        await super().cleanup()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Send a chunk of raw input audio as one binary WebSocket frame."""

        if not audio:
            yield None
            return
        if self._deferred_start and self._connect_task is not None:
            await self._connect_task
        if (
            self._finish_sent
            or not self._task_started.is_set()
            or not self._is_websocket_open()
        ):
            if not self._send_error_reported:
                self._send_error_reported = True
                yield ErrorFrame(
                    error="DashScope STT is not ready to accept audio",
                    fatal=True,
                    processor=self,
                )
            else:
                yield None
            return

        try:
            async with self._send_lock:
                if self._finish_sent:
                    yield None
                    return
                assert self._websocket is not None
                await self._websocket.send_bytes(audio)
        except Exception as exc:  # noqa: BLE001 - provider send failures vary by transport
            self._send_error_reported = True
            yield ErrorFrame(
                error=f"Failed to send audio to DashScope STT: {exc}",
                fatal=True,
                processor=self,
                exception=exc,
            )
            return
        yield None


__all__ = [
    "DashScopeSTTError",
    "DashScopeSTTService",
    "DashScopeSTTSettings",
    "DashScopeSegmentation",
]
