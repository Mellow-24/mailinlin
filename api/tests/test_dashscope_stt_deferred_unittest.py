"""Dependency-light regression test for fast-opening DashScope STT gating."""

import asyncio
from unittest import IsolatedAsyncioTestCase

from api.services.dashscope.stt import DashScopeSTTService


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent_bytes: list[bytes] = []

    async def send_bytes(self, audio: bytes) -> None:
        self.sent_bytes.append(audio)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class TestDashScopeDeferredStart(IsolatedAsyncioTestCase):
    async def test_first_audio_waits_for_background_connection(self) -> None:
        service = DashScopeSTTService(api_key="test-key")
        service.enable_deferred_start()
        websocket = _FakeWebSocket()
        connection_ready = asyncio.Event()

        async def finish_connection() -> None:
            await connection_ready.wait()
            service._reset_task_state()
            service._websocket = websocket
            service._task_started.set()

        service._connect_task = asyncio.create_task(finish_connection())
        audio_task = asyncio.create_task(
            anext(service.run_stt(b"\x01\x02\x03\x04"))
        )
        await asyncio.sleep(0)

        self.assertEqual(websocket.sent_bytes, [])
        self.assertFalse(audio_task.done())

        connection_ready.set()
        self.assertIsNone(await audio_task)
        self.assertEqual(websocket.sent_bytes, [b"\x01\x02\x03\x04"])
        await service._disconnect()


if __name__ == "__main__":
    import unittest

    unittest.main()
