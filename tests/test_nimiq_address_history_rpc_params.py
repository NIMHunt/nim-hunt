from __future__ import annotations

import time
from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import database as schema
import trans_updater

ADDRESS = const.DEV_PLATFORM_FEE_ADDRESS
HASH = "ab" * 32


class AddressHistoryRpcParameterTests(IsolatedAsyncioTestCase):
    async def test_unused_start_cursor_is_sent_as_json_null(self):
        response = {"data": [], "metadata": None}
        with mock.patch.object(
            trans_updater.asyncio,
            "to_thread",
            mock.AsyncMock(return_value=response),
        ) as to_thread:
            result = await trans_updater.get_chain_transactions_by_address(
                ADDRESS,
                max_transactions=123,
            )

        self.assertEqual(result, response)
        to_thread.assert_awaited_once()
        args, kwargs = to_thread.await_args
        self.assertIs(args[0], trans_updater._json_rpc_post_sync)
        self.assertEqual(kwargs["method"], "getTransactionsByAddress")
        self.assertEqual(kwargs["params"], [ADDRESS, 123, None])

    async def test_valid_start_cursor_is_sent_as_third_parameter(self):
        with mock.patch.object(
            trans_updater.asyncio,
            "to_thread",
            mock.AsyncMock(return_value={"data": [], "metadata": None}),
        ) as to_thread:
            await trans_updater.get_chain_transactions_by_address(
                ADDRESS,
                max_transactions=25,
                start_at=HASH,
            )

        _args, kwargs = to_thread.await_args
        self.assertEqual(kwargs["params"], [ADDRESS, 25, HASH])

    async def test_invalid_start_cursor_is_rejected_before_rpc_call(self):
        with mock.patch.object(
            trans_updater.asyncio,
            "to_thread",
            mock.AsyncMock(),
        ) as to_thread:
            with self.assertRaisesRegex(ValueError, "64-character hexadecimal"):
                await trans_updater.get_chain_transactions_by_address(
                    ADDRESS,
                    start_at="not-a-hash",
                )
            to_thread.assert_not_awaited()

    async def test_cancellation_recovery_succeeds_with_null_default_cursor(self):
        transaction = {
            schema.TRANS_ID: 91,
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
            schema.TRANS_TX_HASH: HASH,
            schema.TRANS_FROM_ADDRESS: ADDRESS,
            schema.TRANS_TO_ADDRESS: ADDRESS,
            schema.TRANS_AMOUNT: 123_000,
            schema.TRANS_CREATED_AT: int(time.time()),
        }
        pending = trans_updater.ChainTransactionStatus(
            status="pending",
            tx_hash=HASH,
            reason="hash not found yet",
        )
        chain_transaction = {
            "hash": HASH,
            "sender": ADDRESS,
            "recipient": ADDRESS,
            "value": 123_000,
            "blockNumber": 456,
            "executionResult": True,
        }

        def strict_rpc(**kwargs):
            self.assertEqual(kwargs["method"], "getTransactionsByAddress")
            self.assertEqual(kwargs["params"], [ADDRESS, 500, None])
            return {"data": [chain_transaction], "metadata": None}

        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "_json_rpc_post_sync",
                side_effect=strict_rpc,
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                mock.AsyncMock(),
            ) as send,
        ):
            result = await trans_updater.check_pending_transaction(transaction)

        self.assertEqual(result.status, "confirmed")
        verified = trans_updater._verify_chain_details_for_record(transaction, result)
        self.assertTrue(verified.ok)
        send.assert_not_awaited()
