from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]

(ROOT / "tests" / "test_cancellation_at_most_once.py").write_text(
    dedent('''\
    from __future__ import annotations

    from unittest import IsolatedAsyncioTestCase, mock

    import constants as const
    import database as schema
    import trans_updater


    class FakeDb:
        async def execute(self, *args, **kwargs):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None


    def published_spot() -> dict:
        return {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_CREATED_BY: 1,
            schema.SPOT_MAX_TOTAL_CLAIMS: 0,
        }


    def failed_leg(trans_type: int) -> dict:
        return {
            schema.TRANS_TYPE: int(trans_type),
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
            schema.TRANS_AMOUNT: 100,
        }


    class CancellationAtMostOnceTest(IsolatedAsyncioTestCase):
        async def _assert_failed_leg_blocks_all_sends(self, trans_type: int) -> None:
            spot = published_spot()
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
                    mock.AsyncMock(return_value=[failed_leg(trans_type)]),
                ),
                mock.patch.object(
                    trans_updater.db_access,
                    "mark_spot_cancellation_started",
                    mock.AsyncMock(),
                ),
                mock.patch.object(
                    trans_updater.db_access,
                    "get_unpaid_successful_standard_claim_ids",
                    mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    trans_updater,
                    "submit_platform_fee_transaction",
                    mock.AsyncMock(),
                ) as send_fee,
                mock.patch.object(
                    trans_updater,
                    "submit_spot_refund_transaction",
                    mock.AsyncMock(),
                ) as send_refund,
                mock.patch.object(
                    trans_updater.cache,
                    "notify_spot_changed",
                    mock.AsyncMock(),
                ),
                mock.patch.object(
                    trans_updater.cache,
                    "notify_user_changed",
                    mock.AsyncMock(),
                ),
            ):
                result = await trans_updater.submit_spot_cancellation_transactions(
                    FakeDb(),
                    spot_id=7,
                    cancellation_fee=10,
                    fee_address="NQ34 fee",
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["cancellation_pending"])
            self.assertEqual(result["reason"], "manual_reconciliation_required")
            send_fee.assert_not_awaited()
            send_refund.assert_not_awaited()

        async def test_failed_refund_is_never_automatically_resent(self) -> None:
            await self._assert_failed_leg_blocks_all_sends(const.TRANS_TYPE_CANCEL_SPOT)

        async def test_failed_cancellation_fee_is_never_automatically_resent(self) -> None:
            await self._assert_failed_leg_blocks_all_sends(const.TRANS_TYPE_PLAT_FEE)
    '''),
    encoding="utf-8",
)

print("Added cancellation regression tests.")
