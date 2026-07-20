from __future__ import annotations

import unittest
from unittest import mock

import constants as const
import database as schema
import public_html
import trans_updater


class CancellationCompletionTests(unittest.IsolatedAsyncioTestCase):
    def _spot(self, *, status: int, cancellation_started_at: int | None) -> dict:
        return {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: status,
            schema.SPOT_TITLE: "Cancellation Test",
            schema.SPOT_CANCELLATION_STARTED_AT: cancellation_started_at,
            schema.SPOT_MAX_TOTAL_CLAIMS: 1,
        }

    def test_terminal_cancelled_status_overrides_durable_cancellation_marker(self):
        serialised = public_html._serialise_owner_spot(
            self._spot(
                status=const.SPOT_STATUS_CANCELLED,
                cancellation_started_at=123456,
            ),
            now=123999,
            transactions=[],
        )

        self.assertEqual(serialised["status_label"], "cancelled")
        self.assertEqual(serialised["badge_status_label"], "cancelled")
        self.assertEqual(serialised["bucket"], "previous")
        self.assertTrue(serialised["cancellation_started"])
        self.assertFalse(serialised["cancellation_in_progress"])

    def test_nonterminal_spot_with_marker_still_displays_cancelling(self):
        serialised = public_html._serialise_owner_spot(
            self._spot(
                status=const.SPOT_STATUS_DRAFT,
                cancellation_started_at=123456,
            ),
            now=123999,
            transactions=[],
        )

        self.assertEqual(serialised["status_label"], "draft")
        self.assertEqual(serialised["badge_status_label"], "cancelling")
        self.assertTrue(serialised["cancellation_in_progress"])

    async def test_confirmed_fee_and_refund_make_spot_terminal(self):
        spot = self._spot(
            status=const.SPOT_STATUS_PUBLISHED,
            cancellation_started_at=123456,
        )
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 1_100,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CREATION_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 100,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 10,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 990,
            },
        ]

        with (
            mock.patch.object(
                trans_updater.db_access,
                "get_spot",
                mock.AsyncMock(return_value=spot),
            ),
            mock.patch.object(
                trans_updater.db_access,
                "is_prizedraw",
                mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(
                trans_updater.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=transactions),
            ),
            mock.patch.object(
                trans_updater.db_access,
                "set_spot_status_to_cancelled",
                mock.AsyncMock(),
            ) as set_cancelled,
        ):
            finalised = await trans_updater._finalize_cancelled_spot_if_ready(
                object(),
                spot_id=7,
            )

        self.assertTrue(finalised)
        set_cancelled.assert_awaited_once_with(mock.ANY, spot_id=7)


if __name__ == "__main__":
    unittest.main()
