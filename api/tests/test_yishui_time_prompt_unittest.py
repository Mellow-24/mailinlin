"""The time-only prompt update must not reset the user's demo configuration."""

from copy import deepcopy
from unittest import TestCase

from api.services.workflow.initial_context import VOICE_DEMO_TIME_POLICY
from scripts.optimize_yishui_agent import update_time_prompt


class TestYishuiTimePrompt(TestCase):
    def test_updates_only_global_time_policy_and_is_idempotent(self) -> None:
        definition = {
            "nodes": [
                {
                    "id": "global",
                    "type": "globalNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"name": "Custom role", "prompt": "保留原有人設。"},
                },
                {
                    "id": "start",
                    "type": "startCall",
                    "position": {"x": 0, "y": 100},
                    "data": {
                        "name": "Custom consultation",
                        "prompt": "保留三問流程。",
                        "greeting": "保留暖場語。",
                        "greeting_type": "text",
                        "add_global_prompt": True,
                    },
                },
            ],
            "edges": [],
        }
        original = deepcopy(definition)
        updated = update_time_prompt(definition)

        self.assertEqual(definition, original)
        self.assertEqual(update_time_prompt(updated), updated)
        self.assertEqual(
            updated["nodes"][0]["data"]["prompt"],
            f"保留原有人設。\n\n{VOICE_DEMO_TIME_POLICY}",
        )
        updated["nodes"][0]["data"]["prompt"] = "保留原有人設。"
        self.assertEqual(updated, original)


if __name__ == "__main__":
    import unittest

    unittest.main()
