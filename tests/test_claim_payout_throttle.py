from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest import mock

import cache
import claim_payout_throttle
import database as schema


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


class ClaimPayoutReservationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def _reserve(self, *, claim_id: int, amount: int = 100) -> dict:
        async with schema.get_db() as db:
            return await claim_payout_throttle.reserve_payout_slot(
                db,
                claim_id=claim_id,
                amount=amount,
            )

    async def test_concurrent_workers_cannot_both_take_last_payout_slot(self):
        """BEGIN IMMEDIATE serialises the final-slot decision across connections."""
        with (
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 1),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 1_000_000),
        ):
            first, second = await asyncio.gather(
                self._reserve(claim_id=101),
                self._reserve(claim_id=202),
            )

        allowed = [result for result in (first, second) if result.get("allow")]
        blocked = [result for result in (first, second) if not result.get("allow")]
        self.assertEqual(len(allowed), 1)
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["reason"], "global_payout_count_limit")

    async def test_retrying_same_claim_reuses_reservation_without_extra_slot(self):
        with (
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 1),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 1_000_000),
        ):
            first = await self._reserve(claim_id=303)
            retry = await self._reserve(claim_id=303)
            unrelated = await self._reserve(claim_id=404)

        self.assertTrue(first["allow"])
        self.assertTrue(first.get("reservation_created"))
        self.assertTrue(retry["allow"])
        self.assertTrue(retry.get("reservation_reused"))
        self.assertFalse(unrelated["allow"])
        self.assertEqual(unrelated["reason"], "global_payout_count_limit")


if __name__ == "__main__":
    unittest.main()
