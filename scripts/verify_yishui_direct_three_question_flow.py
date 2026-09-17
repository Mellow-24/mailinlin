#!/usr/bin/env python3
"""Exercise the real Qwen model through the direct voice turn-state processor."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loguru import logger
from pipecat.frames.frames import LLMContextFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from api.db import db_client
from api.services.configuration.ai_model_configuration import (
    get_effective_ai_model_configuration_for_workflow,
)
from api.services.pipecat.consultation_response_guard_processor import (
    sanitize_intake_reply,
)
from api.services.pipecat.direct_final_transcript_processor import (
    DirectFinalTranscriptProcessor,
)
from api.services.pipecat.service_factory import create_llm_service
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
)
from api.services.workflow.workflow_graph import WorkflowGraph
from api.utils.template_renderer import render_template


def _question_count(text: str) -> int:
    return text.count("？") + text.count("?")


def _node_data(definition: dict, node_type: str) -> dict:
    matches = [
        node.get("data", {})
        for node in definition.get("nodes", [])
        if node.get("type") == node_type
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {node_type}, found {len(matches)}")
    return matches[0]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--organization-id", type=int, default=1)
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    workflow = await db_client.get_workflow(
        args.workflow_id,
        organization_id=args.organization_id,
    )
    if workflow is None or workflow.released_definition is None:
        raise SystemExit("Published workflow not found")
    released = workflow.released_definition
    definition = released.workflow_json
    configurations = released.workflow_configurations or {}
    if configurations.get("direct_consultation_question_limit") != 3:
        raise SystemExit("Published workflow does not enable the three-question flow")

    model_config = await get_effective_ai_model_configuration_for_workflow(
        organization_id=args.organization_id,
        workflow_configurations=configurations,
    )
    if model_config.llm.model != "qwen-flash":
        raise SystemExit(f"Expected qwen-flash, got {model_config.llm.model}")
    if model_config.llm.enable_thinking is not False:
        raise SystemExit("Qwen thinking must remain disabled")
    if (model_config.llm.max_tokens or 0) < 320:
        raise SystemExit("Qwen max_tokens must be at least 320")

    graph = WorkflowGraph(ReactFlowDTO.model_validate(definition))
    start_node = graph.nodes[graph.start_node_id]
    system_prompt = compose_system_prompt_for_node(
        node=start_node,
        workflow=graph,
        format_prompt=lambda prompt: str(
            render_template(prompt, {"remembered_summary": "暫無"}) or ""
        ),
        has_recordings=False,
    )
    opening = str(_node_data(definition, "startCall").get("greeting") or "").strip()
    context = LLMContext(messages=[{"role": "assistant", "content": opening}])
    processor = DirectFinalTranscriptProcessor(
        context,
        debounce_seconds=0,
        consultation_questions=3,
    )
    captured: list[LLMContextFrame] = []

    async def capture(frame, _direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, LLMContextFrame):
            captured.append(frame)

    processor.push_frame = capture
    llm = create_llm_service(model_config)
    inputs = (
        "我想睇下睡房風水，最近成日瞓得唔好。",
        "床頭靠窗，夜晚有街燈照入嚟。",
        "房門對住床尾，間房比較細，唔方便搬床。",
        "品我今晚最先做邊一樣。",
    )
    replies: list[str] = []
    try:
        for index, user_text in enumerate(inputs, start=1):
            before = len(captured)
            await processor.process_frame(
                TranscriptionFrame(
                    text=user_text,
                    user_id="direct-regression",
                    timestamp=f"2026-09-01T04:00:0{index}Z",
                    finalized=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            commit_task = processor._commit_task
            if commit_task is not None:
                await commit_task
            if len(captured) != before + 1:
                raise RuntimeError("A user turn did not produce exactly one LLM frame")
            raw_response = (
                await llm.run_inference(
                    captured[-1].context,
                    max_tokens=120 if index <= 2 else 320 if index == 3 else 200,
                    system_instruction=system_prompt,
                )
                or ""
            ).strip()
            response = (
                sanitize_intake_reply(raw_response, user_text)
                if index <= 2
                else raw_response
            )
            replies.append(response)
            context.add_message({"role": "assistant", "content": response})
    finally:
        await processor.cleanup()
        cleanup = getattr(llm, "cleanup", None)
        if callable(cleanup):
            await cleanup()

    question_counts = [_question_count(reply) for reply in replies]
    last_question = replies[1][
        max(replies[1].rfind("。"), replies[1].rfind("！")) + 1 :
    ]
    checks = {
        "one_llm_call_per_turn": len(captured) == len(inputs),
        "question_counts_are_1_1_0_0": question_counts == [1, 1, 0, 0],
        "second_question_is_single_fact": not any(
            connector in last_question
            for connector in ("或者", "同埋", "以及")
        ),
        "final_is_complete_spoken_analysis": (
            len(replies[2]) >= 120
            and replies[2].endswith(("。", "！", "!"))
            and "**" not in replies[2]
        ),
        "followup_does_not_restart_intake": _question_count(replies[3]) == 0,
        "internal_marker_not_leaked": all(
            "YISHUI_INTERNAL" not in reply for reply in replies
        ),
    }
    print(
        json.dumps(
            {
                "definition_version": released.version_number,
                "model": model_config.llm.model,
                "max_tokens": model_config.llm.max_tokens,
                "question_counts": question_counts,
                "checks": checks,
                "replies": replies,
            },
            ensure_ascii=False,
        )
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
