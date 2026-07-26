import unittest
from contextlib import ExitStack, asynccontextmanager
from unittest import mock

import constants as const
import database as schema
import settlement_updater


class PrizedrawPrizeAmountsTest(unittest.TestCase):
    def test_fewer_winners_than_prize_slots_receive_full_pool(self):
        self.assertEqual(
            settlement_updater._prize_amounts(total_value=100, prize_count=3, winner_count=2),
            [50, 50],
        )

    def test_indivisible_remainder_goes_to_first_actual_winner(self):
        self.assertEqual(
            settlement_updater._prize_amounts(total_value=101, prize_count=3, winner_count=2),
            [51, 50],
        )

    def test_no_winners_have_no_payouts(self):
        self.assertEqual(
            settlement_updater._prize_amounts(total_value=101, prize_count=3, winner_count=0),
            [],
        )

    def test_claim_id_mapping_is_stable_and_uses_actual_winner_count(self):
        self.assertEqual(
            settlement_updater._prize_amounts_by_claim_id(
                total_value=101,
                prize_count=3,
                winner_claim_ids=[9, 3],
            ),
            {3: 51, 9: 50},
        )


class PrizedrawSettlementIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_undersubscribed_settlement_payouts_sum_to_full_spot_value(self):
        @asynccontextmanager
        async def fake_get_db():
            yield object()

        @asynccontextmanager
        async def fake_transaction(db, *, immediate=False):
            self.assertTrue(immediate)
            yield

        spot_published = {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_TOTAL_VALUE: 101,
            schema.SPOT_CREATED_BY: 1,
        }
        spot_completed = {**spot_published, schema.SPOT_STATUS: const.SPOT_STATUS_COMPLETED}
        prizedraw = {schema.PRIZEDRAW_PRIZE_COUNT: 3}
        selected_claims = [{schema.CLAIM_ID: 9}, {schema.CLAIM_ID: 3}]
        pending_winners = [
            {schema.CLAIM_ID: 9, schema.CLAIM_RECIPIENT: 90},
            {schema.CLAIM_ID: 3, schema.CLAIM_RECIPIENT: 30},
        ]
        sent: list[tuple[int, int]] = []

        async def fake_submit_claim_reward_transaction(db, *, claim_id, amount, **kwargs):
            sent.append((int(claim_id), int(amount)))
            return {"ok": True, "claim_id": int(claim_id), "amount": int(amount)}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(settlement_updater, "get_db", fake_get_db))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "transaction", fake_transaction))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "get_spot", mock.AsyncMock(side_effect=[spot_published, spot_completed])))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=True)))
            stack.enter_context(mock.patch.object(settlement_updater, "_get_unixepoch", mock.AsyncMock(return_value=123)))
            stack.enter_context(mock.patch.object(settlement_updater, "_settlement_ready_reason", mock.AsyncMock(return_value="ended")))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "fail_pending_claims_for_spot", mock.AsyncMock(return_value=0)))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "get_successful_claims_for_spot", mock.AsyncMock(return_value=selected_claims)))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "get_prizedraw", mock.AsyncMock(return_value=prizedraw)))
            stack.enter_context(mock.patch.object(settlement_updater.secrets.SystemRandom, "sample", return_value=selected_claims))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "mark_prizedraw_winners_pending", mock.AsyncMock(return_value={"updated_count": 2})))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "set_spot_status_to_completed", mock.AsyncMock()))
            stack.enter_context(mock.patch.object(settlement_updater.cache, "notify_spot_changed", mock.AsyncMock()))
            stack.enter_context(mock.patch.object(settlement_updater.cache, "notify_claim_changed", mock.AsyncMock()))
            stack.enter_context(mock.patch.object(settlement_updater.cache, "notify_user_changed", mock.AsyncMock()))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "get_pending_claims_for_spot", mock.AsyncMock(return_value=pending_winners)))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "get_prizedraw_winner_claim_ids", mock.AsyncMock(return_value=[9, 3])))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "has_confirmed_claim_payout_transaction", mock.AsyncMock(return_value=False)))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "has_nonfailed_claim_payout_transaction", mock.AsyncMock(return_value=False)))
            stack.enter_context(mock.patch.object(settlement_updater.db_access, "latest_failed_claim_payout_amount", mock.AsyncMock(return_value=None)))
            stack.enter_context(mock.patch.object(settlement_updater.trans_updater, "submit_claim_reward_transaction", fake_submit_claim_reward_transaction))
            result = await settlement_updater.settle_prizedraw_spot_if_ready(spot_id=7)

        self.assertTrue(result["ok"], result)
        self.assertEqual(sent, [(3, 51), (9, 50)])
        self.assertEqual(sum(amount for _, amount in sent), spot_published[schema.SPOT_TOTAL_VALUE])
