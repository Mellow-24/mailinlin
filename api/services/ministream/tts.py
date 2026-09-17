"""Credential-safe streaming MiniStream text-to-speech integration.

MiniStream delivers 48 kHz MP3 bytes over one WebSocket connection.  Pipecat
expects signed 16-bit PCM frames, so each provider request owns a short-lived
ffmpeg decoder while the service-level socket and receiver task are reused.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp
from loguru import logger

from api.services.configuration.options.ministream import (
    MINISTREAM_TRIAL_TLS_FINGERPRINT_SHA256,
    MINISTREAM_WEBSOCKET_URL,
)
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
)
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService


DecoderFactory = Callable[[], Any | Awaitable[Any]]
Sleep = Callable[[float], Awaitable[None]]


_RETRYABLE_OVERLOAD_STATUS = 429
_RETRYABLE_OVERLOAD_CLOSE_CODE = 1013
_INITIAL_RETRYABLE_RECONNECT_DELAY_S = 0.25
_MAX_RETRYABLE_RECONNECT_DELAY_S = 1.0


@dataclass
class _SynthesisRequest:
    """State that associates one provider request with one Pipecat context."""

    request_id: str
    context_id: str
    text: str
    sent: bool = False
    decoder: Any | None = None
    decoder_task: asyncio.Task[None] | None = None
    provider_started: bool = False
    provider_ended: bool = False
    decoder_drained: bool = False
    cancelled: bool = False
    pcm_remainder: bytes = b""


class MiniStreamTTSService(TTSService):
    """Stream MiniStream MP3 synthesis through per-request PCM decoders.

    The service deliberately keeps credentials out of the connection URL.  All
    documented non-secret provider settings live in the URL query string;
    authentication is sent only during the WebSocket handshake.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        websocket_url: str,
        language: str,
        generation_mode: str,
        voice_preset_key: str,
        buffer: str,
        buffer_idle_ms: int,
        max_generate_length: int,
        sample_rate: int,
        playback_rate: float,
        verify_ssl: bool,
        tls_certificate_fingerprint_sha256: str = (
            MINISTREAM_TRIAL_TLS_FINGERPRINT_SHA256
        ),
        aiohttp_session: aiohttp.ClientSession | None = None,
        decoder_factory: DecoderFactory | None = None,
        **kwargs: Any,
    ) -> None:
        """Create a MiniStream streaming TTS service.

        ``decoder_factory`` is an optional test seam.  Production callers use
        ffmpeg via :func:`asyncio.create_subprocess_exec`.
        """
        if not api_key or not api_key.strip():
            raise ValueError("MiniStream API key must not be empty")
        if sample_rate != 16_000:
            raise ValueError("MiniStream TTS output sample rate must be 16000 Hz")
        if playback_rate <= 0:
            raise ValueError("MiniStream playback_rate must be greater than zero")
        if buffer_idle_ms < 0:
            raise ValueError("MiniStream buffer_idle_ms must not be negative")
        if max_generate_length <= 0:
            raise ValueError("MiniStream max_generate_length must be greater than zero")
        if not verify_ssl:
            raise ValueError("verify_ssl=True is required for MiniStream TTS")
        if not all(
            value and value.strip()
            for value in (model, language, generation_mode, voice_preset_key, buffer)
        ):
            raise ValueError("MiniStream TTS settings must not be empty")

        super().__init__(
            sample_rate=sample_rate,
            push_start_frame=True,
            push_stop_frames=True,
            push_text_frames=True,
            pause_frame_processing=True,
            settings=TTSSettings(model=model, voice=None, language=None),
            **kwargs,
        )

        self._api_key = api_key
        self._sample_rate = sample_rate
        self._language = language
        self._generation_mode = generation_mode
        self._voice_preset_key = voice_preset_key
        self._buffer = buffer
        self._buffer_idle_ms = buffer_idle_ms
        self._max_generate_length = max_generate_length
        self._playback_rate = playback_rate
        self._trial_tls_fingerprint = self._parse_trial_tls_fingerprint(
            tls_certificate_fingerprint_sha256
        )
        self._client_id = uuid.uuid4().hex
        self._uses_trial_tls_pin = self._is_fixed_trial_websocket_url(websocket_url)
        self._websocket_url = self._build_websocket_url(websocket_url, self._client_id)

        self._session = aiohttp_session
        self._session_owner = aiohttp_session is None
        self._decoder_factory = decoder_factory
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._dispatching_task: asyncio.Task[Any] | None = None
        self._state_lock = asyncio.Lock()
        self._closing = False
        self._sleep: Sleep = asyncio.sleep
        self._retryable_reconnect_delay_s = 0.0
        self._pending_reconnect_delay_s = 0.0

        self._requests: dict[str, _SynthesisRequest] = {}
        self._context_requests: dict[str, set[str]] = {}
        self._flushed_contexts: set[str] = set()
        self._pending_request_ids: deque[str] = deque()
        self._active_request_id: str | None = None

    @staticmethod
    def _build_websocket_url(base_url: str, client_id: str) -> str:
        """Append ``client_id`` and controlled non-secret settings to ``base_url``.

        A configured query string is rejected rather than copied through.  That
        prevents an accidental legacy ``token=`` URL configuration from reaching
        a proxy, access log, or exception message.
        """
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "wss"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "MiniStream WebSocket URL must be a credential-free base URL"
            )
        if not client_id:
            raise ValueError("MiniStream client ID must not be empty")

        # Settings are bound by __init__ immediately after this helper is used;
        # keep the URL construction in one place by allowing __init__ to replace
        # the placeholder query below.
        path = f"{parsed.path.rstrip('/')}/{client_id}"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    @staticmethod
    def _is_fixed_trial_websocket_url(websocket_url: str) -> bool:
        """Return whether ``websocket_url`` is the pinned public trial URL."""
        return websocket_url.rstrip("/") == MINISTREAM_WEBSOCKET_URL.rstrip("/")

    @staticmethod
    def _parse_trial_tls_fingerprint(fingerprint: str) -> bytes:
        """Decode the one allowed public trial certificate fingerprint."""
        if not isinstance(fingerprint, str):
            raise ValueError("MiniStream TLS certificate fingerprint is invalid")
        normalized_fingerprint = fingerprint.replace(":", "").upper()
        if normalized_fingerprint != MINISTREAM_TRIAL_TLS_FINGERPRINT_SHA256:
            raise ValueError("MiniStream TLS certificate fingerprint is invalid")
        return bytes.fromhex(normalized_fingerprint)

    def _websocket_tls_policy(self) -> bool | aiohttp.Fingerprint:
        """Use a pin for the self-signed trial and verified TLS elsewhere."""
        if self._uses_trial_tls_pin:
            return aiohttp.Fingerprint(self._trial_tls_fingerprint)
        return True

    def _connection_url(self) -> str:
        """Return the final URL containing only documented non-secret settings."""
        parsed = urlsplit(self._websocket_url)
        query = urlencode(
            (
                ("language", self._language),
                ("generation_mode", self._generation_mode),
                ("voice_preset_key", self._voice_preset_key),
                ("buffer", self._buffer),
                ("buffer_idle_ms", str(self._buffer_idle_ms)),
                ("max_generate_length", str(self._max_generate_length)),
            )
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))

    def can_generate_metrics(self) -> bool:
        """MiniStream has normal Pipecat TTS timing and usage metrics."""
        return True

    async def start(self, frame: StartFrame) -> None:
        """Start Pipecat context handling and open the reusable socket."""
        await super().start(frame)
        await self._connect()

    async def prewarm(self) -> None:
        """Open the authenticated socket before the first sentence arrives."""
        await self.prime()

    async def prime(self) -> None:
        """Warm the socket for fast-opening voice calls without synthesizing text."""
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        """Release the socket and any decoders before stopping Pipecat."""
        await self._disconnect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        """Stop decoder output immediately when the pipeline is cancelled."""
        await self._disconnect()
        await super().cancel(frame)

    async def cleanup(self) -> None:
        """Idempotently close all owned runtime resources."""
        await self._disconnect()
        await super().cleanup()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._session_owner = True
        return self._session

    @staticmethod
    def _websocket_is_open(websocket: Any | None) -> bool:
        return websocket is not None and not websocket.closed

    @staticmethod
    def _is_retryable_handshake_overload(error: Exception) -> bool:
        """Return whether a failed WebSocket handshake was rate limited."""
        try:
            return int(getattr(error, "status", 0) or 0) == _RETRYABLE_OVERLOAD_STATUS
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _is_retryable_overload_close(websocket: Any) -> bool:
        """Return whether MiniStream closed this socket due to capacity pressure."""
        return getattr(websocket, "close_code", None) == _RETRYABLE_OVERLOAD_CLOSE_CODE

    def _schedule_retryable_reconnect(self) -> None:
        """Delay only a later, unsent request after a provider overload.

        Requests that have reached MiniStream are intentionally not replayed: a
        replay would risk speaking the same sentence twice.  The next request
        uses this capped async delay before opening a new socket instead.
        """
        previous_delay = self._retryable_reconnect_delay_s
        self._retryable_reconnect_delay_s = min(
            _MAX_RETRYABLE_RECONNECT_DELAY_S,
            (
                _INITIAL_RETRYABLE_RECONNECT_DELAY_S
                if previous_delay <= 0
                else previous_delay * 2
            ),
        )
        self._pending_reconnect_delay_s = self._retryable_reconnect_delay_s

    async def _wait_for_retryable_reconnect_backoff(self) -> None:
        """Yield to the event loop for a previously scheduled overload delay."""
        delay = self._pending_reconnect_delay_s
        if delay <= 0:
            return
        self._pending_reconnect_delay_s = 0.0
        try:
            await self._sleep(delay)
        except asyncio.CancelledError:
            self._pending_reconnect_delay_s = max(
                self._pending_reconnect_delay_s, delay
            )
            raise

    def _reset_retryable_reconnect_backoff(self) -> None:
        """Clear overload history after MiniStream accepted a real request."""
        self._retryable_reconnect_delay_s = 0.0
        self._pending_reconnect_delay_s = 0.0

    async def _connect(self) -> None:
        """Open one authenticated WebSocket, once per service instance."""
        async with self._connect_lock:
            if self._websocket_is_open(self._websocket):
                return

            receive_task = self._receive_task
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
            self._receive_task = None

            await self._wait_for_retryable_reconnect_backoff()

            session = await self._ensure_session()
            try:
                self._websocket = await session.ws_connect(
                    self._connection_url(),
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "User-Agent": "Dograh-MiniStream-TTS/1.0",
                    },
                    ssl=self._websocket_tls_policy(),
                    heartbeat=20.0,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._websocket = None
                if self._is_retryable_handshake_overload(error):
                    self._schedule_retryable_reconnect()
                    logger.warning("MiniStream TTS WebSocket is temporarily busy")
                    message = "MiniStream TTS is temporarily busy"
                else:
                    logger.warning("MiniStream TTS WebSocket connection failed")
                    message = "MiniStream TTS connection failed"
                await self._notify_connection_error(message)
                raise ConnectionError(message) from None

            self._closing = False
            self._receive_task = asyncio.create_task(
                self._receive_messages(), name=f"{self.name}-ministream-receive"
            )
            with contextlib.suppress(Exception):
                await self._call_event_handler("on_connected")

    async def _notify_connection_error(self, message: str) -> None:
        """Notify observers without passing through untrusted provider details."""
        with contextlib.suppress(Exception):
            await self._call_event_handler("on_connection_error", message)

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

    async def _disconnect(self) -> None:
        """Tear down local resources without propagating provider secrets."""
        self._closing = True
        had_connection = self._websocket is not None
        await self._close_websocket()
        await self._discard_all_contexts(remove_contexts=True)

        if (
            self._session_owner
            and self._session is not None
            and not self._session.closed
        ):
            with contextlib.suppress(Exception):
                await self._session.close()
        if self._session_owner:
            self._session = None

        if had_connection:
            with contextlib.suppress(Exception):
                await self._call_event_handler("on_disconnected")

    async def _send_json(self, message: dict[str, str]) -> None:
        websocket = self._websocket
        if not self._websocket_is_open(websocket):
            raise ConnectionError("MiniStream TTS WebSocket is not connected")
        async with self._send_lock:
            await websocket.send_json(message)

    async def _dispatch_pending_requests(self) -> None:
        """Send one FIFO request and wait for its terminal provider lifecycle.

        MiniStream MP3 frames do not include a request ID.  We therefore allow
        only the request at the head of the service-wide queue onto a socket at
        a time.  ``run_tts`` returns immediately for later sentences while this
        method is resumed by the active request's terminal path.
        """
        current_task = asyncio.current_task()
        if self._dispatching_task is current_task:
            return

        async with self._dispatch_lock:
            self._dispatching_task = current_task
            try:
                while True:
                    async with self._state_lock:
                        if self._active_request_id is not None:
                            return

                        request: _SynthesisRequest | None = None
                        while self._pending_request_ids:
                            request_id = self._pending_request_ids.popleft()
                            candidate = self._requests.get(request_id)
                            if candidate is not None and not candidate.cancelled:
                                request = candidate
                                break
                        if request is None:
                            return
                        self._active_request_id = request.request_id

                    try:
                        await self._connect()
                        async with self._state_lock:
                            current_request = self._requests.get(request.request_id)
                            ready_to_send = (
                                current_request is request
                                and not request.cancelled
                                and self._active_request_id == request.request_id
                            )
                            if ready_to_send:
                                # Mark before awaiting the write so a start event
                                # received immediately after it can be accepted.
                                request.sent = True

                        if not ready_to_send:
                            # Interruption/cancellation won the race with the
                            # handshake; do not leave an unowned socket alive.
                            await self._close_websocket()
                            continue

                        await self._send_json(
                            {
                                "type": "synthesize",
                                "text": request.text,
                                "request_id": request.request_id,
                            }
                        )
                        await self.start_tts_usage_metrics(request.text)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "MiniStream TTS synthesis request could not be sent"
                        )
                        await self._fail_context(
                            request.context_id,
                            "MiniStream TTS synthesis request failed",
                            dispatch_next=False,
                        )
                        # The failed request was removed.  The next queued
                        # request can be attempted without replaying it.
                        continue

                    # The provider now owns this request.  Its ``end`` plus
                    # decoder drain path will invoke this dispatcher again.
                    return
            finally:
                if self._dispatching_task is current_task:
                    self._dispatching_task = None

    async def _receive_messages(self) -> None:
        """Route provider events and binary chunks without logging their payloads."""
        websocket = self._websocket
        if websocket is None:
            return

        clean_close = False
        try:
            async for message in websocket:
                if message.type is aiohttp.WSMsgType.TEXT:
                    await self._handle_text_message(str(message.data))
                elif message.type is aiohttp.WSMsgType.BINARY:
                    await self._handle_binary_message(bytes(message.data))
                elif message.type is aiohttp.WSMsgType.ERROR:
                    raise ConnectionError("MiniStream TTS WebSocket receiver failed")
                elif message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                }:
                    clean_close = True
                    break
            else:
                clean_close = True
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                logger.warning("MiniStream TTS WebSocket receiver failed")
                await self._fail_all_contexts("MiniStream TTS connection failed")
                await self._notify_connection_error("MiniStream TTS connection failed")
        finally:
            retryable_overload = (
                not self._closing and self._is_retryable_overload_close(websocket)
            )
            if retryable_overload:
                self._schedule_retryable_reconnect()
            if self._websocket is websocket:
                self._websocket = None
            if clean_close and not self._closing and self._requests:
                await self._fail_all_contexts("MiniStream TTS connection closed")
                await self._notify_connection_error("MiniStream TTS connection closed")

    async def _handle_text_message(self, raw_message: str) -> None:
        try:
            message = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("MiniStream TTS returned invalid provider JSON")
            await self._fail_all_contexts("MiniStream TTS returned an invalid response")
            return

        if not isinstance(message, dict):
            logger.warning("MiniStream TTS returned an invalid provider JSON")
            await self._fail_all_contexts("MiniStream TTS returned an invalid response")
            return

        event_type = str(message.get("type") or message.get("event") or "").lower()
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        request_id = message.get("request_id") or data.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            if event_type == "error":
                logger.warning("MiniStream TTS received an uncorrelated provider error")
                await self._fail_all_contexts("MiniStream TTS synthesis failed")
                return
            logger.debug("MiniStream TTS ignored an event without a request ID")
            return

        if event_type == "start":
            await self._start_request(request_id)
        elif event_type == "end":
            await self._end_request(request_id)
        elif event_type == "error":
            await self._fail_request(request_id, "MiniStream TTS synthesis failed")
        else:
            logger.debug("MiniStream TTS ignored an unknown provider event")

    async def _start_request(self, request_id: str) -> None:
        """Launch the request's incremental MP3 decoder after provider start."""
        async with self._state_lock:
            request = self._requests.get(request_id)
            if (
                request is None
                or request.cancelled
                or not request.sent
                or self._active_request_id != request_id
                or request.provider_started
                or request.provider_ended
            ):
                return
            request.provider_started = True
            self._reset_retryable_reconnect_backoff()

        try:
            decoder = await self._create_decoder()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("MiniStream TTS decoder could not be started")
            await self._fail_request(
                request_id, "MiniStream TTS decoder failed to start"
            )
            return

        discard_decoder = False
        async with self._state_lock:
            request = self._requests.get(request_id)
            if (
                request is None
                or request.cancelled
                or not request.sent
                or self._active_request_id != request_id
                or request.provider_ended
            ):
                discard_decoder = True
            else:
                request.decoder = decoder
                request.decoder_task = asyncio.create_task(
                    self._pump_decoder(request_id, decoder),
                    name=f"{self.name}-ministream-decode",
                )

        if discard_decoder:
            await self._terminate_decoder(decoder)

    async def _create_decoder(self) -> Any:
        if self._decoder_factory is not None:
            decoder = self._decoder_factory()
            if inspect.isawaitable(decoder):
                decoder = await decoder
            return decoder

        return await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-probesize",
            "32",
            "-analyzeduration",
            "0",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-af",
            f"atempo={self._playback_rate}",
            "-ac",
            "1",
            "-ar",
            str(self._init_sample_rate),
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _handle_binary_message(self, audio: bytes) -> None:
        """Feed a binary MP3 chunk only to the provider's active request."""
        if not audio:
            return
        async with self._state_lock:
            request_id = self._active_request_id
            request = self._requests.get(request_id) if request_id else None
            if (
                request is None
                or request.cancelled
                or not request.sent
                or request.provider_ended
                or request.decoder is None
            ):
                return
            decoder = request.decoder

        try:
            stdin = decoder.stdin
            stdin.write(audio)
            await stdin.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("MiniStream TTS decoder input failed")
            await self._fail_request(request_id, "MiniStream TTS audio decoding failed")

    async def _end_request(self, request_id: str) -> None:
        """Close decoder stdin, then let its stdout task finish the request."""
        async with self._state_lock:
            request = self._requests.get(request_id)
            if (
                request is None
                or request.cancelled
                or not request.sent
                or self._active_request_id != request_id
            ):
                return
            request.provider_ended = True
            decoder = request.decoder
            decoder_drained = request.decoder_drained

        if decoder is None:
            # A provider may end an empty request without a start/audio frame.
            await self._complete_request(request_id)
            return

        try:
            decoder.stdin.close()
            wait_closed = getattr(decoder.stdin, "wait_closed", None)
            if wait_closed is not None:
                await wait_closed()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("MiniStream TTS decoder input could not be closed")
            await self._fail_request(request_id, "MiniStream TTS audio decoding failed")
            return

        if decoder_drained:
            await self._complete_request(request_id)

    async def _pump_decoder(self, request_id: str, decoder: Any) -> None:
        """Append ffmpeg stdout as aligned 16-bit mono Pipecat PCM frames."""
        try:
            while True:
                pcm = await decoder.stdout.read(4096)
                if not pcm:
                    break
                await self._append_pcm(request_id, bytes(pcm))

            return_code = await decoder.wait()
            if return_code not in (None, 0):
                logger.warning("MiniStream TTS decoder exited unsuccessfully")
                await self._fail_request(
                    request_id, "MiniStream TTS audio decoding failed"
                )
                return
            await self._decoder_drained(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("MiniStream TTS decoder output failed")
            await self._fail_request(request_id, "MiniStream TTS audio decoding failed")

    async def _append_pcm(self, request_id: str, pcm: bytes) -> None:
        """Align arbitrary decoder reads to signed 16-bit samples and queue them."""
        async with self._state_lock:
            request = self._requests.get(request_id)
            if request is None or request.cancelled:
                return
            combined = request.pcm_remainder + pcm
            aligned_length = len(combined) & ~1
            request.pcm_remainder = combined[aligned_length:]
            if not aligned_length or not self.audio_context_available(
                request.context_id
            ):
                return
            await self.append_to_audio_context(
                request.context_id,
                TTSAudioRawFrame(
                    audio=combined[:aligned_length],
                    sample_rate=self._init_sample_rate,
                    num_channels=1,
                    context_id=request.context_id,
                ),
            )

    async def _decoder_drained(self, request_id: str) -> None:
        async with self._state_lock:
            request = self._requests.get(request_id)
            if request is None or request.cancelled:
                return
            request.decoder_drained = True
            provider_ended = request.provider_ended
            context_id = request.context_id
            remainder = request.pcm_remainder
            request.pcm_remainder = b""

            if remainder and self.audio_context_available(context_id):
                await self.append_to_audio_context(
                    context_id,
                    TTSAudioRawFrame(
                        audio=remainder + b"\x00",
                        sample_rate=self._init_sample_rate,
                        num_channels=1,
                        context_id=context_id,
                    ),
                )

        if provider_ended:
            await self._complete_request(request_id)

    async def _complete_request(self, request_id: str) -> None:
        """Release a completed request and finish a flushed, drained context."""
        async with self._state_lock:
            request = self._requests.pop(request_id, None)
            if request is None or request.cancelled:
                return
            if self._active_request_id == request_id:
                self._active_request_id = None

            request_ids = self._context_requests.get(request.context_id)
            if request_ids is not None:
                request_ids.discard(request_id)
                if not request_ids:
                    self._context_requests.pop(request.context_id, None)
            should_finish_context = (
                request.context_id in self._flushed_contexts
                and request.context_id not in self._context_requests
            )
            if should_finish_context:
                self._flushed_contexts.discard(request.context_id)
            no_requests_remain = not self._requests

        if should_finish_context and self.audio_context_available(request.context_id):
            await self.remove_audio_context(request.context_id)
        if no_requests_remain:
            await self.stop_all_metrics()
        await self._dispatch_pending_requests()

    async def _fail_request(self, request_id: str | None, message: str) -> None:
        if not request_id:
            return
        async with self._state_lock:
            request = self._requests.get(request_id)
            context_id = (
                request.context_id
                if (
                    request is not None
                    and request.sent
                    and self._active_request_id == request_id
                )
                else None
            )
        if context_id is not None:
            await self._fail_context(context_id, message)

    async def _fail_context(
        self, context_id: str, message: str, *, dispatch_next: bool = True
    ) -> None:
        """End one context with a fixed, credential-safe error frame."""
        requests = await self._discard_context(context_id)
        for request in requests:
            await self._dispose_request(request)
        if self.audio_context_available(context_id):
            exception = RuntimeError(message)
            await self.append_to_audio_context(
                context_id, ErrorFrame(error=message, exception=exception)
            )
            await self.remove_audio_context(context_id)
        await self.stop_all_metrics()
        if dispatch_next:
            await self._dispatch_pending_requests()

    async def _fail_all_contexts(self, message: str) -> None:
        async with self._state_lock:
            context_ids = list(self._context_requests)
        for context_id in context_ids:
            await self._fail_context(context_id, message, dispatch_next=False)
        await self._dispatch_pending_requests()

    async def _discard_context(self, context_id: str) -> list[_SynthesisRequest]:
        """Drop request bookkeeping and return decoders for asynchronous teardown."""
        async with self._state_lock:
            request_ids = self._context_requests.pop(context_id, set())
            requests: list[_SynthesisRequest] = []
            for request_id in request_ids:
                request = self._requests.pop(request_id, None)
                if request is not None:
                    request.cancelled = True
                    requests.append(request)
                if self._active_request_id == request_id:
                    self._active_request_id = None
            if request_ids:
                self._pending_request_ids = deque(
                    request_id
                    for request_id in self._pending_request_ids
                    if request_id not in request_ids
                )
            self._flushed_contexts.discard(context_id)
            return requests

    async def _discard_all_contexts(self, *, remove_contexts: bool) -> None:
        async with self._state_lock:
            context_ids = list(self._context_requests)
        for context_id in context_ids:
            requests = await self._discard_context(context_id)
            for request in requests:
                await self._dispose_request(request)
            if remove_contexts and self.audio_context_available(context_id):
                await self.remove_audio_context(context_id)
        await self.stop_all_metrics()

    async def _dispose_request(self, request: _SynthesisRequest) -> None:
        task = request.decoder_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if request.decoder is not None:
            await self._terminate_decoder(request.decoder)

    async def _terminate_decoder(self, decoder: Any) -> None:
        with contextlib.suppress(Exception):
            decoder.stdin.close()
        if getattr(decoder, "returncode", None) is not None:
            return
        with contextlib.suppress(Exception):
            decoder.terminate()
        try:
            await asyncio.wait_for(decoder.wait(), timeout=1.0)
            return
        except (TimeoutError, Exception):
            pass
        with contextlib.suppress(Exception):
            decoder.kill()
        with contextlib.suppress(Exception):
            await decoder.wait()

    async def flush_audio(self, context_id: str | None = None) -> None:
        """Mark the LLM turn complete without cutting off in-flight sentences."""
        context_id = context_id or self._turn_context_id
        if not context_id:
            return
        async with self._state_lock:
            self._flushed_contexts.add(context_id)
            should_finish_context = context_id not in self._context_requests
            if should_finish_context:
                self._flushed_contexts.discard(context_id)
        if should_finish_context and self.audio_context_available(context_id):
            await self.remove_audio_context(context_id)

    async def on_audio_context_interrupted(self, context_id: str) -> None:
        """Drop locally buffered MP3/PCM immediately after user interruption."""
        requests = await self._discard_context(context_id)
        for request in requests:
            await self._dispose_request(request)
        if requests:
            await self.stop_all_metrics()
        # MiniStream binary messages are not request-addressable.  Reconnect
        # before the next sentence so a delayed chunk from the interrupted
        # stream cannot be attributed to a later provider ``start`` event.
        await self._close_websocket()

    async def on_audio_context_completed(self, context_id: str) -> None:
        """Discard a request that outlived Pipecat's audio-context timeout.

        Pipecat invokes this only after removing the context.  A request which
        never produced a provider ``start``/``end`` would otherwise remain
        live and could later attach unaddressable MP3 bytes to a recreated
        context ID.  Closing the socket makes that stale provider stream
        unreachable before a future request reconnects.
        """
        requests = await self._discard_context(context_id)
        for request in requests:
            await self._dispose_request(request)
        if requests:
            await self._close_websocket()
            await self.stop_all_metrics()
        await super().on_audio_context_completed(context_id)
        await self._dispatch_pending_requests()

    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        """Send one sentence promptly; audio arrives through the receiver task."""
        if not text:
            yield None
            return
        if len(text) > self._max_generate_length:
            await self._fail_context(
                context_id, "MiniStream TTS input exceeds the configured limit"
            )
            yield None
            return

        request_id = uuid.uuid4().hex
        request = _SynthesisRequest(
            request_id=request_id, context_id=context_id, text=text
        )
        async with self._state_lock:
            self._requests[request_id] = request
            self._context_requests.setdefault(context_id, set()).add(request_id)
            self._pending_request_ids.append(request_id)

        try:
            await self._dispatch_pending_requests()
        except asyncio.CancelledError:
            requests = await self._discard_context(context_id)
            for pending_request in requests:
                await self._dispose_request(pending_request)
            await self._close_websocket()
            raise
        except Exception:
            await self._fail_context(
                context_id, "MiniStream TTS synthesis request failed"
            )

        # The receiver task owns streaming audio and context completion.
        yield None
