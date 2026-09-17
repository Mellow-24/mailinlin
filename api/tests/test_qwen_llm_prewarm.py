"""Regression test for the data-minimal voice-call LLM warmup request."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from pipecat.services.qwen.llm import QwenLLMSettings

from api.services.pipecat.service_factory import DograhQwenLLMService


class TestQwenLLMPrewarm(IsolatedAsyncioTestCase):
    async def test_voice_client_fails_fast_without_sdk_retries(self) -> None:
        service = DograhQwenLLMService(
            api_key="sk-dashscope-test",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            settings=QwenLLMSettings(model="qwen-flash"),
        )

        try:
            self.assertEqual(service._client.max_retries, 0)
            self.assertEqual(service._client.timeout.connect, 12.0)
            self.assertEqual(service._client.timeout.read, 12.0)
        finally:
            await service._client.close()

    async def test_prewarm_uses_only_generic_one_token_input(self) -> None:
        service = object.__new__(DograhQwenLLMService)
        service._settings = SimpleNamespace(model="qwen-flash")
        create = AsyncMock(return_value=SimpleNamespace())
        service._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        await service.prewarm()

        create.assert_awaited_once_with(
            model="qwen-flash",
            messages=[{"role": "user", "content": "回复好"}],
            temperature=0,
            max_tokens=1,
            extra_body={"enable_thinking": False},
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
