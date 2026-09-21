"""Process-local client turn-boundary callbacks for headless WebRTC calls.

The signaling WebSocket and its pipeline run in the same API worker.  This
small registry lets the push-to-talk client tell the direct transcript
processor when the microphone drain has completed, so multiple finalized ASR
sentences can be committed as one user turn.
"""

from collections.abc import Awaitable, Callable


VoiceTurnBoundaryHandler = Callable[[], Awaitable[None]]

_handlers: dict[int, VoiceTurnBoundaryHandler] = {}


def register_voice_turn_boundary_handler(
    workflow_run_id: int, handler: VoiceTurnBoundaryHandler
) -> None:
    _handlers[workflow_run_id] = handler


def unregister_voice_turn_boundary_handler(workflow_run_id: int) -> None:
    _handlers.pop(workflow_run_id, None)


async def notify_voice_turn_boundary(workflow_run_id: int) -> bool:
    handler = _handlers.get(workflow_run_id)
    if handler is None:
        return False
    await handler()
    return True


__all__ = [
    "notify_voice_turn_boundary",
    "register_voice_turn_boundary_handler",
    "unregister_voice_turn_boundary_handler",
]
