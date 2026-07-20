from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "public_html.py",
    '''    cancellation = _cancellation_summary(transactions)
    cancellation_started = spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
    bucket = _owner_spot_bucket(spot, now=now, status_label=status_label)
''',
    '''    cancellation = _cancellation_summary(transactions)
    cancellation_started = spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
    # cancellation_started_at is a durable audit and claim-blocking marker. It is
    # deliberately retained after completion, so it must not override a terminal
    # database status in the visible badge.
    cancellation_in_progress = (
        cancellation_started
        and int(spot[schema.SPOT_STATUS])
        in {const.SPOT_STATUS_DRAFT, const.SPOT_STATUS_PUBLISHED}
    )
    bucket = _owner_spot_bucket(spot, now=now, status_label=status_label)
''',
)

replace_once(
    "public_html.py",
    '''        "badge_status_label": (
            "cancelling"
            if cancellation_started
''',
    '''        "badge_status_label": (
            "cancelling"
            if cancellation_in_progress
''',
)

replace_once(
    "public_html.py",
    '''        "cancellation_started": cancellation_started,
        "total_value_locked": bool(deposit.get("has_submitted")),
''',
    '''        "cancellation_started": cancellation_started,
        "cancellation_in_progress": cancellation_in_progress,
        "total_value_locked": bool(deposit.get("has_submitted")),
''',
)

(ROOT / "tests/test_cancellation_completion.py").write_text(
    '''from __future__ import annotations

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
''',
    encoding="utf-8",
)

print("Cancellation terminal badge fix applied.")
