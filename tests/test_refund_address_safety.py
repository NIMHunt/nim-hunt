import unittest
from contextlib import asynccontextmanager
from unittest import mock

import constants as const
import database as schema
import refund_address_safety
import trans_updater

TOP_UP_ADDRESS = "NQ94 13CJ G33S CPB5 T4P5 AK5F 38H5 H6MQ KFKN"
HTLC_ADDRESS = "NQ11 9C0Q FLC0 JPK2 TD7F 5XS0 NN1B PFGR A9BE"
OTHER_ADDRESS = "NQ54 FTGY F6VJ EJPU NSMN RA5Q 0K21 8EQT Q05P"
TX_HASH = "a" * 64


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class QueueDb:
    def __init__(self, select_rows=None):
        self.select_rows = list(select_rows or [])
        self.executed = []
        self.commits = 0

    async def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))
        if "SELECT" in sql.upper():
            row = self.select_rows.pop(0) if self.select_rows else None
            return FakeCursor(row)
        return FakeCursor()

    async def commit(self):
        self.commits += 1


class RefundAddressResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_refund_uses_nimiq_pay_account_not_htlc_beneficiary(self):
        db = QueueDb(
            [
                {
                    "return_address": TOP_UP_ADDRESS,
                    "spot_id": 7,
                    "chain_sender": HTLC_ADDRESS,
                }
            ]
        )

        @asynccontextmanager
        async def fake_get_db():
            yield db

        original = mock.AsyncMock(side_effect=AssertionError("old HTLC resolver used"))
        with mock.patch.object(refund_address_safety, "get_db", fake_get_db), \
             mock.patch.object(refund_address_safety, "_ORIGINAL_RESOLVE", original), \
             mock.patch.object(const, "PUBLIC_DEPLOYMENT", True), \
             mock.patch.object(
                 trans_updater,
                 "get_chain_account_by_address",
                 mock.AsyncMock(return_value={"type": 0}),
             ):
            result = await refund_address_safety.resolve_nimiq_pay_payout_address(
                HTLC_ADDRESS,
                source_tx_hash=TX_HASH,
                force_chain_resolution=True,
            )

        self.assertEqual(result, TOP_UP_ADDRESS)
        original.assert_not_awaited()

    async def test_claim_payout_without_source_transaction_is_unchanged(self):
        original = mock.AsyncMock(return_value=TOP_UP_ADDRESS)
        with mock.patch.object(refund_address_safety, "_ORIGINAL_RESOLVE", original):
            result = await refund_address_safety.resolve_nimiq_pay_payout_address(
                TOP_UP_ADDRESS,
                source_tx_hash=None,
            )

        self.assertEqual(result, TOP_UP_ADDRESS)
        original.assert_awaited_once()

    async def test_public_refund_without_recorded_return_address_fails_closed(self):
        db = QueueDb([None])

        @asynccontextmanager
        async def fake_get_db():
            yield db

        original = mock.AsyncMock(return_value=OTHER_ADDRESS)
        with mock.patch.object(refund_address_safety, "get_db", fake_get_db), \
             mock.patch.object(refund_address_safety, "_ORIGINAL_RESOLVE", original), \
             mock.patch.object(const, "PUBLIC_DEPLOYMENT", True):
            with self.assertRaisesRegex(RuntimeError, "manual reconciliation"):
                await refund_address_safety.resolve_nimiq_pay_payout_address(
                    HTLC_ADDRESS,
                    source_tx_hash=TX_HASH,
                    force_chain_resolution=True,
                )

        original.assert_not_awaited()

    async def test_non_basic_recorded_return_address_fails_closed(self):
        db = QueueDb(
            [
                {
                    "return_address": TOP_UP_ADDRESS,
                    "spot_id": 7,
                    "chain_sender": HTLC_ADDRESS,
                }
            ]
        )

        @asynccontextmanager
        async def fake_get_db():
            yield db

        with mock.patch.object(refund_address_safety, "get_db", fake_get_db), \
             mock.patch.object(refund_address_safety, "_ORIGINAL_RESOLVE", mock.AsyncMock()), \
             mock.patch.object(const, "PUBLIC_DEPLOYMENT", True), \
             mock.patch.object(
                 trans_updater,
                 "get_chain_account_by_address",
                 mock.AsyncMock(return_value={"type": 2}),
             ):
            with self.assertRaisesRegex(RuntimeError, "not a basic account"):
                await refund_address_safety.resolve_nimiq_pay_payout_address(
                    HTLC_ADDRESS,
                    source_tx_hash=TX_HASH,
                    force_chain_resolution=True,
                )


class ReturnAddressPersistenceTest(unittest.IsolatedAsyncioTestCase):
    def _draft_spot(self):
        return {
            schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            schema.SPOT_DEPOSIT_ADDRESS: OTHER_ADDRESS,
        }

    async def test_deposit_records_provider_return_address_before_chain_overwrite(self):
        create_transaction = mock.AsyncMock(return_value=9)
        remember = mock.AsyncMock()
        with mock.patch.object(
            refund_address_safety,
            "_ensure_return_address_table",
            mock.AsyncMock(),
        ), mock.patch.object(
            refund_address_safety.trans_updater,
            "_transaction_by_hash",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety,
            "_spot_return_address",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_confirmed_spot_funding_address",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_spot",
            mock.AsyncMock(return_value=self._draft_spot()),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_spot_deposit_totals",
            mock.AsyncMock(return_value={"pending_amount": 0, "confirmed_amount": 0}),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "spot_required_deposit_amount",
            return_value=200_000,
        ), mock.patch.object(
            refund_address_safety.db_access,
            "create_spot_deposit_transaction",
            create_transaction,
        ), mock.patch.object(
            refund_address_safety,
            "_remember_return_address",
            remember,
        ):
            result = await refund_address_safety.record_spot_deposit_transaction(
                object(),
                user_id=1,
                spot_id=7,
                amount=100_000,
                from_address=TOP_UP_ADDRESS,
                tx_hash=TX_HASH,
                to_address=OTHER_ADDRESS,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["already_recorded"])
        self.assertEqual(create_transaction.await_args.kwargs["from_address"], TOP_UP_ADDRESS)
        remember.assert_awaited_once()
        self.assertEqual(remember.await_args.kwargs["return_address"], TOP_UP_ADDRESS)

    async def test_same_nimiq_pay_account_can_top_up_after_htlc_confirmation(self):
        create_transaction = mock.AsyncMock(return_value=10)
        confirmed_chain_sender = mock.AsyncMock(
            side_effect=AssertionError("HTLC sender must not be used as top-up identity")
        )
        with mock.patch.object(
            refund_address_safety,
            "_ensure_return_address_table",
            mock.AsyncMock(),
        ), mock.patch.object(
            refund_address_safety.trans_updater,
            "_transaction_by_hash",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety,
            "_spot_return_address",
            mock.AsyncMock(return_value=TOP_UP_ADDRESS),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_confirmed_spot_funding_address",
            confirmed_chain_sender,
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_spot",
            mock.AsyncMock(return_value=self._draft_spot()),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_spot_deposit_totals",
            mock.AsyncMock(return_value={"pending_amount": 0, "confirmed_amount": 50_000}),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "spot_required_deposit_amount",
            return_value=200_000,
        ), mock.patch.object(
            refund_address_safety.db_access,
            "create_spot_deposit_transaction",
            create_transaction,
        ), mock.patch.object(
            refund_address_safety,
            "_remember_return_address",
            mock.AsyncMock(),
        ):
            result = await refund_address_safety.record_spot_deposit_transaction(
                object(),
                user_id=1,
                spot_id=7,
                amount=150_000,
                from_address=TOP_UP_ADDRESS,
                tx_hash="b" * 64,
                to_address=OTHER_ADDRESS,
            )

        self.assertTrue(result["ok"])
        confirmed_chain_sender.assert_not_awaited()
        self.assertEqual(create_transaction.await_args.kwargs["from_address"], TOP_UP_ADDRESS)

    async def test_second_return_address_for_same_spot_is_rejected(self):
        create_transaction = mock.AsyncMock()
        with mock.patch.object(
            refund_address_safety,
            "_ensure_return_address_table",
            mock.AsyncMock(),
        ), mock.patch.object(
            refund_address_safety.trans_updater,
            "_transaction_by_hash",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety,
            "_spot_return_address",
            mock.AsyncMock(return_value=TOP_UP_ADDRESS),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_spot",
            mock.AsyncMock(return_value=self._draft_spot()),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "create_spot_deposit_transaction",
            create_transaction,
        ):
            with self.assertRaisesRegex(ValueError, "original Nimiq Pay account"):
                await refund_address_safety.record_spot_deposit_transaction(
                    object(),
                    user_id=1,
                    spot_id=7,
                    amount=100_000,
                    from_address=OTHER_ADDRESS,
                    tx_hash="c" * 64,
                    to_address=OTHER_ADDRESS,
                )
        create_transaction.assert_not_awaited()

    async def test_legacy_confirmed_deposit_without_mapping_fails_closed(self):
        with mock.patch.object(
            refund_address_safety,
            "_ensure_return_address_table",
            mock.AsyncMock(),
        ), mock.patch.object(
            refund_address_safety.trans_updater,
            "_transaction_by_hash",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety,
            "_spot_return_address",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_confirmed_spot_funding_address",
            mock.AsyncMock(return_value=HTLC_ADDRESS),
        ), mock.patch.object(
            refund_address_safety.db_access,
            "get_spot",
            mock.AsyncMock(return_value=self._draft_spot()),
        ):
            with self.assertRaisesRegex(ValueError, "manual reconciliation"):
                await refund_address_safety.record_spot_deposit_transaction(
                    object(),
                    user_id=1,
                    spot_id=7,
                    amount=100_000,
                    from_address=TOP_UP_ADDRESS,
                    tx_hash="d" * 64,
                    to_address=OTHER_ADDRESS,
                )


if __name__ == "__main__":
    unittest.main()
