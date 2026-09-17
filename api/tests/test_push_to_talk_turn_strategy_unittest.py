"""Dependency-light regression for final-only ASR in headless push-to-talk."""

from unittest import TestCase

from pipecat.turns.user_start import ExternalUserTurnStartStrategy

from api.services.pipecat.run_pipeline import (
    _create_non_realtime_user_turn_start_strategies,
    _resolve_user_turn_stop_timeout,
)


class TestPushToTalkTurnStrategy(TestCase):
    def test_external_start_does_not_clear_adjacent_final_transcript(self) -> None:
        strategies = _create_non_realtime_user_turn_start_strategies(
            {},
            uses_external_turns=True,
            enable_interruptions=False,
        )

        self.assertEqual(len(strategies), 1)
        self.assertIsInstance(strategies[0], ExternalUserTurnStartStrategy)
        self.assertFalse(strategies[0]._enable_interruptions)
        self.assertEqual(
            _resolve_user_turn_stop_timeout(
                {"user_turn_stop_timeout": 0.8},
                uses_external_turns=True,
            ),
            5.0,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
