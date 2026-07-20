from __future__ import annotations

import time
import unittest
from unittest import mock

import trans_updater


class ChainHeadPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.old_height = trans_updater._CHAIN_HEAD_HEIGHT
        self.old_updated = trans_updater._CHAIN_HEAD_UPDATED_AT
        self.old_error = trans_updater._CHAIN_HEAD_LAST_ERROR
        trans_updater._CHAIN_HEAD_HEIGHT = None
        trans_updater._CHAIN_HEAD_UPDATED_AT = None
        trans_updater._CHAIN_HEAD_LAST_ERROR = None

    def tearDown(self) -> None:
        trans_updater._CHAIN_HEAD_HEIGHT = self.old_height
        trans_updater._CHAIN_HEAD_UPDATED_AT = self.old_updated
        trans_updater._CHAIN_HEAD_LAST_ERROR = self.old_error

    async def test_reads_compact_block_number_rpc_shape(self):
        with mock.patch.object(
            trans_updater,
            "_json_rpc_post_sync",
            return_value={"data": 123456, "metadata": None},
        ) as rpc:
            height = await trans_updater.get_chain_head_height(
                rpc_url="https://rpc.test.invalid/",
                timeout_seconds=2,
            )

        self.assertEqual(height, 123456)
        self.assertEqual(rpc.call_args.kwargs["method"], "getBlockNumber")
        self.assertEqual(rpc.call_args.kwargs["params"], [])

    async def test_deposit_uses_recent_cache_without_new_rpc_request(self):
        trans_updater._CHAIN_HEAD_HEIGHT = 456789
        trans_updater._CHAIN_HEAD_UPDATED_AT = time.monotonic()
        with mock.patch.object(
            trans_updater,
            "refresh_chain_head_height",
            mock.AsyncMock(side_effect=AssertionError("unexpected RPC refresh")),
        ) as refresh:
            height = await trans_updater.get_chain_head_height_for_deposit(
                max_age_seconds=300,
            )

        self.assertEqual(height, 456789)
        refresh.assert_not_awaited()

    async def test_stale_cache_is_refreshed(self):
        trans_updater._CHAIN_HEAD_HEIGHT = 100
        trans_updater._CHAIN_HEAD_UPDATED_AT = time.monotonic() - 301
        with mock.patch.object(
            trans_updater,
            "refresh_chain_head_height",
            mock.AsyncMock(return_value=200),
        ) as refresh:
            height = await trans_updater.get_chain_head_height_for_deposit(
                max_age_seconds=300,
            )

        self.assertEqual(height, 200)
        refresh.assert_awaited_once()

    async def test_refresh_records_failures_without_destroying_last_height(self):
        trans_updater._CHAIN_HEAD_HEIGHT = 777
        trans_updater._CHAIN_HEAD_UPDATED_AT = time.monotonic() - 999
        with mock.patch.object(
            trans_updater,
            "get_chain_head_height",
            mock.AsyncMock(side_effect=TimeoutError("temporary RPC timeout")),
        ):
            with self.assertRaises(TimeoutError):
                await trans_updater.refresh_chain_head_height()

        self.assertEqual(trans_updater._CHAIN_HEAD_HEIGHT, 777)
        self.assertIn("temporary RPC timeout", trans_updater._CHAIN_HEAD_LAST_ERROR)


if __name__ == "__main__":
    unittest.main()
