from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import settlement_updater
import trans_updater


class CancellationFeeLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash=f"cancellation-fee-owner-{id(self)}",
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def _create_funded_published_spot(self) -> int:
        # Keep this test focused on the cancellation fee itself. Creation-fee
        # recovery has separate coverage in test_completed_spot_creation_fee_recovery.py.
        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", 0):
            async with schema.get_db() as db:
                spot_id = await db_access.create_spot(
                    db,
                    created_by=self.owner_id,
                    title="Cancellation Fee Lifecycle",
                    desc="Published Spot used to verify cancellation fee retries.",
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=0,
                    max_claims_per_user=1,
                    max_total_claims=2,
                    total_value=const.MIN_SPOT_TOTAL_VALUE,
                    starts_at=int(time.time()) + 3600,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
                spot = await db_access.get_spot(db, spot_id=spot_id)
                required = db_access.spot_required_deposit_amount(spot)
                deposit_id = await db_access.create_spot_deposit_transaction(
                    db,
                    user_id=self.owner_id,
                    spot_id=spot_id,
                    amount=required,
                    from_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                    to_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                    tx_hash=f"cancellation-fee-deposit-{time.time_ns()}",
                )
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=deposit_id,
                    block_number=123,
                )
                await db_access.publish_spot(db, spot_id=spot_id)
                await db.commit()
                return spot_id

    async def test_cancellation_fee_stays_submittable_and_spot_stays_retryable(self):
        spot_id = await self._create_funded_published_spot()
        cancellation_fee = 5 * const.LUNA_PER_NIM
        expected_refund = const.MIN_SPOT_TOTAL_VALUE - cancellation_fee
        refund_address = "NQ00 NIMHUNT DEV FUNDING WALLET"

        async with schema.get_db() as db:
            with (
                mock.patch.object(
                    trans_updater,
                    "resolve_nimiq_pay_payout_address",
                    mock.AsyncMock(return_value=refund_address),
                ),
                mock.patch.object(
                    trans_updater,
                    "submit_platform_fee_transaction",
                    mock.AsyncMock(return_value={"ok": True, "trans_id": 91}),
                ) as fee_submit,
                mock.patch.object(
                    trans_updater,
                    "submit_spot_refund_transaction",
                    mock.AsyncMock(return_value={"ok": True, "trans_id": 92}),
                ) as refund_submit,
            ):
                result = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=cancellation_fee,
                    fee_address=const.DEV_PLATFORM_FEE_ADDRESS,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["cancellation_pending"])
            self.assertEqual(result["fee_amount"], cancellation_fee)
            self.assertEqual(result["refund_amount"], expected_refund)
            fee_submit.assert_awaited_once_with(
                db,
                spot_id=spot_id,
                amount=cancellation_fee,
                fee_address=const.DEV_PLATFORM_FEE_ADDRESS,
            )
            refund_submit.assert_awaited_once_with(
                db,
                spot_id=spot_id,
                to_address=refund_address,
                amount=expected_refund,
            )

            spot = await db_access.get_spot(db, spot_id=spot_id)
            self.assertEqual(int(spot[schema.SPOT_STATUS]), const.SPOT_STATUS_PUBLISHED)
            self.assertIsNotNone(spot[schema.SPOT_CANCELLATION_STARTED_AT])

            retry_ids = await db_access.get_pending_cancellation_spot_ids(db)
            self.assertIn(spot_id, retry_ids)

    async def test_remainder_settlement_cannot_complete_a_cancelling_spot(self):
        spot_id = await self._create_funded_published_spot()

        async with schema.get_db() as db:
            await db_access.mark_spot_cancellation_started(db, spot_id=spot_id)
            await db.commit()

        result = await settlement_updater.settle_spot_remainder_if_ready(
            spot_id=spot_id,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["refunded"])
        self.assertEqual(result["reason"], "cancellation_managed_separately")

        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=spot_id)
            self.assertEqual(int(spot[schema.SPOT_STATUS]), const.SPOT_STATUS_PUBLISHED)
            self.assertIsNotNone(spot[schema.SPOT_CANCELLATION_STARTED_AT])
            retry_ids = await db_access.get_pending_cancellation_spot_ids(db)
            self.assertIn(spot_id, retry_ids)


if __name__ == "__main__":
    unittest.main()
