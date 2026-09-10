from __future__ import annotations

import unittest
from unittest import mock

import constants as const
import database as schema
import trans_updater

PAYOUT_A = "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"
PAYOUT_B = "NQ48 LH6Q 7PFD LJYF 7PGB NJXL F8CX GHTJ YEKG"


class ClaimPayoutIdentityTest(unittest.IsolatedAsyncioTestCase):
    def _claim(self) -> dict:
        return {
            schema.CLAIM_ID: 7,
            schema.CLAIM_SPOT_ID: 3,
            schema.CLAIM_RECIPIENT: 4,
            schema.CLAIM_PAYOUT_ADDRESS: PAYOUT_A,
        }

    async def test_claim_reward_uses_immutable_claim_payout_without_htlc_resolution(self):
        submit = mock.AsyncMock(return_value={"ok": True, "trans_id": 9})
        with (
            mock.patch.object(trans_updater.db_access, "get_claim", mock.AsyncMock(return_value=self._claim())),
            mock.patch.object(trans_updater.db_access, "has_nonfailed_claim_payout_transaction", mock.AsyncMock(return_value=False)),
            mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value={schema.SPOT_ID: 3, schema.SPOT_TITLE: "Safe"})),
            mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)),
            mock.patch.object(trans_updater, "resolve_nimiq_pay_payout_address", mock.AsyncMock()) as resolver,
            mock.patch.object(trans_updater, "_submit_recorded_chain_send", submit),
            mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", False),
        ):
            result = await trans_updater.submit_claim_reward_transaction(object(), claim_id=7, amount=100)

        self.assertTrue(result["ok"])
        self.assertEqual(submit.await_args.kwargs["to_address"], PAYOUT_A)
        resolver.assert_not_awaited()

    async def test_differing_internal_override_cannot_redirect_claim_reward(self):
        with (
            mock.patch.object(trans_updater.db_access, "get_claim", mock.AsyncMock(return_value=self._claim())),
            mock.patch.object(trans_updater.db_access, "has_nonfailed_claim_payout_transaction", mock.AsyncMock(return_value=False)),
            mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value={schema.SPOT_ID: 3, schema.SPOT_TITLE: "Safe"})),
            mock.patch.object(trans_updater, "_submit_recorded_chain_send", mock.AsyncMock()) as submit,
            mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", False),
        ):
            with self.assertRaisesRegex(ValueError, "must equal the immutable claim payout_address"):
                await trans_updater.submit_claim_reward_transaction(
                    object(), claim_id=7, amount=100, to_address=PAYOUT_B
                )

        submit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
