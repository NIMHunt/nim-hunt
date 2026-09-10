from __future__ import annotations

import unittest
from unittest import mock

import cache
import database as schema
import home_metrics_capacity


class HomeMetricsCapacityTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _record(
        spot_id: int,
        *,
        max_total: int,
        successful: int = 0,
        pending: int = 0,
        prizedraw: bool = False,
    ) -> cache.SpotCacheRecord:
        return cache.SpotCacheRecord(
            spot_id=spot_id,
            spot={
                schema.SPOT_ID: spot_id,
                schema.SPOT_MAX_TOTAL_CLAIMS: max_total,
                "success_claim_count": successful,
                "pending_claim_count": pending,
            },
            creator={},
            prizedraw={} if prizedraw else None,
            owner_summary=None,
            claims=[],
            claim_codes=[],
            transactions=[],
        )

    def test_exhausted_standard_spot_has_no_home_capacity(self):
        record = self._record(1, max_total=4, successful=4)
        self.assertFalse(home_metrics_capacity._spot_has_claim_capacity(record))

    def test_pending_standard_duration_claim_does_not_consume_capacity(self):
        record = self._record(1, max_total=4, successful=3, pending=1)
        self.assertTrue(home_metrics_capacity._spot_has_claim_capacity(record))

    def test_pending_prizedraw_entries_consume_capacity(self):
        record = self._record(1, max_total=4, successful=1, pending=3, prizedraw=True)
        self.assertFalse(home_metrics_capacity._spot_has_claim_capacity(record))

    def test_unlimited_spot_remains_active(self):
        record = self._record(1, max_total=0, successful=500, pending=500, prizedraw=True)
        self.assertTrue(home_metrics_capacity._spot_has_claim_capacity(record))

    async def test_home_metric_counts_only_current_spots_with_capacity(self):
        records = {
            1: self._record(1, max_total=4, successful=3),
            2: self._record(2, max_total=4, successful=4),
            3: self._record(3, max_total=2, pending=2, prizedraw=True),
            4: self._record(4, max_total=5),
            5: self._record(5, max_total=0),
        }
        snapshot = cache.SpotCacheSnapshot(
            loaded_at=100,
            now_at_load=100,
            next_transition_at=None,
            spots_by_id=records,
            spot_ids_by_start=[1, 2, 3, 4, 5],
            current_spot_ids_by_start=[1, 2, 3, 5],
            upcoming_spot_ids_by_start=[4],
        )

        with (
            mock.patch.object(
                cache,
                "ensure_spot_cache",
                new=mock.AsyncMock(return_value=snapshot),
            ),
            mock.patch.object(
                cache,
                "get_cached_daily_user_count",
                new=mock.AsyncMock(return_value=14),
            ),
        ):
            metrics = await home_metrics_capacity.get_cached_home_metrics_with_claim_capacity(
                object()
            )

        self.assertEqual(metrics["active_spot_count"], 2)
        self.assertEqual(metrics["daily_user_count"], 14)


if __name__ == "__main__":
    unittest.main()
