from __future__ import annotations

import unittest
from unittest import mock

import claim_payout_throttle


class ClaimPayoutThrottleTest(unittest.TestCase):
    def _state(self, *, count: int, amount: int, now: int = 1_700_000_000):
        return {
            "now": now,
            "cutoff": now - claim_payout_throttle.WINDOW_SECONDS,
            "payout_count": count,
            "payout_amount": amount,
            "oldest_created_at": now - 30 if count else None,
        }

    def test_count_limit_defers_without_failing_claim(self):
        with mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 3):
            decision = claim_payout_throttle.throttle_decision(
                state=self._state(count=3, amount=100),
                amount=50,
            )
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "global_payout_count_limit")
        self.assertGreater(decision["retry_at"], decision.get("window_payout_count", 0))

    def test_aggregate_amount_limit_defers_burst(self):
        with mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 1_000):
            decision = claim_payout_throttle.throttle_decision(
                state=self._state(count=2, amount=900),
                amount=200,
            )
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "global_payout_amount_limit")

    def test_single_large_leg_does_not_deadlock_forever(self):
        with mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 1_000):
            decision = claim_payout_throttle.throttle_decision(
                state=self._state(count=0, amount=0),
                amount=5_000,
            )
        self.assertTrue(decision["allow"])

    def test_normal_payout_is_allowed(self):
        with (
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 30),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10_000),
        ):
            decision = claim_payout_throttle.throttle_decision(
                state=self._state(count=4, amount=1_000),
                amount=500,
            )
        self.assertTrue(decision["allow"])
        self.assertEqual(decision["reason"], "within_global_payout_limits")


if __name__ == "__main__":
    unittest.main()
