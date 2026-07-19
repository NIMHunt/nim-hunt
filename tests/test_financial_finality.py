import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from unittest import mock

import constants as const
import database as schema
import db_access
import settlement_updater
import trans_updater


class MissingHashFinalityTest(unittest.IsolatedAsyncioTestCase):
    async def test_old_unseen_hash_is_quarantined_not_failed(self):
        trans = {
            schema.TRANS_ID: 1,
            schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM,
            schema.TRANS_TX_HASH: "abc",
            schema.TRANS_CREATED_AT: int(time.time()) - 10_000,
        }
        pending = trans_updater.ChainTransactionStatus(
            status="pending",
            tx_hash="abc",
            reason="hash not found yet",
        )
        with mock.patch.object(
            trans_updater,
            "get_chain_transaction_status",
            mock.AsyncMock(return_value=pending),
        ):
            result = await trans_updater.check_pending_transaction(
                trans,
                fail_after_seconds=1,
            )

        self.assertEqual(result.status, "unknown")
        self.assertIn("remains pending", result.reason)


class ConfirmedVerificationQuarantineTest(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_outgoing_mismatch_does_not_release_payout_guard(self):
        trans = {
            schema.TRANS_ID: 1,
            schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM,
            schema.TRANS_TX_HASH: "abc",
            schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
        }

        @asynccontextmanager
        async def fake_get_db():
            yield object()

        chain_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash="abc",
            raw={"hash": "abc"},
        )
        with mock.patch.object(trans_updater, "get_db", fake_get_db), \
             mock.patch.object(trans_updater.cache, "get_pending_transactions", mock.AsyncMock(return_value=[trans])), \
             mock.patch.object(trans_updater, "check_pending_transaction", mock.AsyncMock(return_value=chain_status)), \
             mock.patch.object(
                 trans_updater,
                 "verify_chain_details_for_record",
                 mock.AsyncMock(return_value=trans_updater.VerifiedChainDetails(ok=False, reason="recipient mismatch")),
             ), \
             mock.patch.object(
                 trans_updater,
                 "submit_ready_spot_creation_fees",
                 mock.AsyncMock(return_value={"ok": True, "submitted": [], "skipped": [], "errors": []}),
             ), \
             mock.patch.object(trans_updater, "mark_trans_as_failed", mock.AsyncMock()) as mark_failed:
            result = await trans_updater.check_pending_transactions()

        self.assertEqual(result["finalised_count"], 0)
        self.assertEqual(result["unknown_count"], 1)
        mark_failed.assert_not_awaited()


class SubmittedSendValidationTest(unittest.TestCase):
    def test_helper_cannot_replace_intended_amount(self):
        with mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True):
            result = trans_updater.SubmittedChainTransaction(
                tx_hash="real-hash",
                from_address="NQ00 NIMHUNT DEV SPOT DEPOSIT TEST",
                to_address="NQ00 NIMHUNT DEV CLAIM PAYOUT USER 1",
                amount=101,
            )
            with self.assertRaisesRegex(RuntimeError, "amount"):
                trans_updater._validate_submitted_chain_send(
                    result,
                    expected_from_address="NQ00 NIMHUNT DEV SPOT DEPOSIT TEST",
                    expected_to_address="NQ00 NIMHUNT DEV CLAIM PAYOUT USER 1",
                    expected_amount=100,
                )


class FinancialDatabaseFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def create_claim_fixture(self):
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="owner")
            claimant_id = await db_access.create_user(db, device_id_hash="claimant")
            spot_id = await db_access.create_spot(db, created_by=owner_id, title="Reward Spot")
            await db.execute(
                f"""
                UPDATE {schema.SPOT_TABLE_NAME}
                SET {schema.SPOT_STATUS} = ?,
                    {schema.SPOT_TOTAL_VALUE} = ?,
                    {schema.SPOT_MAX_TOTAL_CLAIMS} = ?
                WHERE {schema.SPOT_ID} = ?;
                """,
                (const.SPOT_STATUS_PUBLISHED, 10_000_001, 2, spot_id),
            )
            claim_id = await db_access.create_claim(
                db,
                spot_id=spot_id,
                user_id=claimant_id,
                lat=1.0,
                long=2.0,
                accuracy=1.0,
            )
            await db_access.set_claim_status_to_success(db, claim_id=claim_id)
            await db.commit()
        return owner_id, claimant_id, spot_id, claim_id


class TransactionFinalStateTest(FinancialDatabaseFixture):
    async def test_confirmed_transaction_cannot_be_overwritten_as_failed(self):
        _owner_id, claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        async with schema.get_db() as db:
            trans_id = await db_access.create_claim_transaction(
                db,
                user_id=claimant_id,
                claim_id=claim_id,
                amount=50,
                from_address="from",
                to_address="to",
                tx_hash="hash-final",
            )
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=trans_id,
                block_number=7,
            )
            await db.commit()
            with self.assertRaisesRegex(RuntimeError, "pending"):
                await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
            await db.rollback()

        async with schema.get_db() as db:
            trans = await db_access.get_transaction(db, trans_id=trans_id)
        self.assertEqual(int(trans[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)

    async def test_repeated_confirmation_is_idempotent(self):
        _owner_id, claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        async with schema.get_db() as db:
            trans_id = await db_access.create_claim_transaction(
                db,
                user_id=claimant_id,
                claim_id=claim_id,
                amount=50,
                from_address="from",
                to_address="to",
                tx_hash="hash-repeat",
            )
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=trans_id,
                block_number=7,
            )
            await db.commit()

            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=trans_id,
                block_number=8,
            )
            await db.commit()
            trans = await db_access.get_transaction(db, trans_id=trans_id)

        self.assertEqual(int(trans[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)
        self.assertEqual(int(trans[schema.TRANS_BLOCK_NUMBER]), 7)


class StandardPayoutRecoveryTest(FinancialDatabaseFixture):
    async def test_successful_standard_claim_is_queued_until_payout_intent_exists(self):
        _owner_id, claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        async with schema.get_db() as db:
            self.assertEqual(
                await db_access.get_unpaid_successful_standard_claim_ids(db),
                [claim_id],
            )
            trans_id = await db_access.create_claim_transaction(
                db,
                user_id=claimant_id,
                claim_id=claim_id,
                amount=50,
                from_address="from",
                to_address="to",
                tx_hash="hash-pending",
            )
            await db.commit()
            self.assertEqual(
                await db_access.get_unpaid_successful_standard_claim_ids(db),
                [],
            )
            await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
            await db.commit()
            self.assertEqual(
                await db_access.get_unpaid_successful_standard_claim_ids(db),
                [claim_id],
            )

    async def test_recovery_submits_integer_divided_standard_reward(self):
        _owner_id, _claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        submitted = {
            "ok": True,
            "claim_id": claim_id,
            "trans_id": 10,
            "amount": 50,
            "already_exists": False,
        }
        with mock.patch.object(
            settlement_updater.trans_updater,
            "submit_claim_reward_transaction",
            mock.AsyncMock(return_value=submitted),
        ) as submit:
            result = await settlement_updater.payout_standard_claim_if_ready(
                claim_id=claim_id,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["paid"])
        submit.assert_awaited_once()
        self.assertEqual(submit.await_args.kwargs["amount"], 5_000_000)

    async def test_chain_send_failure_is_not_mislabeled_as_concurrent(self):
        _owner_id, _claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        with mock.patch.object(
            settlement_updater.trans_updater,
            "submit_claim_reward_transaction",
            mock.AsyncMock(
                side_effect=RuntimeError(
                    "Chain send did not return a usable transaction hash; local intent 7 was left pending for safety"
                )
            ),
        ):
            result = await settlement_updater.payout_standard_claim_if_ready(
                claim_id=claim_id,
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["paid"])
        self.assertIn("local intent 7", result["reason"])


class CancellationLiabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_waits_for_successful_claim_payout_intent(self):
        spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_CREATED_BY: 1,
        }

        class FakeDb:
            async def execute(self, *args, **kwargs):
                return None

            async def commit(self):
                return None

            async def rollback(self):
                return None

        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)), \
             mock.patch.object(
                 trans_updater.db_access,
                 "get_unpaid_successful_standard_claim_ids",
                 mock.AsyncMock(return_value=[11]),
             ), \
             mock.patch.object(
                 trans_updater.db_access,
                 "mark_spot_cancellation_started",
                 mock.AsyncMock(),
             ) as mark_started:
            result = await trans_updater.submit_spot_cancellation_transactions(
                FakeDb(), spot_id=7
            )

        self.assertTrue(result["cancellation_pending"])
        self.assertEqual(result["reason"], "claim_payouts_pending")
        mark_started.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
