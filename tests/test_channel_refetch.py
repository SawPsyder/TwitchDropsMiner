"""
Tests for the CHANNEL_SWITCH channel-refetch fallback.

When the miner finds nothing watchable among the currently tracked channels
(e.g. every channel for the active game drifted offline over a long watch
session), it should re-fetch the directory a bounded number of times before
falling back to IDLE - so it can pick up the next queue item instead of
stalling until the periodic maintenance reload fires.
"""

import unittest

from src.config import CHANNEL_REFETCH_BACKOFF, CHANNEL_REFETCH_MAX_ATTEMPTS
from src.core.client import Twitch


def _bare_client() -> Twitch:
    """A Twitch instance without the heavy __init__ (only the field we need)."""
    twitch = Twitch.__new__(Twitch)
    twitch._channel_refetch_attempts = 0
    return twitch


class TestChannelRefetchPlan(unittest.TestCase):
    def test_backoff_schedule_matches_attempt_count(self):
        # the escalating backoff must cover every attempt without indexing past
        # its end (the plan clamps to the last entry, but they should line up)
        self.assertEqual(len(CHANNEL_REFETCH_BACKOFF), CHANNEL_REFETCH_MAX_ATTEMPTS)

    def test_retries_up_to_the_limit_then_gives_up(self):
        twitch = _bare_client()

        # every attempt up to the limit asks for a re-fetch, with the matching backoff
        for attempt, expected_backoff in enumerate(CHANNEL_REFETCH_BACKOFF, start=1):
            should_refetch, backoff = twitch._plan_channel_refetch()
            self.assertTrue(should_refetch, f"attempt {attempt} should re-fetch")
            self.assertEqual(backoff, float(expected_backoff))
            self.assertEqual(twitch._channel_refetch_attempts, attempt)

        # once exhausted, it gives up (caller falls back to IDLE) and resets
        should_refetch, backoff = twitch._plan_channel_refetch()
        self.assertFalse(should_refetch)
        self.assertEqual(backoff, 0.0)
        self.assertEqual(twitch._channel_refetch_attempts, 0)

    def test_first_retry_is_immediate(self):
        # the common cause is a stale tracked list a fresh fetch fixes at once,
        # so the very first retry must not wait
        twitch = _bare_client()
        should_refetch, backoff = twitch._plan_channel_refetch()
        self.assertTrue(should_refetch)
        self.assertEqual(backoff, 0.0)

    def test_cycle_restarts_after_giving_up(self):
        twitch = _bare_client()
        # exhaust one full cycle
        for _ in range(CHANNEL_REFETCH_MAX_ATTEMPTS):
            twitch._plan_channel_refetch()
        should_refetch, _backoff = twitch._plan_channel_refetch()  # gives up, resets
        self.assertFalse(should_refetch)
        # a subsequent stall gets a fresh set of attempts
        should_refetch, backoff = twitch._plan_channel_refetch()
        self.assertTrue(should_refetch)
        self.assertEqual(backoff, 0.0)
        self.assertEqual(twitch._channel_refetch_attempts, 1)

    def test_reset_restarts_backoff(self):
        twitch = _bare_client()
        twitch._plan_channel_refetch()
        twitch._plan_channel_refetch()
        self.assertEqual(twitch._channel_refetch_attempts, 2)
        # a successful watch resets the counter mid-way
        twitch._channel_refetch_attempts = 0
        should_refetch, backoff = twitch._plan_channel_refetch()
        self.assertTrue(should_refetch)
        self.assertEqual(backoff, 0.0)
        self.assertEqual(twitch._channel_refetch_attempts, 1)


if __name__ == "__main__":
    unittest.main()
