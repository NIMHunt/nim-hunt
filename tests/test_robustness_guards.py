import unittest
from contextlib import asynccontextmanager
from unittest import mock

import constants as const
import database as schema
import db_access
import settlement_updater
import trans_updater


class PrizedrawWinnerPersistenceGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_settlement_does_not_complete_when_selected_winners_are_not_persisted(self):
        @asynccontextmanager
        async def fake_get_db():
            yield object()

        @asynccontextmanager
        async def fake_transaction(db):
            yield

        spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_TOTAL_VALUE: 101,
            schema.SPOT_CREATED_BY: 1,
        }
        claims = [{schema.CLAIM_ID: 3}, {schema.CLAIM_ID: 9}]

        with mock.patch.object(settlement_updater, "get_db", fake_get_db), \
             mock.patch.object(settlement_updater.db_access, "transaction", fake_transaction), \
             mock.patch.object(settlement_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(settlement_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=True)), \
             mock.patch.object(settlement_updater, "_get_unixepoch", mock.AsyncMock(return_value=123)), \
             mock.patch.object(settlement_updater, "_settlement_ready_reason", mock.AsyncMock(return_value="ended")), \
             mock.patch.object(settlement_updater.db_access, "fail_pending_claims_for_spot", mock.AsyncMock(return_value=0)), \
             mock.patch.object(settlement_updater.db_access, "get_successful_claims_for_spot", mock.AsyncMock(return_value=claims)), \
             mock.patch.object(settlement_updater.db_access, "get_prizedraw", mock.AsyncMock(return_value={schema.PRIZEDRAW_PRIZE_COUNT: 2})), \
             mock.patch.object(settlement_updater.secrets.SystemRandom, "sample", return_value=claims), \
             mock.patch.object(settlement_updater.db_access, "mark_prizedraw_winners_pending", mock.AsyncMock(return_value={"updated_count": 1})), \
             mock.patch.object(settlement_updater.db_access, "set_spot_status_to_completed", mock.AsyncMock()) as complete:
            result = await settlement_updater.settle_prizedraw_spot_if_ready(spot_id=7)

        self.assertFalse(result["ok"])
        self.assertIn("not all selected Prizedraw winners were persisted", result["reason"])
        complete.assert_not_awaited()


class DuplicateClaimPayoutGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_claim_transaction_rejects_existing_nonfailed_payout(self):
        with mock.patch.object(db_access, "get_claim", mock.AsyncMock(return_value={schema.CLAIM_SPOT_ID: 7})), \
             mock.patch.object(db_access, "has_nonfailed_claim_payout_transaction", mock.AsyncMock(return_value=True)), \
             mock.patch.object(db_access, "_create_transaction", mock.AsyncMock()) as create:
            with self.assertRaisesRegex(RuntimeError, "already has a non-failed payout"):
                await db_access.create_claim_transaction(
                    object(),
                    user_id=1,
                    claim_id=3,
                    amount=50,
                    from_address="from",
                    to_address="to",
                    tx_hash="hash",
                )

        create.assert_not_awaited()


class CancellationFinalizationGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_status_waits_until_refund_and_fee_are_final(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 90},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_PENDING, schema.TRANS_AMOUNT: 10},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater.db_access, "set_spot_status_to_cancelled", mock.AsyncMock()) as cancel:
            finalized = await trans_updater._finalize_cancelled_spot_if_ready(object(), spot_id=7)

        self.assertFalse(finalized)
        cancel.assert_not_awaited()

    async def test_cancelled_status_finalizes_after_all_cancellation_sends_confirm(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 90},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 10},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater.db_access, "set_spot_status_to_cancelled", mock.AsyncMock()) as cancel:
            finalized = await trans_updater._finalize_cancelled_spot_if_ready(object(), spot_id=7)

        self.assertTrue(finalized)
        cancel.assert_awaited_once()
