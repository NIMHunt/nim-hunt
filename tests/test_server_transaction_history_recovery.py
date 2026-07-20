from __future__ import annotations

import time
from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import database as schema
import trans_updater

HASH = "aa" * 32
ADDRESS = const.DEV_PLATFORM_FEE_ADDRESS


class ServerTransactionHistoryRecoveryTest(IsolatedAsyncioTestCase):
    def transaction(self, *, amount: int = 123_000) -> dict:
        return {
            schema.TRANS_ID: 41,
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
            schema.TRANS_TX_HASH: HASH,
            schema.TRANS_FROM_ADDRESS: ADDRESS,
            schema.TRANS_TO_ADDRESS: ADDRESS,
            schema.TRANS_AMOUNT: amount,
            schema.TRANS_CREATED_AT: int(time.time()),
        }

    def chain_transaction(self, *, amount: int = 123_000) -> dict:
        return {
            "hash": HASH,
            "from": ADDRESS,
            "to": ADDRESS,
            "value": amount,
            "blockNumber": 456,
            "executionResult": True,
        }

    async def test_pending_direct_lookup_recovers_exact_outgoing_hash_from_history(self):
        pending = trans_updater.ChainTransactionStatus(
            status="pending",
            tx_hash=HASH,
            reason="hash not found yet",
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value={"data": [self.chain_transaction()], "metadata": None}),
            ) as history,
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.tx_hash, HASH)
        self.assertEqual(result.block_number, 456)
        self.assertIn("expected-address", result.reason)
        history.assert_awaited_once()
        verified = trans_updater._verify_chain_details_for_record(self.transaction(), result)
        self.assertTrue(verified.ok)

    async def test_unknown_direct_lookup_can_recover_exact_outgoing_hash_from_history(self):
        unknown = trans_updater.ChainTransactionStatus(
            status="unknown",
            tx_hash=HASH,
            reason="temporary direct lookup error",
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=unknown),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=[self.chain_transaction()]),
            ),
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "confirmed")

    async def test_absent_exact_hash_stays_pending_and_never_becomes_failed(self):
        pending = trans_updater.ChainTransactionStatus(
            status="pending",
            tx_hash=HASH,
            reason="hash not found yet",
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=[]),
            ),
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "pending")

    async def test_exact_hash_with_wrong_amount_does_not_pass_strict_verification(self):
        pending = trans_updater.ChainTransactionStatus(status="pending", tx_hash=HASH)
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(
                    return_value=[self.chain_transaction(amount=999_000)]
                ),
            ),
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "confirmed")
        verified = trans_updater._verify_chain_details_for_record(self.transaction(), result)
        self.assertFalse(verified.ok)
        self.assertIn("amount", verified.reason)
