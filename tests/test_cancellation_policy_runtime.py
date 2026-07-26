"""Regression coverage for cancellation-policy persistence at runtime."""

from unittest import IsolatedAsyncioTestCase, mock

import cancellation_safety
import constants as const


class CancellationPolicyUseTests(IsolatedAsyncioTestCase):
    async def test_guarded_flow_uses_the_persisted_policy(self):
        original = mock.AsyncMock(
            return_value={"ok": True, "reason": "submitted"}
        )
        policy = {
            "fee_amount": 500 * const.LUNA_PER_NIM,
            "fee_address": const.DEV_PLATFORM_FEE_ADDRESS,
        }
        with mock.patch.object(
            cancellation_safety,
            "_ORIGINAL_SUBMIT",
            original,
        ), mock.patch.object(
            cancellation_safety,
            "_ensure_guard_table",
            mock.AsyncMock(),
        ), mock.patch.object(
            cancellation_safety.db_access,
            "get_transactions_by_spot",
            mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            cancellation_safety,
            "_guard_row",
            mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            cancellation_safety,
            "_snapshot_cancellation_policy",
            mock.AsyncMock(return_value=(policy, True)),
        ), mock.patch.object(
            cancellation_safety,
            "_acquire_guard",
            mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            cancellation_safety,
            "_release_guard",
            mock.AsyncMock(),
        ):
            result = (
                await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                    object(),
                    spot_id=7,
                    cancellation_fee=1,
                    fee_address=(
                        "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"
                    ),
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            original.await_args.kwargs["cancellation_fee"],
            500 * const.LUNA_PER_NIM,
        )
        self.assertEqual(
            original.await_args.kwargs["fee_address"],
            const.DEV_PLATFORM_FEE_ADDRESS,
        )
