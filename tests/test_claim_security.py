from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from unittest import mock

import cache
import claim_security
import constants as const
import database as schema
import db_access


class ClaimSecurityTest(unittest.IsolatedAsyncioTestCase):
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

    async def _user(self, db, suffix: str) -> int:
        return await db_access.create_user(
            db,
            device_id_hash=hashlib.sha256(suffix.encode("utf-8")).hexdigest(),
        )

    async def _spot(
        self,
        db,
        *,
        suffix: str,
        lat: float = 51.5,
        long: float = -0.1,
        radius: int = 100,
    ) -> int:
        owner_id = await self._user(db, f"owner-{suffix}")
        spot_id = await db_access.create_spot(
            db,
            created_by=owner_id,
            title=f"Security {suffix}",
            lat=lat,
            long=long,
            radius=radius,
            claim_duration=0,
            max_claims_per_user=1,
            max_total_claims=10,
            total_value=10 * const.MIN_STANDARD_CLAIM_PAYOUT,
            starts_at=int(time.time()) - 60,
            ends_at=24 * 60 * 60,
            auto_reverse_geocode=False,
        )
        await db.execute(
            f"UPDATE {schema.SPOT_TABLE_NAME} SET {schema.SPOT_STATUS} = ? "
            f"WHERE {schema.SPOT_ID} = ?;",
            (const.SPOT_STATUS_PUBLISHED, int(spot_id)),
        )
        return int(spot_id)

    async def _claim(self, db, *, suffix: str = "claim") -> tuple[int, int, int]:
        user_id = await self._user(db, f"user-{suffix}")
        spot_id = await self._spot(db, suffix=suffix)
        claim_id = await db_access.create_claim(
            db,
            spot_id=spot_id,
            user_id=user_id,
            lat=51.5,
            long=-0.1,
            accuracy=1.0,
            payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
        )
        return int(user_id), int(spot_id), int(claim_id)

    def _event(
        self,
        *,
        claim_id: int,
        spot_id: int,
        claimed_at: int,
        lat: float,
        long: float,
        device: str,
        wallet_address: str,
        payout: str,
        ip_hash: str = "ip",
        centre_offset: float = 0.0,
        user_created_at: int | None = None,
        session_created_at: int | None = None,
    ) -> dict:
        return {
            "claim_id": int(claim_id),
            "spot_id": int(spot_id),
            "user_id": int(claim_id),
            "device_id_hash": device,
            "verified_wallet": wallet_address,
            "payout_address": payout,
            "ip_hash": ip_hash,
            "claimed_at": int(claimed_at),
            "user_created_at": int(user_created_at if user_created_at is not None else claimed_at - 10),
            "session_created_at": int(session_created_at if session_created_at is not None else claimed_at - 10),
            "spot_lat": float(lat),
            "spot_long": float(long),
            "spot_radius": 50,
            "centre_offset_metres": float(centre_offset),
            "manual_review": False,
        }

    async def test_missing_security_record_holds_public_payout(self):
        async with schema.get_db() as db:
            _user_id, _spot_id, claim_id = await self._claim(db, suffix="missing-record")
            await db.commit()
            with mock.patch.object(const, "PUBLIC_DEPLOYMENT", True):
                decision = await claim_security._payout_security_decision(
                    db,
                    claim_id=claim_id,
                )
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "security_record_missing")
        self.assertTrue(decision["manual_review"])

    async def test_development_keeps_spoof_claim_workflow_compatible(self):
        async with schema.get_db() as db:
            _user_id, _spot_id, claim_id = await self._claim(db, suffix="development")
            await db.commit()
            with mock.patch.object(const, "PUBLIC_DEPLOYMENT", False):
                decision = await claim_security._payout_security_decision(
                    db,
                    claim_id=claim_id,
                )
        self.assertTrue(decision["allow"])
        self.assertEqual(decision["reason"], "development_without_security_record")

    async def test_new_verified_claim_has_short_payout_hold(self):
        async with schema.get_db() as db:
            user_id, spot_id, claim_id = await self._claim(db, suffix="hold")
            claim = await db_access.get_claim(db, claim_id=claim_id)
            now = await db_access.get_unixepoch(db)
            await claim_security._metadata_set(
                db,
                claim_security._claim_record_key(claim_id),
                {
                    "claim_id": claim_id,
                    "spot_id": spot_id,
                    "user_id": user_id,
                    "claimed_at": int(claim[schema.CLAIM_CLAIMED_AT]),
                    "manual_review": False,
                },
            )
            await db.commit()
            decision = await claim_security._payout_security_decision(db, claim_id=claim_id)

        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "security_hold")
        self.assertGreaterEqual(decision["retry_at"], now)

    async def test_old_verified_claim_can_pass_payout_hold(self):
        async with schema.get_db() as db:
            user_id, spot_id, claim_id = await self._claim(db, suffix="old")
            now = await db_access.get_unixepoch(db)
            old_time = now - claim_security.PAYOUT_HOLD_SECONDS - 5
            await db.execute(
                f"UPDATE {schema.CLAIM_TABLE_NAME} SET {schema.CLAIM_CLAIMED_AT} = ? "
                f"WHERE {schema.CLAIM_ID} = ?;",
                (old_time, claim_id),
            )
            await claim_security._metadata_set(
                db,
                claim_security._claim_record_key(claim_id),
                {
                    "claim_id": claim_id,
                    "spot_id": spot_id,
                    "user_id": user_id,
                    "claimed_at": old_time,
                    "manual_review": False,
                },
            )
            await db.commit()
            decision = await claim_security._payout_security_decision(db, claim_id=claim_id)

        self.assertTrue(decision["allow"])
        self.assertEqual(decision["reason"], "security_checks_passed")

    async def test_manual_review_never_auto_releases_with_age(self):
        async with schema.get_db() as db:
            user_id, spot_id, claim_id = await self._claim(db, suffix="manual")
            now = await db_access.get_unixepoch(db)
            old_time = now - claim_security.PAYOUT_HOLD_SECONDS - 5
            await db.execute(
                f"UPDATE {schema.CLAIM_TABLE_NAME} SET {schema.CLAIM_CLAIMED_AT} = ? "
                f"WHERE {schema.CLAIM_ID} = ?;",
                (old_time, claim_id),
            )
            await claim_security._metadata_set(
                db,
                claim_security._claim_record_key(claim_id),
                {
                    "claim_id": claim_id,
                    "spot_id": spot_id,
                    "user_id": user_id,
                    "claimed_at": old_time,
                    "manual_review": True,
                    "manual_review_reason": "coordinated_test_burst",
                },
            )
            await db.commit()
            decision = await claim_security._payout_security_decision(db, claim_id=claim_id)

        self.assertFalse(decision["allow"])
        self.assertTrue(decision["manual_review"])
        self.assertEqual(decision["reason"], "coordinated_test_burst")

    def test_same_verified_wallet_cannot_teleport_via_new_device(self):
        now = 1_700_000_000
        previous = self._event(
            claim_id=1,
            spot_id=1,
            claimed_at=now,
            lat=51.5074,
            long=-0.1278,
            device="a" * 64,
            wallet_address="NQ WALLET SAME",
            payout="NQ PAYOUT A",
            ip_hash="ip-a",
        )
        target = self._event(
            claim_id=0,
            spot_id=2,
            claimed_at=now + 5,
            lat=40.7128,
            long=-74.0060,
            device="b" * 64,
            wallet_address="NQ WALLET SAME",
            payout="NQ PAYOUT B",
            ip_hash="ip-b",
        )

        decision = claim_security._preclaim_risk([previous], target)
        self.assertTrue(decision["blocked"])
        self.assertEqual(decision["reason"], "impossible_travel")
        self.assertEqual(decision["signal"], "verified wallet")

    def test_same_payout_address_links_fresh_wallets(self):
        now = 1_700_000_000
        previous = self._event(
            claim_id=1,
            spot_id=1,
            claimed_at=now,
            lat=-33.8568,
            long=151.2153,
            device="a" * 64,
            wallet_address="wallet-a",
            payout="same-payout",
            ip_hash="ip-a",
        )
        target = self._event(
            claim_id=0,
            spot_id=2,
            claimed_at=now + 10,
            lat=25.1972,
            long=55.2744,
            device="b" * 64,
            wallet_address="wallet-b",
            payout="same-payout",
            ip_hash="ip-b",
        )

        decision = claim_security._preclaim_risk([previous], target)
        self.assertTrue(decision["blocked"])
        self.assertEqual(decision["signal"], "payout address")

    def test_unrelated_nearby_users_are_not_linked(self):
        now = 1_700_000_000
        previous = self._event(
            claim_id=1,
            spot_id=1,
            claimed_at=now,
            lat=51.5000,
            long=-0.1000,
            device="a" * 64,
            wallet_address="wallet-a",
            payout="payout-a",
            ip_hash="ip-a",
        )
        target = self._event(
            claim_id=0,
            spot_id=2,
            claimed_at=now + 3,
            lat=51.5010,
            long=-0.1010,
            device="b" * 64,
            wallet_address="wallet-b",
            payout="payout-b",
            ip_hash="ip-b",
        )

        decision = claim_security._preclaim_risk([previous], target)
        self.assertFalse(decision["blocked"])

    def test_four_new_exact_centre_identities_across_world_trigger_review(self):
        now = 1_700_000_000
        coordinates = [
            (-33.8568, 151.2153),  # Sydney
            (41.0082, 28.9784),    # Istanbul
            (-22.9519, -43.2105),  # Rio de Janeiro
            (25.1972, 55.2744),    # Dubai
        ]
        events = [
            self._event(
                claim_id=index + 1,
                spot_id=index + 1,
                claimed_at=now + index,
                lat=lat,
                long=long,
                device=(f"{index + 1:x}" * 64)[:64],
                wallet_address=f"wallet-{index}",
                payout=f"payout-{index}",
                ip_hash=f"ip-{index}",
                centre_offset=0.0,
            )
            for index, (lat, long) in enumerate(coordinates)
        ]

        claim_ids = claim_security._coordinated_burst_claim_ids(events, now=now + 10)
        self.assertEqual(claim_ids, [1, 2, 3, 4])

    def test_worldwide_users_with_realistic_location_offsets_do_not_trip_exact_centre_burst(self):
        now = 1_700_000_000
        coordinates = [
            (-33.8568, 151.2153),
            (41.0082, 28.9784),
            (-22.9519, -43.2105),
            (25.1972, 55.2744),
        ]
        events = [
            self._event(
                claim_id=index + 1,
                spot_id=index + 1,
                claimed_at=now + index,
                lat=lat,
                long=long,
                device=(f"{index + 1:x}" * 64)[:64],
                wallet_address=f"wallet-{index}",
                payout=f"payout-{index}",
                ip_hash=f"ip-{index}",
                centre_offset=25.0,
            )
            for index, (lat, long) in enumerate(coordinates)
        ]

        claim_ids = claim_security._coordinated_burst_claim_ids(events, now=now + 10)
        self.assertEqual(claim_ids, [])

    async def test_release_helper_clears_manual_review_only_explicitly(self):
        async with schema.get_db() as db:
            _user_id, _spot_id, claim_id = await self._claim(db, suffix="release")
            await claim_security._metadata_set(
                db,
                claim_security._claim_record_key(claim_id),
                {
                    "claim_id": claim_id,
                    "manual_review": True,
                    "manual_review_reason": "test",
                },
            )
            released = await claim_security.release_claim_manual_review(db, claim_id=claim_id)
            record = await claim_security.get_claim_security_record(db, claim_id=claim_id)

        self.assertTrue(released)
        self.assertIsNotNone(record)
        self.assertFalse(record["manual_review"])
        self.assertNotIn("manual_review_reason", record)
        self.assertIn("manual_review_released_at", record)


if __name__ == "__main__":
    unittest.main()
