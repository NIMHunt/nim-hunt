import tempfile
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


    async def test_cancelled_status_accounts_for_confirmed_claim_payouts(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 40},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 50},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 10},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater.db_access, "set_spot_status_to_cancelled", mock.AsyncMock()) as cancel:
            finalized = await trans_updater._finalize_cancelled_spot_if_ready(object(), spot_id=7)

        self.assertTrue(finalized)
        cancel.assert_awaited_once()

    async def test_failed_cancellation_transaction_does_not_finalize_and_allows_retry(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_FAILED, schema.TRANS_AMOUNT: 90},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_FAILED, schema.TRANS_AMOUNT: 10},
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


class PendingCancellationClaimGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_claim_rule_blocks_claims_while_cancellation_transaction_is_pending(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_CREATED_BY: 1}
        public = {schema.SPOT_ID: 7, "availability_rank": 0}
        distance = {"within_radius": True}
        with mock.patch.object(db_access, "can_user_claim", mock.AsyncMock(return_value=True)), \
             mock.patch.object(db_access, "get_public_spot", mock.AsyncMock(return_value=public)), \
             mock.patch.object(db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(db_access, "get_claim_distance_check", mock.AsyncMock(return_value=distance)), \
             mock.patch.object(db_access, "is_spot_claim_capacity_available", mock.AsyncMock(return_value=True)), \
             mock.patch.object(db_access, "has_user_reached_claim_limit", mock.AsyncMock(return_value=False)), \
             mock.patch.object(db_access, "has_spot_cancellation_started", mock.AsyncMock(return_value=True)):
            rule = await db_access.get_claim_rule_check(
                object(),
                spot_id=7,
                user_id=2,
                lat=1.0,
                long=2.0,
            )

        self.assertFalse(rule["allowed"])
        self.assertEqual(rule["reason"], "cancellation_pending")
        self.assertTrue(rule["cancellation_pending"])


class DirectClaimCancellationGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_claim_attempt_rejects_once_cancellation_has_begun(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_CREATED_BY: 1, schema.SPOT_USE_PASSWORD: 0}
        rule = {"allowed": False, "reason": "cancellation_pending", "message": "This spot is being cancelled and can no longer be claimed."}
        with mock.patch.object(db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(db_access, "get_claim_rule_check", mock.AsyncMock(return_value=rule)), \
             mock.patch.object(db_access, "create_claim", mock.AsyncMock()) as create_claim:
            with self.assertRaisesRegex(ValueError, "being cancelled"):
                await db_access.create_claim_attempt(
                    object(),
                    spot_id=7,
                    user_id=2,
                    lat=1.0,
                    long=2.0,
                )

        create_claim.assert_not_awaited()


class CancellationRetryGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_cancellation_can_retry_after_failed_cancellation_sends(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CREATED_BY: 1}
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 100,
                schema.TRANS_FROM_ADDRESS: "NQ12 payer",
                schema.TRANS_CREATED_AT: 1,
            },
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_FAILED, schema.TRANS_AMOUNT: 90},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_FAILED, schema.TRANS_AMOUNT: 10},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater, "submit_platform_fee_transaction", mock.AsyncMock(return_value={"ok": True, "trans_id": 31})) as fee, \
             mock.patch.object(trans_updater, "submit_spot_refund_transaction", mock.AsyncMock(return_value={"ok": True, "trans_id": 32})) as refund, \
             mock.patch.object(trans_updater.cache, "notify_spot_changed", mock.AsyncMock()) as spot_changed, \
             mock.patch.object(trans_updater.cache, "notify_user_changed", mock.AsyncMock()):
            result = await trans_updater.submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
                cancellation_fee=10,
                fee_address="NQ34 fee",
            )

        self.assertTrue(result["cancellation_pending"])
        self.assertEqual(result["remaining_amount"], 100)
        fee.assert_awaited_once()
        refund.assert_awaited_once()
        spot_changed.assert_awaited_once()


class PartialCancellationRetryGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_confirmed_fee_and_failed_refund_only_sends_refund(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CREATED_BY: 1}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100, schema.TRANS_FROM_ADDRESS: "NQ12 payer", schema.TRANS_CREATED_AT: 1},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 10},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_FAILED, schema.TRANS_AMOUNT: 90},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater, "submit_platform_fee_transaction", mock.AsyncMock()) as fee, \
             mock.patch.object(trans_updater, "submit_spot_refund_transaction", mock.AsyncMock(return_value={"ok": True, "trans_id": 32})) as refund, \
             mock.patch.object(trans_updater.cache, "notify_spot_changed", mock.AsyncMock()), \
             mock.patch.object(trans_updater.cache, "notify_user_changed", mock.AsyncMock()):
            result = await trans_updater.submit_spot_cancellation_transactions(object(), spot_id=7, cancellation_fee=10)

        self.assertEqual(result["fee_amount"], 0)
        self.assertEqual(result["refund_amount"], 90)
        fee.assert_not_awaited()
        refund.assert_awaited_once()

    async def test_retry_after_confirmed_refund_and_failed_fee_only_sends_fee(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CREATED_BY: 1}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100, schema.TRANS_FROM_ADDRESS: "NQ12 payer", schema.TRANS_CREATED_AT: 1},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 90},
            {schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE, schema.TRANS_STATUS: const.TRANS_STATUS_FAILED, schema.TRANS_AMOUNT: 10},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater, "submit_platform_fee_transaction", mock.AsyncMock(return_value={"ok": True, "trans_id": 31})) as fee, \
             mock.patch.object(trans_updater, "submit_spot_refund_transaction", mock.AsyncMock()) as refund, \
             mock.patch.object(trans_updater.cache, "notify_spot_changed", mock.AsyncMock()), \
             mock.patch.object(trans_updater.cache, "notify_user_changed", mock.AsyncMock()):
            result = await trans_updater.submit_spot_cancellation_transactions(object(), spot_id=7, cancellation_fee=10)

        self.assertEqual(result["fee_amount"], 10)
        self.assertEqual(result["refund_amount"], 0)
        fee.assert_awaited_once()
        refund.assert_not_awaited()

    async def test_zero_remaining_cancellation_marks_cancelled_without_transaction(self):
        spot = {schema.SPOT_ID: 7, schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CREATED_BY: 1}
        transactions = [
            {schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100, schema.TRANS_FROM_ADDRESS: "NQ12 payer", schema.TRANS_CREATED_AT: 1},
            {schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM, schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED, schema.TRANS_AMOUNT: 100},
        ]
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(trans_updater.db_access, "get_transactions_by_spot", mock.AsyncMock(return_value=transactions)), \
             mock.patch.object(trans_updater.db_access, "set_spot_status_to_cancelled", mock.AsyncMock()) as cancel, \
             mock.patch.object(trans_updater, "submit_platform_fee_transaction", mock.AsyncMock()) as fee, \
             mock.patch.object(trans_updater, "submit_spot_refund_transaction", mock.AsyncMock()) as refund, \
             mock.patch.object(trans_updater.cache, "notify_spot_changed", mock.AsyncMock()), \
             mock.patch.object(trans_updater.cache, "notify_user_changed", mock.AsyncMock()):
            result = await trans_updater.submit_spot_cancellation_transactions(object(), spot_id=7, cancellation_fee=10)

        self.assertTrue(result["cancelled"])
        self.assertFalse(result["cancellation_pending"])
        cancel.assert_awaited_once()
        fee.assert_not_awaited()
        refund.assert_not_awaited()


class ClaimPayoutDatabaseIdempotencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def _create_claim_fixture(self):
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash="device")
            spot_id = await db_access.create_spot(db, created_by=user_id, title="Spot")
            claim_id = await db_access.create_claim(
                db,
                spot_id=spot_id,
                user_id=user_id,
                lat=1.0,
                long=2.0,
                accuracy=1.0,
            )
            await db.commit()
        return user_id, claim_id

    async def test_concurrent_claim_payout_creation_is_database_idempotent(self):
        user_id, claim_id = await self._create_claim_fixture()
        async with schema.get_db() as db1, schema.get_db() as db2:
            first_id = await db_access.create_claim_transaction(
                db1,
                user_id=user_id,
                claim_id=claim_id,
                amount=10,
                from_address="from",
                to_address="to",
                tx_hash="hash-1",
            )
            await db1.commit()
            with self.assertRaisesRegex(RuntimeError, "already has a non-failed payout"):
                await db_access.create_claim_transaction(
                    db2,
                    user_id=user_id,
                    claim_id=claim_id,
                    amount=10,
                    from_address="from",
                    to_address="to",
                    tx_hash="hash-2",
                )
            await db2.rollback()

        self.assertGreater(first_id, 0)

    async def test_failed_claim_payout_can_be_retried(self):
        user_id, claim_id = await self._create_claim_fixture()
        async with schema.get_db() as db:
            failed_id = await db_access.create_claim_transaction(
                db,
                user_id=user_id,
                claim_id=claim_id,
                amount=10,
                from_address="from",
                to_address="to",
                tx_hash="hash-1",
            )
            await db_access.set_transaction_status_to_failed(db, trans_id=failed_id)
            retry_id = await db_access.create_claim_transaction(
                db,
                user_id=user_id,
                claim_id=claim_id,
                amount=10,
                from_address="from",
                to_address="to",
                tx_hash="hash-2",
            )
            await db.commit()

        self.assertNotEqual(failed_id, retry_id)
