from contextlib import asynccontextmanager
import unittest
from unittest import mock

import constants as const
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
    async def test_deposit_records_provider_return_address_before_chain_overwrite(self):
        db = QueueDb([None, None])
        original = mock.AsyncMock(return_value={"ok": True, "trans_id": 9})

        with mock.patch.object(refund_address_safety, "_ORIGINAL_RECORD", original):
            result = await refund_address_safety.record_spot_deposit_transaction(
                db,
                user_id=1,
                spot_id=7,
                amount=100_000,
                from_address=TOP_UP_ADDRESS,
                tx_hash=TX_HASH,
                to_address=OTHER_ADDRESS,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(original.await_args.kwargs["from_address"], TOP_UP_ADDRESS)
        inserts = [
            params
            for sql, params in db.executed
            if "INSERT INTO nimiq_pay_return_address" in sql
        ]
        self.assertEqual(inserts, [(TX_HASH, 7, TOP_UP_ADDRESS)])

    async def test_second_return_address_for_same_spot_is_rejected(self):
        db = QueueDb([{"return_address": TOP_UP_ADDRESS}])
        original = mock.AsyncMock(return_value={"ok": True, "trans_id": 9})

        with mock.patch.object(refund_address_safety, "_ORIGINAL_RECORD", original):
            with self.assertRaisesRegex(ValueError, "original Nimiq Pay account"):
                await refund_address_safety.record_spot_deposit_transaction(
                    db,
                    user_id=1,
                    spot_id=7,
                    amount=100_000,
                    from_address=OTHER_ADDRESS,
                    tx_hash=TX_HASH,
                    to_address=TOP_UP_ADDRESS,
                )

        # The core record is called before the additive mapping is persisted. In
        # production this whole route is one SQLite transaction, so the raised
        # validation error rolls the core insert back as well.
        original.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
