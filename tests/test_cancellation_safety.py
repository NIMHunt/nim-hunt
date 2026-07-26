from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, mock

import cancellation_safety
import constants as const
import database as schema


class CancellationSafetyTest(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original = mock.AsyncMock()
        self.previous_original = cancellation_safety._ORIGINAL_SUBMIT
        cancellation_safety._ORIGINAL_SUBMIT = self.original
        cancellation_safety._SPOT_LOCKS.clear()

        async def passthrough_policy(
            _db,
            *,
            spot_id,
            cancellation_fee=None,
            fee_address=None,
        ):
            return (
                {
                    "spot_id": int(spot_id),
                    "fee_amount": int(
                        const.SPOT_CANCELLATION_FEE
                        if cancellation_fee is None
                        else cancellation_fee
                    ),
                    "fee_address": str(
                        fee_address or const.SPOT_CANCELLATION_FEE_ADDRESS
                    ),
                },
                False,
            )

        self.policy_patcher = mock.patch.object(
            cancellation_safety,
            "_snapshot_cancellation_policy",
            side_effect=passthrough_policy,
        )
        self.policy_patcher.start()

    def tearDown(self) -> None:
        self.policy_patcher.stop()
        cancellation_safety._ORIGINAL_SUBMIT = self.previous_original
        cancellation_safety._SPOT_LOCKS.clear()

    async def test_one_proven_failed_refund_is_retried_once(self) -> None:
        failed_refund = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
            schema.TRANS_TX_HASH: "ab" * 32,
        }
        self.original.return_value = {
            "ok": True,
            "spot_id": 7,
            "cancelled": False,
            "cancellation_pending": True,
            "refund": {"ok": True},
        }
        failed_status = cancellation_safety.trans_updater.ChainTransactionStatus(
            status="failed",
            tx_hash="ab" * 32,
            raw={"executionResult": False},
        )

        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[failed_refund]),
            ),
            mock.patch.object(
                cancellation_safety.trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=failed_status),
            ),
            mock.patch.object(cancellation_safety, "_acquire_guard", mock.AsyncMock(return_value=True)),
            mock.patch.object(cancellation_safety, "_release_guard", mock.AsyncMock()) as release_guard,
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
                cancellation_fee=10,
                fee_address="NQ34 fee",
            )

        self.assertEqual(result["refund"], {"ok": True})
        self.original.assert_awaited_once()
        release_guard.assert_awaited_once()
        block_guard.assert_not_awaited()

    async def test_ambiguous_failed_refund_remains_blocked(self) -> None:
        failed_refund = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
            schema.TRANS_TX_HASH: "not-a-chain-hash",
        }
        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[failed_refund]),
            ),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(), spot_id=7
            )
        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.original.assert_not_awaited()
        block_guard.assert_awaited_once()

    async def test_second_failed_refund_is_never_retried(self) -> None:
        failed_refunds = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
                schema.TRANS_TX_HASH: value * 32,
            }
            for value in ("ab", "cd")
        ]
        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=failed_refunds),
            ),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(), spot_id=7
            )
        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.original.assert_not_awaited()
        block_guard.assert_awaited_once()

    async def test_failed_fee_attempt_blocks_all_automatic_resends(self) -> None:
        failed_fee = {
            schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE,
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
        }

        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[failed_fee]),
            ),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()),
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
            )

        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.original.assert_not_awaited()

    async def test_ambiguous_helper_result_persists_a_manual_block(self) -> None:
        self.original.return_value = {
            "ok": True,
            "spot_id": 7,
            "cancelled": False,
            "cancellation_pending": True,
            "reason": "send_retry_pending",
        }

        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[]),
            ),
            mock.patch.object(cancellation_safety, "_acquire_guard", mock.AsyncMock(return_value=True)),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
            mock.patch.object(cancellation_safety, "_release_guard", mock.AsyncMock()) as release_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
            )

        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.original.assert_awaited_once()
        block_guard.assert_awaited_once()
        release_guard.assert_not_awaited()

    async def test_normal_recorded_result_releases_temporary_lease(self) -> None:
        expected = {
            "ok": True,
            "spot_id": 7,
            "cancelled": False,
            "cancellation_pending": True,
            "fee": {"ok": True},
            "refund": {"ok": True},
        }
        self.original.return_value = expected

        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[]),
            ),
            mock.patch.object(cancellation_safety, "_acquire_guard", mock.AsyncMock(return_value=True)),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
            mock.patch.object(cancellation_safety, "_release_guard", mock.AsyncMock()) as release_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
            )

        self.assertEqual(result, expected)
        release_guard.assert_awaited_once()
        block_guard.assert_not_awaited()
