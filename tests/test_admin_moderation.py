from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest import mock

import admin_moderation
import constants as const
import database as schema


class AdminModerationTests(unittest.IsolatedAsyncioTestCase):
    def _spot(self, *, status: int = const.SPOT_STATUS_BANNED) -> dict:
        return {
            schema.SPOT_ID: 7,
            schema.SPOT_CREATED_BY: 3,
            schema.SPOT_STATUS: status,
            schema.SPOT_TITLE: "Reported Spot",
            schema.SPOT_DEPOSIT_ADDRESS: "spot-deposit",
            schema.SPOT_CANCELLATION_STARTED_AT: None,
        }

    def test_financial_state_uses_only_confirmed_chain_state(self):
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 1_000,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 200,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CREATION_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 50,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM,
                schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
                schema.TRANS_AMOUNT: 100,
            },
        ]

        state = admin_moderation._financial_state(transactions)

        self.assertEqual(state["confirmed_deposits"], 1_000)
        self.assertEqual(state["confirmed_outgoing"], 250)
        self.assertEqual(state["remaining"], 750)
        self.assertEqual(state["pending"], [])

    async def test_sweep_uses_server_calculated_amount_and_fixed_address(self):
        db = mock.MagicMock()
        db.commit = mock.AsyncMock()
        spot = self._spot()
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 1_000,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 250,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CREATION_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 50,
            },
        ]

        @asynccontextmanager
        async def db_context():
            yield db

        submit = mock.AsyncMock(return_value={"trans_id": 44, "tx_hash": "chain-hash"})
        with (
            mock.patch.object(admin_moderation, "get_db", side_effect=lambda: db_context()),
            mock.patch.object(admin_moderation.admin_store, "ensure_admin_tables", mock.AsyncMock()),
            mock.patch.object(admin_moderation.admin_store, "upsert_ban_record", mock.AsyncMock()),
            mock.patch.object(admin_moderation.admin_store, "record_audit", mock.AsyncMock()),
            mock.patch.object(admin_moderation.db_access, "get_spot", mock.AsyncMock(return_value=spot)),
            mock.patch.object(
                admin_moderation.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=transactions),
            ),
            mock.patch.object(admin_moderation, "_normalise_address", side_effect=lambda value, **_: value),
            mock.patch.object(admin_moderation.const, "SPOT_FEE_ADDRESS", "fixed-cancellation-address"),
            mock.patch.object(admin_moderation.trans_updater, "_submit_recorded_chain_send", submit),
        ):
            result = await admin_moderation.attempt_banned_spot_sweep(spot_id=7)

        self.assertTrue(result["submitted"])
        self.assertEqual(result["amount"], 700)
        submit.assert_awaited_once()
        kwargs = submit.await_args.kwargs
        self.assertEqual(kwargs["amount"], 700)
        self.assertEqual(kwargs["to_address"], "fixed-cancellation-address")
        self.assertIs(kwargs["create_transaction"], admin_moderation._create_ban_sweep_transaction)
        self.assertEqual(kwargs["create_transaction_kwargs"]["spot_id"], 7)

    async def test_pending_transaction_defers_sweep_instead_of_guessing(self):
        db = mock.MagicMock()
        db.commit = mock.AsyncMock()
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 1_000,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CLAIM,
                schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
                schema.TRANS_AMOUNT: 100,
            },
        ]

        @asynccontextmanager
        async def db_context():
            yield db

        submit = mock.AsyncMock()
        with (
            mock.patch.object(admin_moderation, "get_db", side_effect=lambda: db_context()),
            mock.patch.object(admin_moderation.admin_store, "ensure_admin_tables", mock.AsyncMock()),
            mock.patch.object(admin_moderation.admin_store, "upsert_ban_record", mock.AsyncMock()),
            mock.patch.object(
                admin_moderation.db_access,
                "get_spot",
                mock.AsyncMock(return_value=self._spot()),
            ),
            mock.patch.object(
                admin_moderation.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=transactions),
            ),
            mock.patch.object(admin_moderation.trans_updater, "_submit_recorded_chain_send", submit),
        ):
            result = await admin_moderation.attempt_banned_spot_sweep(spot_id=7)

        self.assertTrue(result["deferred"])
        self.assertEqual(result["reason"], "transactions_pending")
        submit.assert_not_awaited()

    async def test_claim_transaction_guard_blocks_banned_spot(self):
        original = mock.AsyncMock(return_value=99)
        old_original = admin_moderation._ORIGINAL_CREATE_CLAIM_TRANSACTION
        admin_moderation._ORIGINAL_CREATE_CLAIM_TRANSACTION = original
        try:
            with (
                mock.patch.object(
                    admin_moderation.db_access,
                    "get_claim",
                    mock.AsyncMock(return_value={schema.CLAIM_SPOT_ID: 7}),
                ),
                mock.patch.object(
                    admin_moderation.db_access,
                    "get_spot",
                    mock.AsyncMock(return_value=self._spot(status=const.SPOT_STATUS_BANNED)),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Spot has been banned"):
                    await admin_moderation._guarded_create_claim_transaction(
                        object(),
                        user_id=9,
                        claim_id=12,
                        amount=100,
                        from_address="from",
                        to_address="to",
                        tx_hash="hash",
                    )
        finally:
            admin_moderation._ORIGINAL_CREATE_CLAIM_TRANSACTION = old_original

        original.assert_not_awaited()

    async def test_sweep_transaction_rejects_browser_selected_recipient(self):
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 500,
            }
        ]
        with (
            mock.patch.object(
                admin_moderation.db_access,
                "get_spot",
                mock.AsyncMock(return_value=self._spot()),
            ),
            mock.patch.object(
                admin_moderation.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=transactions),
            ),
            mock.patch.object(admin_moderation, "_normalise_address", side_effect=lambda value, **_: value),
            mock.patch.object(admin_moderation.const, "SPOT_FEE_ADDRESS", "fixed-cancellation-address"),
            mock.patch.object(admin_moderation.db_access, "_create_transaction", mock.AsyncMock()) as create,
        ):
            with self.assertRaisesRegex(ValueError, "configured cancellation address"):
                await admin_moderation._create_ban_sweep_transaction(
                    object(),
                    user_id=3,
                    spot_id=7,
                    amount=500,
                    from_address="spot-deposit",
                    to_address="attacker-address",
                    tx_hash="hash",
                )

        create.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
