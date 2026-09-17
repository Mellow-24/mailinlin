"""Alibaba Cloud Model Studio (DashScope) streaming text-to-speech service.

This module implements the raw DashScope WebSocket protocol used by CosyVoice
and Qwen-Audio-TTS.  A single WebSocket is reused across synthesis turns while
each turn receives a fresh DashScope ``task_id``.  Audio is requested as raw
16-bit mono PCM so it can be forwarded to Pipecat without a codec dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import NOT_GIVEN, TTSSettings, _NotGiven, is_given
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts


DEFAULT_DASHSCOPE_TTS_WEBSOCKET_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
)
SUPPORTED_SAMPLE_RATES = frozenset({8000, 16000, 22050, 24000, 44100, 48000})


@dataclass
class DashScopeTTSSettings(TTSSettings):
    """Runtime-updatable settings for :class:`DashScopeTTSService`.

    ``audio_format`` is deliberately limited to ``pcm``.  Pipecat's
    :class:`TTSAudioRawFrame` represents decoded signed 16-bit PCM, so passing
    MP3, Opus, or WAV container bytes through that frame would corrupt audio.
    """

    audio_format: str | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    instruction: str | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    rate: float | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)

    _aliases = {**TTSSettings._aliases, "format": "audio_format"}


class DashScopeTTSService(TTSService):
    """Stream speech from Alibaba Cloud Model Studio over a raw WebSocket.

    The service follows the DashScope task lifecycle exactly:

    ``run-task`` -> ``task-started`` -> one or more ``continue-task`` events ->
    ``finish-task`` -> ``task-finished``.

    User interruption sends a ``finish-task`` event with
    ``payload.input.directive=cancel``.  Audio arriving after cancellation is
    discarded, and a new turn is not started until the server acknowledges the
    canceled task (or the stale connection is force-closed after a short
    timeout).
    """

    Settings = DashScopeTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longjiaxin_v3",
        sample_rate: int = 24000,
        audio_format: str = "pcm",
        format: str | None = None,
        instruction: str | None = None,
        rate: float = 1.0,
        base_url: str = DEFAULT_DASHSCOPE_TTS_WEBSOCKET_URL,
        workspace_id: str | None = None,
        aiohttp_session: aiohttp.ClientSession | None = None,
        settings: Settings | None = None,
        task_start_timeout_s: float = 10.0,
        cancel_timeout_s: float = 2.0,
        **kwargs: Any,
    ) -> None:
        """Initialize the DashScope streaming TTS service.

        Args:
            api_key: Alibaba Cloud Model Studio API key.
            model: CosyVoice or Qwen-Audio-TTS model name.
            voice: System, cloned, or designed voice identifier.
            sample_rate: Requested PCM sample rate in Hz.
            audio_format: Output format.  Only raw ``pcm`` is supported.
            format: Alias for ``audio_format`` for configuration compatibility.
            instruction: Optional dialect, emotion, or voice-character prompt.
            rate: Speech speed multiplier from 0.5 to 2.0.
            base_url: Complete DashScope WebSocket inference URL.
            workspace_id: Optional workspace ID sent in the request header.
            aiohttp_session: Optional caller-owned HTTP session.
            settings: Optional runtime settings; specified values win over the
                direct model, voice, format, and instruction arguments.
            task_start_timeout_s: Maximum wait for ``task-started``.
            cancel_timeout_s: Maximum wait for cancellation acknowledgement.
            **kwargs: Additional arguments passed to :class:`TTSService`.
        """
        if not api_key.strip():
            raise ValueError("DashScope API key must not be empty")
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            supported = ", ".join(str(rate) for rate in sorted(SUPPORTED_SAMPLE_RATES))
            raise ValueError(f"Unsupported DashScope sample rate {sample_rate}; use one of {supported}")
        if not base_url.startswith(("wss://", "ws://")):
            raise ValueError("DashScope TTS base_url must be a complete WebSocket URL")
        if task_start_timeout_s <= 0 or cancel_timeout_s <= 0:
            raise ValueError("DashScope TTS timeouts must be greater than zero")
        self._validate_rate(rate)

        if format is not None:
            if audio_format != "pcm" and audio_format.lower() != format.lower():
                raise ValueError("Conflicting audio_format and format values")
            audio_format = format

        default_settings = self.Settings(
            model=model,
            voice=voice,
            language=None,
            audio_format=audio_format.lower(),
            instruction=instruction,
            rate=rate,
        )
        if settings is not None:
            default_settings.apply_update(settings)
        self._validate_audio_format(default_settings.audio_format)

        super().__init__(
            sample_rate=sample_rate,
            push_start_frame=True,
            push_stop_frames=True,
            push_text_frames=True,
            pause_frame_processing=True,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        self._base_url = base_url
        self._workspace_id = workspace_id
        self._session = aiohttp_session
        self._session_owner = aiohttp_session is None
        self._task_start_timeout_s = task_start_timeout_s
        self._cancel_timeout_s = cancel_timeout_s

        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._task_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._closing = False
        self._auto_prime_tasks = False
        self._prime_task: asyncio.Task[None] | None = None

        self._active_task_id: str | None = None
        self._active_context_id: str | None = None
        self._task_started_event: asyncio.Event | None = None
        self._task_done_event: asyncio.Event | None = None
        self._finish_sent = False
        self._cancel_requested = False
        self._last_task_error: tuple[str, Exception] | None = None
        self._last_error_queued_to_context = False
        self._pcm_remainder = b""

    @staticmethod
    def _validate_audio_format(audio_format: str | None | _NotGiven) -> None:
        if not is_given(audio_format) or not audio_format:
            raise ValueError("DashScope TTS audio format must be set")
        if audio_format.lower() != "pcm":
            raise ValueError(
                "DashScopeTTSService only supports raw PCM output; "
                "MP3, Opus, and WAV cannot be put in TTSAudioRawFrame"
            )

    @staticmethod
    def _validate_rate(rate: float | None | _NotGiven) -> None:
        if not is_given(rate) or rate is None:
            return
        if not 0.5 <= float(rate) <= 2.0:
            raise ValueError("DashScope TTS rate must be between 0.5 and 2.0")

    def can_generate_metrics(self) -> bool:
        """Return whether this service can emit Pipecat TTS metrics."""
        return True

    async def _update_settings(self, delta: TTSSettings) -> dict[str, Any]:
        """Validate and apply settings used by the next DashScope task."""
        audio_format = getattr(delta, "audio_format", NOT_GIVEN)
        if is_given(audio_format):
            self._validate_audio_format(audio_format)
        rate = getattr(delta, "rate", NOT_GIVEN)
        if is_given(rate):
            self._validate_rate(rate)
        return await super()._update_settings(delta)

    async def start(self, frame: StartFrame) -> None:
        """Start Pipecat context handling and establish the WebSocket."""
        await super().start(frame)
        await self._connect()

    async def prewarm(self) -> None:
        """Open the authenticated socket before this processor sees StartFrame.

        ``_connect`` is lock-protected and idempotent, so pipeline startup can
        overlap this handshake with an upstream STT service safely. ``start``
        remains authoritative and will wait for or retry the same connection.
        """

        await self._connect()

    def enable_task_priming(self) -> None:
        """Tune the service for gated headless push-to-talk conversations.

        The browser already prevents user audio while the bot is speaking, so
        pausing the whole frame processor is redundant and can hold a final ASR
        frame behind the opening utterance. Keep provider tasks warm, but let
        transcription and turn-control frames continue through the pipeline.
        """

        self._auto_prime_tasks = True
        self._pause_frame_processing = False

    async def prime(self) -> None:
        """Open the socket and acknowledge an idle task for the next utterance."""

        await self._ensure_task(None)

    async def stop(self, frame: EndFrame) -> None:
        """Gracefully finish active synthesis before releasing resources."""
        await self.flush_audio(self._active_context_id)
        await super().stop(frame)
        await self._disconnect(cancel_active=False)

    async def cancel(self, frame: CancelFrame) -> None:
        """Promptly cancel active synthesis and release resources."""
        await self._cancel_active_task(self._active_context_id)
        await super().cancel(frame)
        await self._disconnect(cancel_active=False)

    async def cleanup(self) -> None:
        """Idempotently close the task, socket, receive loop, and owned session."""
        await self._cancel_active_task(self._active_context_id)
        await super().cleanup()
        await self._disconnect(cancel_active=False)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._session_owner = True
        return self._session

    @staticmethod
    def _websocket_is_open(websocket: Any | None) -> bool:
        return websocket is not None and not websocket.closed

    async def _connect(self) -> None:
        """Open one reusable authenticated DashScope WebSocket."""
        async with self._connect_lock:
            if self._websocket_is_open(self._websocket):
                return

            if self._receive_task is not None and not self._receive_task.done():
                self._receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._receive_task
            self._receive_task = None

            session = await self._ensure_session()
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "Dograh-DashScope-TTS/1.0",
            }
            if self._workspace_id:
                headers["X-DashScope-WorkSpace"] = self._workspace_id

            self._closing = False
            try:
                self._websocket = await session.ws_connect(
                    self._base_url,
                    headers=headers,
                    heartbeat=20.0,
                )
            except Exception as exc:
                self._websocket = None
                await self._call_event_handler("on_connection_error", str(exc))
                raise ConnectionError(f"Unable to connect to DashScope TTS: {exc}") from exc

            self._receive_task = asyncio.create_task(
                self._receive_messages(), name=f"{self.name}-dashscope-receive"
            )
            await self._call_event_handler("on_connected")

    async def _close_websocket(self) -> None:
        websocket = self._websocket
        self._websocket = None
        if websocket is not None and not websocket.closed:
            with contextlib.suppress(Exception):
                await websocket.close()

        receive_task = self._receive_task
        self._receive_task = None
        if (
            receive_task is not None
            and receive_task is not asyncio.current_task()
            and not receive_task.done()
        ):
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task

    async def _cancel_prime_task(self) -> None:
        task = self._prime_task
        self._prime_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _disconnect(self, *, cancel_active: bool = True) -> None:
        """Safely close network resources without leaking background tasks."""
        self._closing = True
        await self._cancel_prime_task()
        if cancel_active:
            await self._cancel_active_task(self._active_context_id)

        had_connection = self._websocket is not None
        await self._close_websocket()
        self._release_active_task(ConnectionError("DashScope TTS connection closed"))

        if self._session_owner and self._session is not None and not self._session.closed:
            await self._session.close()
        if self._session_owner:
            self._session = None

        if had_connection:
            await self._call_event_handler("on_disconnected")

    async def _send_json(self, message: dict[str, Any]) -> None:
        websocket = self._websocket
        if not self._websocket_is_open(websocket):
            raise ConnectionError("DashScope TTS WebSocket is not connected")
        async with self._send_lock:
            await websocket.send_json(message)

    def _build_run_task(self, task_id: str) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "text_type": "PlainText",
            "voice": self._settings.voice,
            "format": self._settings.audio_format,
            "sample_rate": self._init_sample_rate,
        }
        instruction = self._settings.instruction
        if instruction and instruction.strip():
            parameters["instruction"] = instruction.strip()
        rate = self._settings.rate
        if is_given(rate) and rate is not None and float(rate) != 1.0:
            parameters["rate"] = float(rate)
        return {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._settings.model,
                "parameters": parameters,
                "input": {},
            },
        }

    @staticmethod
    def _build_continue_task(task_id: str, text: str) -> dict[str, Any]:
        return {
            "header": {
                "action": "continue-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {"text": text}},
        }

    @staticmethod
    def _build_finish_task(task_id: str, *, cancel: bool = False) -> dict[str, Any]:
        input_payload: dict[str, str] = {"directive": "cancel"} if cancel else {}
        return {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": input_payload},
        }

    async def _ensure_task(self, context_id: str | None) -> str:
        """Start a task for ``context_id`` and wait for server acknowledgement."""
        async with self._task_lock:
            if self._active_task_id and self._active_context_id == context_id:
                if self._last_task_error and self._last_task_error[0] == self._active_task_id:
                    raise self._last_task_error[1]
                return self._active_task_id

            # ``prime`` starts an empty provider task before text exists. The
            # first real utterance adopts it without another run-task roundtrip.
            if (
                self._active_task_id
                and self._active_context_id is None
                and context_id is not None
            ):
                self._active_context_id = context_id
                return self._active_task_id

            if self._active_task_id:
                done_event = self._task_done_event
                if done_event is not None:
                    try:
                        await asyncio.wait_for(
                            done_event.wait(), timeout=self._task_start_timeout_s
                        )
                    except TimeoutError as exc:
                        await self._close_websocket()
                        self._release_active_task(exc)
                        raise TimeoutError(
                            "Previous DashScope TTS task did not finish in time"
                        ) from exc

            await self._connect()
            task_id = str(uuid.uuid4())
            started_event = asyncio.Event()
            self._active_task_id = task_id
            self._active_context_id = context_id
            self._task_started_event = started_event
            self._task_done_event = asyncio.Event()
            self._finish_sent = False
            self._cancel_requested = False
            self._last_task_error = None
            self._last_error_queued_to_context = False
            self._pcm_remainder = b""

            await self._send_json(self._build_run_task(task_id))
            try:
                await asyncio.wait_for(
                    started_event.wait(), timeout=self._task_start_timeout_s
                )
            except TimeoutError as exc:
                await self._close_websocket()
                self._release_active_task(exc)
                raise TimeoutError("DashScope TTS did not acknowledge run-task") from exc

            if self._last_task_error and self._last_task_error[0] == task_id:
                raise self._last_task_error[1]
            if self._active_task_id != task_id:
                raise RuntimeError("DashScope TTS task ended before synthesis started")
            return task_id

    def _schedule_prime_task(self) -> None:
        if (
            not self._auto_prime_tasks
            or self._closing
            or (self._prime_task is not None and not self._prime_task.done())
        ):
            return

        async def _run() -> None:
            try:
                await self.prime()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"{self}: next-turn TTS priming failed: {exc}")
            finally:
                if self._prime_task is asyncio.current_task():
                    self._prime_task = None

        self._prime_task = asyncio.create_task(
            _run(), name=f"{self.name}-dashscope-prime"
        )

    def _release_active_task(self, error: Exception | None = None) -> tuple[str | None, str | None]:
        task_id = self._active_task_id
        context_id = self._active_context_id
        if task_id and error is not None:
            self._last_task_error = (task_id, error)
        if self._task_started_event is not None:
            self._task_started_event.set()
        if self._task_done_event is not None:
            self._task_done_event.set()
        self._active_task_id = None
        self._active_context_id = None
        self._task_started_event = None
        self._task_done_event = None
        self._finish_sent = False
        self._cancel_requested = False
        self._pcm_remainder = b""
        return task_id, context_id

    async def _fail_active_task(self, error: Exception) -> None:
        """Complete the Pipecat context and unblock all task waiters on failure."""
        task_id = self._active_task_id
        context_id = self._active_context_id
        if task_id is None:
            return
        self._last_task_error = (task_id, error)
        if self._task_started_event is not None:
            self._task_started_event.set()
        if context_id and self.audio_context_available(context_id):
            await self.append_to_audio_context(
                context_id, ErrorFrame(error=str(error), exception=error)
            )
            await self.append_to_audio_context(
                context_id, TTSStoppedFrame(context_id=context_id)
            )
            await self.remove_audio_context(context_id)
            self._last_error_queued_to_context = True
        await self.stop_all_metrics()
        self._release_active_task(error)

    async def flush_audio(self, context_id: str | None = None) -> None:
        """Send ``finish-task`` after all text for the current turn is queued."""
        task_id = self._active_task_id
        if (
            task_id is None
            or self._finish_sent
            or self._cancel_requested
            or (context_id is not None and context_id != self._active_context_id)
        ):
            return
        self._finish_sent = True
        try:
            await self._send_json(self._build_finish_task(task_id))
        except Exception as exc:
            await self._fail_active_task(
                ConnectionError(f"Unable to finish DashScope TTS task: {exc}")
            )

    async def _cancel_active_task(self, context_id: str | None) -> None:
        task_id = self._active_task_id
        if (
            task_id is None
            or self._cancel_requested
            or (context_id is not None and context_id != self._active_context_id)
        ):
            return

        self._cancel_requested = True
        done_event = self._task_done_event
        try:
            await self._send_json(self._build_finish_task(task_id, cancel=True))
            if done_event is not None:
                await asyncio.wait_for(done_event.wait(), timeout=self._cancel_timeout_s)
        except (TimeoutError, ConnectionError, aiohttp.ClientError) as exc:
            logger.warning(f"{self}: cancel acknowledgement failed; dropping connection: {exc}")
            await self._close_websocket()
            self._release_active_task(exc)

    async def on_audio_context_interrupted(self, context_id: str) -> None:
        """Cancel provider-side generation immediately when the user interrupts."""
        await self._cancel_active_task(context_id)

    async def _receive_messages(self) -> None:
        websocket = self._websocket
        if websocket is None:
            return
        ended_normally = False
        try:
            async for message in websocket:
                if message.type is aiohttp.WSMsgType.TEXT:
                    await self._handle_text_message(message.data)
                elif message.type is aiohttp.WSMsgType.BINARY:
                    await self._handle_binary_message(bytes(message.data))
                elif message.type is aiohttp.WSMsgType.ERROR:
                    exception = websocket.exception()
                    raise ConnectionError(f"DashScope TTS WebSocket error: {exception}")
                elif message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                }:
                    ended_normally = True
                    break
            else:
                ended_normally = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                await self._fail_active_task(
                    ConnectionError(f"DashScope TTS receive loop failed: {exc}")
                )
                await self._call_event_handler("on_connection_error", str(exc))
        finally:
            if self._websocket is websocket:
                self._websocket = None
            if ended_normally and not self._closing and self._active_task_id:
                exc = ConnectionError("DashScope TTS WebSocket closed before task-finished")
                await self._fail_active_task(exc)
                await self._call_event_handler("on_connection_error", str(exc))

    async def _handle_text_message(self, raw_message: str) -> None:
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            await self._fail_active_task(
                ValueError(f"DashScope TTS returned invalid JSON: {exc}")
            )
            return

        header = message.get("header") or {}
        task_id = header.get("task_id")
        event = header.get("event")
        if task_id != self._active_task_id:
            logger.debug(f"{self}: ignoring stale DashScope event for task {task_id}")
            return

        if event == "task-started":
            if self._task_started_event is not None:
                self._task_started_event.set()
            return

        if event == "result-generated":
            # Audio follows a ``sentence-synthesis`` event as the next binary
            # WebSocket message.  Binary handling deliberately keys off the
            # active task as binary frames themselves carry no task ID.
            return

        if event == "task-failed":
            code = header.get("error_code", "UnknownError")
            detail = header.get("error_message", "DashScope synthesis failed")
            await self._fail_active_task(RuntimeError(f"DashScope TTS {code}: {detail}"))
            return

        if event == "task-finished":
            context_id = self._active_context_id
            was_cancelled = self._cancel_requested
            if context_id and not was_cancelled and self.audio_context_available(context_id):
                if self._pcm_remainder:
                    padded = self._pcm_remainder + b"\x00"
                    await self.append_to_audio_context(
                        context_id,
                        TTSAudioRawFrame(
                            audio=padded,
                            sample_rate=self.sample_rate,
                            num_channels=1,
                            context_id=context_id,
                        ),
                    )
                await self.append_to_audio_context(
                    context_id, TTSStoppedFrame(context_id=context_id)
                )
                await self.remove_audio_context(context_id)
            await self.stop_all_metrics()
            self._release_active_task()
            if not was_cancelled:
                self._schedule_prime_task()

    async def _handle_binary_message(self, audio: bytes) -> None:
        context_id = self._active_context_id
        if (
            not audio
            or self._cancel_requested
            or context_id is None
            or not self.audio_context_available(context_id)
        ):
            return

        # DashScope PCM is signed 16-bit little endian.  WebSocket boundaries
        # are arbitrary, so retain an odd trailing byte for the next chunk.
        pcm = self._pcm_remainder + audio
        aligned_length = len(pcm) & ~1
        self._pcm_remainder = pcm[aligned_length:]
        if aligned_length == 0:
            return
        frame = TTSAudioRawFrame(
            audio=pcm[:aligned_length],
            sample_rate=self.sample_rate,
            num_channels=1,
            context_id=context_id,
        )
        await self.stop_ttfb_metrics()
        await self.append_to_audio_context(context_id, frame)

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Send a text fragment with ``continue-task`` and receive audio elsewhere."""
        if not text:
            yield None
            return
        try:
            task_id = await self._ensure_task(context_id)
            await self._send_json(self._build_continue_task(task_id, text))
            await self.start_tts_usage_metrics(text)
            yield None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"{self}: DashScope TTS request failed: {exc}")
            if self._last_error_queued_to_context:
                # The independent receive loop has already queued the ordered
                # Error/TTSStopped frames for this context.
                yield None
            else:
                yield ErrorFrame(error=f"DashScope TTS request failed: {exc}", exception=exc)
                yield TTSStoppedFrame(context_id=context_id)
                if self.audio_context_available(context_id):
                    await self.remove_audio_context(context_id)
            await self._close_websocket()
            self._release_active_task(exc)
