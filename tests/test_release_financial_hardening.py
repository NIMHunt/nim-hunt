from __future__ import annotations

import tempfile
from unittest import IsolatedAsyncioTestCase, mock

import aiosqlite

import cancellation_safety
import constants as const
import funding_status


class TestReleaseThresholds:
    def test_release_amount_defaults(self):
        assert const.MIN_SPOT_TOTAL_VALUE_NIM == 500
        assert const.STANDARD_SPOT_CREATION_FEE == 200 * const.LUNA_PER_NIM
        assert const.PRIZEDRAW_SPOT_CREATION_FEE == 200 * const.LUNA_PER_NIM
        assert const.SPOT_CANCELLATION_FEE == 500 * const.LUNA_PER_NIM


class CreationFeePublicationGateTests(IsolatedAsyncioTestCase):
    async def test_fully_funded_spot_waits_for_confirmed_creation_fee(self):
        with mock.patch.object(
            funding_status,
            "_ORIGINAL_CAN_PUBLISH_SPOT",
            mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            funding_status.db_access,
            "has_confirmed_spot_creation_fee_transaction",
            mock.AsyncMock(return_value=False),
        ):
            self.assertFalse(
                await funding_status.can_publish_spot_after_fee_broadcast(
                    object(),
                    spot_id=7,
                )
            )

        with mock.patch.object(
            funding_status,
            "_ORIGINAL_CAN_PUBLISH_SPOT",
            mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            funding_status.db_access,
            "has_confirmed_spot_creation_fee_transaction",
            mock.AsyncMock(return_value=True),
        ):
            self.assertTrue(
                await funding_status.can_publish_spot_after_fee_broadcast(
                    object(),
                    spot_id=7,
                )
            )

    async def test_owner_publish_readiness_uses_fee_confirmation(self):
        summary = funding_status.deposit_summary(
            [],
            total_value=500 * const.LUNA_PER_NIM,
            creation_fee=200 * const.LUNA_PER_NIM,
        )
        self.assertFalse(summary["fee_paid"])
        self.assertFalse(summary["fee_confirmed"])


class CancellationPolicySnapshotTests(IsolatedAsyncioTestCase):
    async def test_policy_does_not_change_after_first_cancellation_attempt(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            async with aiosqlite.connect(db_file.name) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON;")
                await db.execute("CREATE TABLE spot (id INTEGER PRIMARY KEY);")
                await db.execute("INSERT INTO spot (id) VALUES (7);")
                await db.commit()
                await cancellation_safety._ensure_guard_table(db)

                first, created = await cancellation_safety._snapshot_cancellation_policy(
                    db,
                    spot_id=7,
                    cancellation_fee=500 * const.LUNA_PER_NIM,
                    fee_address=const.DEV_PLATFORM_FEE_ADDRESS,
                )
                second, created_again = await cancellation_safety._snapshot_cancellation_policy(
                    db,
                    spot_id=7,
                    cancellation_fee=1,
                    fee_address="NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF",
                )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["fee_amount"], 500 * const.LUNA_PER_NIM)
        self.assertEqual(second["fee_amount"], first["fee_amount"])
        self.assertEqual(second["fee_address"], first["fee_address"])
