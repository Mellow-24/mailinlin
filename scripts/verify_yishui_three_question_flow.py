#!/usr/bin/env python3
"""Run a real-model regression for the published three-question consultation."""

from __future__ import annotations

import argparse
import asyncio
import time

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    complete_text_chat_session,
    default_text_chat_checkpoint,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
    normalize_text_chat_session_data,
)


def _latest_assistant_text(text_session) -> str:
    turns = normalize_text_chat_session_data(text_session.session_data)["turns"]
    return ((turns[-1].get("assistant_message") or {}).get("text") or "").strip()


def _question_count(text: str) -> int:
    return text.count("？") + text.count("?")


async def _send_turn(*, workflow_id: int, run_id: int, text_session, text: str):
    text_session = await append_text_chat_user_message(
        run_id=run_id,
        text_session=text_session,
        user_text=text,
        expected_revision=text_session.revision,
    )
    text_session = await execute_pending_text_chat_turn(
        workflow_id=workflow_id,
        run_id=run_id,
        text_session=text_session,
    )
    return text_session, _latest_assistant_text(text_session)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--organization-id", type=int, default=1)
    args = parser.parse_args()

    workflow = await db_client.get_workflow(
        args.workflow_id,
        organization_id=args.organization_id,
    )
    if workflow is None:
        raise SystemExit("Workflow not found")
    run_inputs = await prepare_workflow_run_inputs(
        db_client,
        workflow,
        initial_context={},
        use_draft=False,
        include_template_context=True,
    )
    run = await db_client.create_workflow_run(
        name="易水三问封顶自动回归",
        workflow_id=args.workflow_id,
        mode=WorkflowRunMode.TEXTCHAT.value,
        user_id=args.user_id,
        initial_context=run_inputs.initial_context,
        organization_id=args.organization_id,
        definition_id=run_inputs.definition_id,
    )
    text_session = await db_client.ensure_workflow_run_text_session(
        run.id,
        session_data=default_text_chat_session_data(),
        checkpoint=default_text_chat_checkpoint(),
    )

    started_at = time.monotonic()
    text_session = await initialize_text_chat_session(
        run_id=run.id,
        text_session=text_session,
    )
    text_session = await execute_pending_text_chat_turn(
        workflow_id=args.workflow_id,
        run_id=run.id,
        text_session=text_session,
    )
    opening = _latest_assistant_text(text_session)

    inputs = (
        "我想睇下睡房風水，最近成日瞓得唔好。",
        "床頭靠窗，夜晚有街燈照入嚟。",
        "房門對住床尾，間房比較細，唔方便搬床。",
        "品我今晚最先做邊一樣。",
    )
    replies: list[str] = []
    for user_text in inputs:
        text_session, reply = await _send_turn(
            workflow_id=args.workflow_id,
            run_id=run.id,
            text_session=text_session,
            text=user_text,
        )
        replies.append(reply)

    checks = {
        "opening_present": bool(opening),
        "answer_1_has_one_question": _question_count(replies[0]) == 1,
        "answer_2_has_one_final_question": (
            _question_count(replies[1]) == 1
            and any(term in replies[1] for term in ("最後", "最后", "最尾"))
        ),
        "answer_3_is_detailed_without_question": (
            _question_count(replies[2]) == 0
            and len(replies[2]) >= 120
            and "**" not in replies[2]
            and replies[2].endswith(("。", "！", "!"))
        ),
        "followup_is_direct_without_question": _question_count(replies[3]) == 0,
    }

    text_session = await complete_text_chat_session(
        run_id=run.id,
        text_session=text_session,
        expected_revision=text_session.revision,
    )
    print(
        {
            "run_id": run.id,
            "checks": checks,
            "elapsed_seconds": round(time.monotonic() - started_at, 2),
            "opening": opening,
            "replies": replies,
        }
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
