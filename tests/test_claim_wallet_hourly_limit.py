from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from unittest import mock

import cache
import claim_security
import claim_wallet_hourly_limit as wallet_limit
import constants as const
import database as schema
import db_access


class ClaimWalletHourlyLimitTest(unittest.IsolatedAsyncioTestCase):
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

    async def _spot(self, db, *, owner_id: int) -> int:
        spot_id = await db_access.create_spot(
            db,
            created_by=owner_id,
            title="Durable wallet rate test",
            lat=51.5,
            long=-0.1,
            radius=100,
            claim_duration=0,
            max_claims_per_user=0,
            max_total_claims=100,
            total_value=100 * const.MIN_STANDARD_CLAIM_PAYOUT,
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

    async def test_wallet_limit_survives_eviction_from_recent_event_cache(self):
        wallet_address = const.DEV_PLATFORM_FEE_ADDRESS
        async with schema.get_db() as db:
            user_id = await self._user(db, "rate-user")
            owner_id = await self._user(db, "rate-owner")
            spot_id = await self._spot(db, owner_id=owner_id)
            await claim_security._metadata_set(
                db,
                claim_security._user_binding_key(user_id),
                {
                    "user_id": user_id,
                    "wallet_address": wallet_address,
                },
            )

            for _index in range(claim_security.WALLET_HOURLY_CLAIM_LIMIT):
                await db_access.create_claim(
                    db,
                    spot_id=spot_id,
                    user_id=user_id,
                    lat=51.5,
                    long=-0.1,
                    accuracy=1.0,
                    payout_address=wallet_address,
                )

            # Reproduce Codex's failure mode: the bounded behavioural cache no
            # longer contains this wallet's claims. The hard quota must still be
            # derived from durable CLAIM rows instead of that lossy cache.
            await claim_security._metadata_set(db, claim_security.RECENT_EVENTS_KEY, [])
            now = await db_access.get_unixepoch(db)
            recent_events = await claim_security._load_recent_events(db, now=now)
            decision = await wallet_limit._durable_wallet_rate_decision(
                db,
                wallet_address=wallet_address,
                now=now,
            )

        self.assertEqual(recent_events, [])
        self.assertTrue(decision["blocked"])
        self.assertEqual(decision["reason"], "wallet_rate_limit")
        self.assertGreater(decision["retry_at"], now)

    async def test_transactional_recheck_blocks_last_slot_race(self):
        delegate = mock.AsyncMock(return_value={"id": 99})
        binding = {"wallet_address": const.DEV_PLATFORM_FEE_ADDRESS}
        blocked = {
            "blocked": True,
            "reason": "wallet_rate_limit",
            "retry_at": 123,
        }

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                wallet_limit.claim_security,
                "_metadata_get",
                new=mock.AsyncMock(return_value=binding),
            ),
            mock.patch.object(
                wallet_limit,
                "_durable_wallet_rate_decision",
                new=mock.AsyncMock(return_value=blocked),
            ),
            mock.patch.object(
                wallet_limit.db_access,
                "get_unixepoch",
                new=mock.AsyncMock(return_value=100),
            ),
            mock.patch.object(wallet_limit, "_CLAIM_ATTEMPT_DELEGATE", delegate),
        ):
            with self.assertRaisesRegex(ValueError, "hourly claim limit"):
                await wallet_limit._create_claim_attempt_with_durable_wallet_limit(
                    object(),
                    spot_id=3,
                    user_id=4,
                    lat=51.5,
                    long=-0.1,
                    payout_address=None,
                )

        delegate.assert_not_awaited()

    async def test_early_preclaim_check_preserves_wallet_rate_limit_response(self):
        delegate = mock.AsyncMock(return_value={"blocked": False, "reason": "allow"})
        blocked = {
            "blocked": True,
            "reason": "wallet_rate_limit",
            "signal": "verified wallet",
            "retry_at": 456,
        }
        session = {
            "wallet_address": const.DEV_PLATFORM_FEE_ADDRESS,
            "device_id_hash": "a" * 64,
        }

        class FakeDbContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with (
            mock.patch.object(wallet_limit, "_PRECLAIM_DELEGATE", delegate),
            mock.patch.object(
                wallet_limit.claim_security,
                "get_db",
                return_value=FakeDbContext(),
            ),
            mock.patch.object(
                wallet_limit.db_access,
                "get_unixepoch",
                new=mock.AsyncMock(return_value=100),
            ),
            mock.patch.object(
                wallet_limit,
                "_durable_wallet_rate_decision",
                new=mock.AsyncMock(return_value=blocked),
            ),
        ):
            result = await wallet_limit._preclaim_decision_with_durable_wallet_limit(
                spot_id=1,
                session=session,
                request_body={},
                ip_fingerprint="ip",
            )

        self.assertEqual(result, blocked)


if __name__ == "__main__":
    unittest.main()
