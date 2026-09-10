from __future__ import annotations

import asyncio
import contextlib
import hashlib
import tempfile
import time
import unittest
from unittest import mock

import cache
import claim_payout_throttle
import constants as const
import database as schema
import db_access


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

    def test_single_large_leg_is_deferred_by_absolute_automatic_limit(self):
        with mock.patch.object(claim_payout_throttle, "MAX_AUTOMATIC_PAYOUT_LUNA", 1_000):
            decision = claim_payout_throttle.throttle_decision(
                state=self._state(count=0, amount=0),
                amount=5_000,
            )
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "individual_automatic_payout_limit")
        self.assertTrue(decision["manual_review"])

    def test_daily_limit_catches_attempts_spaced_beyond_short_window(self):
        daily = self._state(count=5, amount=900)
        with (
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 50),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", 1_000),
        ):
            decision = claim_payout_throttle.throttle_decision(
                state=self._state(count=0, amount=0), daily_state=daily, amount=200
            )
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "daily_payout_amount_limit")

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

    def test_open_spot_amount_limit_is_inclusive_and_manual_reviewed(self):
        state = self._state(count=2, amount=900)
        with mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_LUNA", 1_000):
            at_boundary = claim_payout_throttle._spot_window_decision(
                state=state, amount=100, spot_id=42
            )
            beyond = claim_payout_throttle._spot_window_decision(
                state=state, amount=101, spot_id=42
            )
        self.assertTrue(at_boundary["allow"])
        self.assertFalse(beyond["allow"])
        self.assertTrue(beyond["manual_review"])
        self.assertEqual(beyond["reason"], "open_spot_payout_amount_limit")
        self.assertEqual(beyond["spot_id"], 42)

    def test_zero_lifetime_percent_is_manual_review_emergency_mode(self):
        spot = {
            schema.SPOT_ID: 42,
            schema.SPOT_MAX_TOTAL_CLAIMS: 20,
            schema.SPOT_TOTAL_VALUE: 2_000 * const.LUNA_PER_NIM,
        }
        with mock.patch.object(claim_payout_throttle, "SPOT_LIFETIME_AUTOMATIC_PERCENT", 0):
            decision = claim_payout_throttle._spot_lifetime_decision(
                state=self._state(count=0, amount=0), amount=100, spot=spot
            )
        self.assertFalse(decision["allow"])
        self.assertTrue(decision["manual_review"])
        self.assertEqual(decision["reason"], "open_spot_lifetime_count_limit")


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

    async def _open_spot_claims(
        self,
        count: int,
        *,
        use_password: bool = False,
        is_prizedraw: bool = False,
        total_value: int | None = None,
    ) -> tuple[int, list[int]]:
        async with schema.get_db() as db:
            owner = await db_access.create_user(
                db, device_id_hash=hashlib.sha256(b"exposure-owner").hexdigest()
            )
            create = db_access.create_prizedraw if is_prizedraw else db_access.create_spot
            spot_id = await create(
                db,
                created_by=owner,
                title="Exposure boundary",
                lat=51.5,
                long=-0.1,
                radius=100,
                claim_duration=0,
                max_claims_per_user=1,
                max_total_claims=count,
                total_value=total_value or max(
                    const.MIN_SPOT_TOTAL_VALUE, count * const.MIN_STANDARD_CLAIM_PAYOUT
                ),
                starts_at=int(time.time()) - 60,
                ends_at=24 * 60 * 60,
                use_password=use_password,
                auto_reverse_geocode=False,
            )
            claim_ids = []
            for index in range(count):
                user_id = await db_access.create_user(
                    db,
                    device_id_hash=hashlib.sha256(f"sybil-{index}".encode()).hexdigest(),
                )
                claim_id = await db_access.create_claim(
                    db,
                    spot_id=spot_id,
                    user_id=user_id,
                    lat=51.5,
                    long=-0.1,
                    accuracy=1.0,
                    payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
                )
                await claim_payout_throttle.claim_security._metadata_set(
                    db,
                    claim_payout_throttle.claim_security._claim_record_key(claim_id),
                    {"claim_id": claim_id, "spot_id": spot_id, "manual_review": False},
                )
                claim_ids.append(claim_id)
            await db.commit()
        return spot_id, claim_ids

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

    async def test_large_payout_hold_is_durable_and_explicitly_releasable(self):
        claim_id = 505
        async with schema.get_db() as db:
            await claim_payout_throttle.claim_security._metadata_set(
                db,
                claim_payout_throttle.claim_security._claim_record_key(claim_id),
                {"claim_id": claim_id, "manual_review": False},
            )
            await db.commit()

        with mock.patch.object(claim_payout_throttle, "MAX_AUTOMATIC_PAYOUT_LUNA", 10):
            held = await self._reserve(claim_id=claim_id, amount=11)
        self.assertFalse(held["allow"])
        self.assertEqual(held["reason"], "individual_automatic_payout_limit")

        async with schema.get_db() as db:
            record = await claim_payout_throttle.claim_security.get_claim_security_record(
                db, claim_id=claim_id
            )
            self.assertTrue(record["manual_review"])
            self.assertEqual(record["manual_review_reason"], held["reason"])
            released = await claim_payout_throttle.claim_security.release_claim_manual_review(
                db, claim_id=claim_id
            )
            await db.commit()
            self.assertTrue(released)
            record = await claim_payout_throttle.claim_security.get_claim_security_record(
                db, claim_id=claim_id
            )
        self.assertFalse(record["manual_review"])

    async def test_fifty_fresh_identities_cannot_drain_one_open_spot(self):
        spot_id, claim_ids = await self._open_spot_claims(50)
        amount = const.MIN_STANDARD_CLAIM_PAYOUT
        with (
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 1_000),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 1_000),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_COUNT", 10),
            mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_LUNA", 10**15),
        ):
            results = [await self._reserve(claim_id=claim_id, amount=amount) for claim_id in claim_ids]

        self.assertEqual(sum(bool(result["allow"]) for result in results), 10)
        self.assertTrue(all(result.get("manual_review") for result in results[10:]))
        self.assertTrue(all(result["spot_id"] == spot_id for result in results[10:]))
        async with schema.get_db() as db:
            claim = await db_access.get_claim(db, claim_id=claim_ids[-1])
            record = await claim_payout_throttle.claim_security.get_claim_security_record(
                db, claim_id=claim_ids[-1]
            )
        self.assertEqual(int(claim[schema.CLAIM_STATUS]), const.CLAIM_STATUS_PENDING)
        self.assertEqual(record["manual_review_reason"], "open_spot_payout_count_limit")
        self.assertEqual(record["manual_review_details"]["spot_id"], spot_id)

    async def test_default_lifetime_policy_protects_small_open_spots(self):
        for capacity, expected_automatic in ((1, 0), (2, 1), (4, 2), (10, 5), (20, 10), (50, 10)):
            with self.subTest(capacity=capacity):
                # Each subcase needs an independent database because durable
                # reservations intentionally survive for the Spot lifetime.
                await self.asyncTearDown()
                await self.asyncSetUp()
                _spot_id, claim_ids = await self._open_spot_claims(capacity)
                with (
                    mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 1_000),
                    mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10**15),
                    mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 1_000),
                    mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", 10**15),
                ):
                    results = [await self._reserve(claim_id=claim_id) for claim_id in claim_ids]
                self.assertEqual(sum(bool(result["allow"]) for result in results), expected_automatic)
                self.assertTrue(all(result.get("manual_review") for result in results[expected_automatic:]))

    async def test_concurrent_open_spot_claims_share_atomic_final_allowance(self):
        _spot_id, claim_ids = await self._open_spot_claims(2)
        with (
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 100),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 100),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_COUNT", 1),
            mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_LUNA", 10**15),
        ):
            results = await asyncio.gather(*(
                self._reserve(claim_id=claim_id, amount=100) for claim_id in claim_ids
            ))
        self.assertEqual(sum(bool(result["allow"]) for result in results), 1)
        self.assertEqual(
            [result for result in results if not result["allow"]][0]["reason"],
            "open_spot_lifetime_count_limit",
        )

    async def test_restart_preserves_lifetime_exposure(self):
        _spot_id, claim_ids = await self._open_spot_claims(2)
        self.assertTrue((await self._reserve(claim_id=claim_ids[0]))["allow"])
        await cache.force_all_cache_clear()
        await schema.init_db()
        after_restart = await self._reserve(claim_id=claim_ids[1])
        self.assertFalse(after_restart["allow"])
        self.assertEqual(after_restart["reason"], "open_spot_lifetime_count_limit")

    async def test_high_value_reward_hits_amount_boundary_before_count_boundary(self):
        total_value = 20_000 * const.LUNA_PER_NIM
        _spot_id, claim_ids = await self._open_spot_claims(4, total_value=total_value)
        reward = total_value // 4
        first = await self._reserve(claim_id=claim_ids[0], amount=reward)
        second = await self._reserve(claim_id=claim_ids[1], amount=reward)
        self.assertTrue(first["allow"])
        self.assertFalse(second["allow"])
        self.assertEqual(second["reason"], "open_spot_payout_amount_limit")

    async def test_operator_release_bypasses_spot_cap_but_not_global_cap(self):
        _spot_id, claim_ids = await self._open_spot_claims(2)
        with (
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 100),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 100),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_COUNT", 1),
            mock.patch.object(claim_payout_throttle, "SPOT_MAX_PAYOUT_LUNA", 10**15),
        ):
            self.assertTrue((await self._reserve(claim_id=claim_ids[0]))["allow"])
            self.assertFalse((await self._reserve(claim_id=claim_ids[1]))["allow"])
            async with schema.get_db() as db:
                self.assertTrue(await claim_payout_throttle.claim_security.release_claim_manual_review(
                    db, claim_id=claim_ids[1]
                ))
                await db.commit()
            with mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 1):
                global_block = await self._reserve(claim_id=claim_ids[1])
            self.assertFalse(global_block["allow"])
            self.assertEqual(global_block["reason"], "global_payout_count_limit")
            self.assertTrue((await self._reserve(claim_id=claim_ids[1]))["allow"])

    async def test_paced_sybil_attack_remains_bounded_in_each_rolling_day(self):
        _spot_id, claim_ids = await self._open_spot_claims(31)
        base = 1_700_000_000
        # Three separately-paced bursts can never reserve more than ten in
        # their respective rolling 24-hour windows.
        allowed_per_day = []
        batches = (claim_ids[:11], claim_ids[11:21], claim_ids[21:31])
        for day, batch in enumerate(batches):
            with contextlib.ExitStack() as stack:
                for name, value in (
                    ("MAX_PAYOUT_COUNT", 1_000),
                    ("MAX_PAYOUT_LUNA", 10**15),
                    ("DAILY_MAX_PAYOUT_COUNT", 1_000),
                    ("DAILY_MAX_PAYOUT_LUNA", 10**15),
                    ("SPOT_MAX_PAYOUT_COUNT", 10),
                    ("SPOT_MAX_PAYOUT_LUNA", 10**15),
                ):
                    stack.enter_context(mock.patch.object(claim_payout_throttle, name, value))
                stack.enter_context(mock.patch.object(
                    claim_payout_throttle.db_access,
                    "get_unixepoch",
                    mock.AsyncMock(return_value=base + day * (24 * 60 * 60 + 1)),
                ))
                results = [await self._reserve(claim_id=claim_id) for claim_id in batch]
            allowed_per_day.append(sum(bool(result["allow"]) for result in results))
        self.assertEqual(allowed_per_day, [10, 5, 0])

    async def test_code_protected_and_prizedraw_spots_are_intentionally_exempt(self):
        for options in ({"use_password": True}, {"is_prizedraw": True}):
            with self.subTest(options=options):
                await self.asyncTearDown()
                await self.asyncSetUp()
                _spot_id, claim_ids = await self._open_spot_claims(2, **options)
                with (
                    mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 100),
                    mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10**15),
                    mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 100),
                    mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", 10**15),
                ):
                    results = [await self._reserve(claim_id=claim_id) for claim_id in claim_ids]
                self.assertTrue(all(result["allow"] for result in results), results)

    async def test_long_spot_window_does_not_lengthen_global_daily_window(self):
        _spot_id, claim_ids = await self._open_spot_claims(2, use_password=True)
        now = 1_700_000_000
        async with schema.get_db() as db:
            await claim_payout_throttle.claim_security._metadata_set(
                db,
                claim_payout_throttle.RESERVATION_KEY,
                [{
                    "claim_id": claim_ids[0],
                    "amount": 100,
                    "reserved_at": now - 2 * 24 * 60 * 60,
                    "spot_id": _spot_id,
                }],
            )
            await db.commit()
        with (
            mock.patch.object(claim_payout_throttle, "SPOT_WINDOW_SECONDS", 3 * 24 * 60 * 60),
            mock.patch.object(claim_payout_throttle, "DAILY_WINDOW_SECONDS", 24 * 60 * 60),
            mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", 1),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", 100),
            mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", 10**15),
            mock.patch.object(claim_payout_throttle.db_access, "get_unixepoch", mock.AsyncMock(return_value=now)),
        ):
            result = await self._reserve(claim_id=claim_ids[1])
        self.assertTrue(result["allow"])

    async def test_lifetime_reservation_rechecks_each_current_global_window(self):
        spot_id, claim_ids = await self._open_spot_claims(4)
        base = 1_700_000_000

        async def reserve_at(when: int, *, max_count: int = 1, max_amount: int = 1_000):
            with (
                mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_COUNT", max_count),
                mock.patch.object(claim_payout_throttle, "MAX_PAYOUT_LUNA", max_amount),
                mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_COUNT", max_count),
                mock.patch.object(claim_payout_throttle, "DAILY_MAX_PAYOUT_LUNA", max_amount),
                mock.patch.object(
                    claim_payout_throttle.db_access,
                    "get_unixepoch",
                    mock.AsyncMock(return_value=when),
                ),
            ):
                return await self._reserve(claim_id=claim_ids[0], amount=100)

        first = await reserve_at(base)
        inside_short = await reserve_at(base + 60)
        inside_daily = await reserve_at(base + claim_payout_throttle.WINDOW_SECONDS + 60)
        self.assertTrue(first["allow"])
        self.assertTrue(inside_short["allow"])
        self.assertTrue(inside_daily["allow"])

        after_daily = base + claim_payout_throttle.DAILY_WINDOW_SECONDS + 120
        async with schema.get_db() as db:
            reservations = await claim_payout_throttle.claim_security._metadata_get(
                db, claim_payout_throttle.RESERVATION_KEY
            )
            reservations.append({
                "claim_id": claim_ids[1],
                "amount": 100,
                "reserved_at": after_daily,
                "short_reserved_at": after_daily,
                "daily_reserved_at": after_daily,
                "spot_reserved_at": after_daily,
                "spot_id": spot_id,
            })
            await claim_payout_throttle.claim_security._metadata_set(
                db, claim_payout_throttle.RESERVATION_KEY, reservations
            )
            await db.commit()

        count_block = await reserve_at(after_daily, max_count=1)
        amount_block = await reserve_at(after_daily, max_count=10, max_amount=100)
        self.assertFalse(count_block["allow"])
        self.assertEqual(count_block["reason"], "daily_payout_count_limit")
        self.assertFalse(amount_block["allow"])
        self.assertEqual(amount_block["reason"], "daily_payout_amount_limit")

        with mock.patch.object(claim_payout_throttle, "MAX_AUTOMATIC_PAYOUT_LUNA", 99):
            individual_block = await reserve_at(after_daily, max_count=10, max_amount=1_000)
        self.assertFalse(individual_block["allow"])
        self.assertEqual(individual_block["reason"], "individual_automatic_payout_limit")

        async with schema.get_db() as db:
            reservations = await claim_payout_throttle.claim_security._metadata_get(
                db, claim_payout_throttle.RESERVATION_KEY
            )
        original = next(item for item in reservations if item["claim_id"] == claim_ids[0])
        self.assertEqual(original["reserved_at"], base)

    async def test_materialised_retry_cannot_create_duplicate_transaction(self):
        _spot_id, claim_ids = await self._open_spot_claims(2)
        claim_id = claim_ids[0]
        self.assertTrue((await self._reserve(claim_id=claim_id))["allow"])
        async with schema.get_db() as db:
            claim = await db_access.get_claim(db, claim_id=claim_id)
            await db_access.create_claim_transaction(
                db,
                user_id=int(claim[schema.CLAIM_RECIPIENT]),
                claim_id=claim_id,
                amount=100,
                from_address=const.DEV_PLATFORM_FEE_ADDRESS,
                to_address=const.DEV_PLATFORM_FEE_ADDRESS,
                tx_hash="materialised-reservation-test",
            )
            await db.commit()

        retry = await self._reserve(claim_id=claim_id)
        self.assertTrue(retry["allow"])
        self.assertEqual(retry["reason"], "claim_payout_already_materialised")
        async with schema.get_db() as db:
            with self.assertRaisesRegex(RuntimeError, "already has a non-failed payout"):
                await db_access.create_claim_transaction(
                    db,
                    user_id=int(claim[schema.CLAIM_RECIPIENT]),
                    claim_id=claim_id,
                    amount=100,
                    from_address=const.DEV_PLATFORM_FEE_ADDRESS,
                    to_address=const.DEV_PLATFORM_FEE_ADDRESS,
                    tx_hash="duplicate-materialised-reservation-test",
                )


if __name__ == "__main__":
    unittest.main()
