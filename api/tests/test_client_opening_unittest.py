"""Dependency-light regression for the client-preloaded opening contract."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.workflow.dto import (
    EdgeDataDTO,
    EndCallNodeData,
    Position,
    ReactFlowDTO,
    RFEdgeDTO,
    RFNodeDTO,
    StartCallNodeData,
)
from api.services.workflow.initial_context import (
    CLIENT_OPENING_CONTEXT_KEY,
    CLIENT_OPENING_TRANSCRIPT_CONTEXT_KEY,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow_graph import WorkflowGraph


def _workflow() -> WorkflowGraph:
    return WorkflowGraph(
        ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type="startCall",
                    position=Position(x=0, y=0),
                    data=StartCallNodeData(
                        name="Start",
                        prompt="Help the caller.",
                        greeting_type="text",
                        greeting="Server greeting",
                        extraction_enabled=False,
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type="endCall",
                    position=Position(x=0, y=100),
                    data=EndCallNodeData(
                        name="End",
                        prompt="End politely.",
                        extraction_enabled=False,
                    ),
                ),
            ],
            edges=[
                RFEdgeDTO(
                    id="edge",
                    source="start",
                    target="end",
                    data=EdgeDataDTO(label="End", condition="Caller asks to end"),
                )
            ],
        )
    )


class TestClientOpening(IsolatedAsyncioTestCase):
    async def test_seeds_context_without_server_tts_or_llm(self) -> None:
        transcript = "哈囉，我係易水 AI 語音顧問。"
        context = LLMContext()
        llm = Mock(queue_frame=AsyncMock())
        task = Mock(queue_frame=AsyncMock())
        workflow = _workflow()
        engine = PipecatEngine(
            llm=llm,
            context=context,
            workflow=workflow,
            call_context_vars={
                CLIENT_OPENING_CONTEXT_KEY: True,
                CLIENT_OPENING_TRANSCRIPT_CONTEXT_KEY: transcript,
            },
            workflow_run_id=1,
        )
        engine.set_task(task)

        result = await engine.queue_node_opening(
            node_id=workflow.start_node_id,
            generate_if_no_greeting=True,
        )

        self.assertEqual(result, "none")
        task.queue_frame.assert_not_awaited()
        llm.queue_frame.assert_not_awaited()
        self.assertEqual(
            context.messages,
            [{"role": "assistant", "content": transcript}],
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
