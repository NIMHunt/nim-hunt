from __future__ import annotations

import tempfile
import time
import unittest
from contextlib import ExitStack, asynccontextmanager
from unittest import mock

import cache
import constants as const
import database as schema
import db_access
import settlement_updater
import trans_updater


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
        async def fake_transaction(db):
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


class PrizedrawParticipantLifecycleTest(unittest.IsolatedAsyncioTestCase):
    """Exercise a complete multi-participant draw against the real SQLite schema."""

    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def _create_ready_prizedraw(
        self,
        *,
        participant_count: int = 4,
        prize_count: int = 2,
        total_value: int = 200_000_001,
    ) -> tuple[int, list[int]]:
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="prizedraw-owner")
            spot_id = await db_access.create_spot(
                db,
                created_by=owner_id,
                title="Lifecycle Draw",
                lat=51.5,
                long=-0.1,
                radius=const.MIN_SPOT_RADIUS_METRES,
                claim_duration=0,
                max_claims_per_user=1,
                max_total_claims=participant_count,
                total_value=total_value,
                starts_at=int(time.time()) - 60,
                ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                is_prizedraw=True,
                auto_reverse_geocode=False,
            )
            await db.execute(
                f"""
                INSERT INTO {schema.PRIZEDRAW_TABLE_NAME} (
                    {schema.PRIZEDRAW_SPOT_ID},
                    {schema.PRIZEDRAW_PRIZE_COUNT}
                )
                VALUES (?, ?);
                """,
                (spot_id, prize_count),
            )
            await db.execute(
                f"""
                UPDATE {schema.SPOT_TABLE_NAME}
                SET {schema.SPOT_STATUS} = ?
                WHERE {schema.SPOT_ID} = ?;
                """,
                (const.SPOT_STATUS_PUBLISHED, spot_id),
            )

            claim_ids: list[int] = []
            for index in range(participant_count):
                participant_id = await db_access.create_user(
                    db,
                    device_id_hash=f"prizedraw-participant-{index}",
                )
                cur = await db.execute(
                    f"""
                    INSERT INTO {schema.CLAIM_TABLE_NAME} (
                        {schema.CLAIM_SPOT_ID},
                        {schema.CLAIM_RECIPIENT},
                        {schema.CLAIM_PAYOUT_ADDRESS},
                        {schema.CLAIM_LAT},
                        {schema.CLAIM_LONG},
                        {schema.CLAIM_ACCURACY},
                        {schema.CLAIM_STATUS}
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        spot_id,
                        participant_id,
                        const.DEV_PLATFORM_FEE_ADDRESS,
                        51.5,
                        -0.1,
                        1.0,
                        const.CLAIM_STATUS_SUCCESS,
                    ),
                )
                claim_ids.append(int(cur.lastrowid))

            await db.commit()
        return spot_id, claim_ids

    async def _claim_statuses(self, *, spot_id: int) -> dict[int, int]:
        async with schema.get_db() as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT {schema.CLAIM_ID}, {schema.CLAIM_STATUS}
                FROM {schema.CLAIM_TABLE_NAME}
                WHERE {schema.CLAIM_SPOT_ID} = ?
                ORDER BY {schema.CLAIM_ID};
                """,
                (spot_id,),
            )
        return {
            int(row[schema.CLAIM_ID]): int(row[schema.CLAIM_STATUS])
            for row in rows
        }

    async def _payout_rows(self, *, spot_id: int) -> list[dict]:
        async with schema.get_db() as db:
            rows = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )
        return [
            row
            for row in rows
            if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CLAIM
        ]

    async def test_draw_persists_winners_and_never_double_pays_across_retries(self):
        total_value = 200_000_001
        spot_id, participant_claim_ids = await self._create_ready_prizedraw(
            participant_count=4,
            prize_count=2,
            total_value=total_value,
        )

        chosen_claim_ids: list[int] = []
        submitted: list[tuple[str, int]] = []

        def choose_winners(population, winner_count):
            self.assertEqual(winner_count, 2)
            population_ids = [int(row[schema.CLAIM_ID]) for row in population]
            self.assertEqual(set(population_ids), set(participant_claim_ids))
            chosen = [population[1], population[-1]]
            chosen_claim_ids[:] = sorted(int(row[schema.CLAIM_ID]) for row in chosen)
            return chosen

        async def fake_chain_send(*, spot, to_address, amount, memo=None):
            tx_hash = f"{len(submitted) + 1:064x}"
            submitted.append((tx_hash, int(amount)))
            return trans_updater.SubmittedChainTransaction(
                tx_hash=tx_hash,
                from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                to_address=to_address,
                amount=int(amount),
            )

        with (
            mock.patch.object(
                settlement_updater.secrets.SystemRandom,
                "sample",
                side_effect=choose_winners,
            ) as sample,
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=fake_chain_send,
            ) as send,
            mock.patch.object(cache, "notify_spot_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_claim_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_user_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_transaction_changed", mock.AsyncMock()),
        ):
            settled = await settlement_updater.settle_prizedraw_spot_if_ready(
                spot_id=spot_id
            )
            repeated = await settlement_updater.settle_prizedraw_spot_if_ready(
                spot_id=spot_id
            )

            self.assertTrue(settled["ok"], settled)
            self.assertTrue(settled["settled"], settled)
            self.assertEqual(settled["winner_claim_ids"], chosen_claim_ids)
            self.assertTrue(repeated["ok"], repeated)
            self.assertFalse(repeated["settled"], repeated)
            sample.assert_called_once()
            self.assertEqual(send.await_count, 2)

            payout_rows = await self._payout_rows(spot_id=spot_id)
            self.assertEqual(len(payout_rows), 2)
            self.assertEqual(
                sum(int(row[schema.TRANS_AMOUNT]) for row in payout_rows),
                total_value,
            )
            self.assertEqual(
                {int(row[schema.TRANS_CLAIM_ID]) for row in payout_rows},
                set(chosen_claim_ids),
            )
            self.assertTrue(
                all(
                    int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_PENDING
                    for row in payout_rows
                )
            )

            statuses = await self._claim_statuses(spot_id=spot_id)
            self.assertEqual(
                {
                    claim_id
                    for claim_id, status in statuses.items()
                    if status == const.CLAIM_STATUS_PENDING
                },
                set(chosen_claim_ids),
            )
            self.assertEqual(
                {
                    claim_id
                    for claim_id, status in statuses.items()
                    if status == const.CLAIM_STATUS_SUCCESS
                },
                set(participant_claim_ids) - set(chosen_claim_ids),
            )

            first_row = min(
                payout_rows,
                key=lambda row: int(row[schema.TRANS_CLAIM_ID]),
            )
            second_row = max(
                payout_rows,
                key=lambda row: int(row[schema.TRANS_CLAIM_ID]),
            )

            async with schema.get_db() as db:
                await trans_updater.mark_trans_as_confirmed(
                    db,
                    first_row,
                    block_number=500,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address=first_row[schema.TRANS_FROM_ADDRESS],
                        to_address=first_row[schema.TRANS_TO_ADDRESS],
                        amount=int(first_row[schema.TRANS_AMOUNT]),
                    ),
                )

            retry_while_pending = (
                await settlement_updater.retry_pending_prizedraw_payouts_for_spot(
                    spot_id=spot_id
                )
            )
            self.assertTrue(retry_while_pending["ok"], retry_while_pending)
            self.assertFalse(retry_while_pending["retried"], retry_while_pending)
            self.assertEqual(send.await_count, 2)
            self.assertEqual(len(await self._payout_rows(spot_id=spot_id)), 2)

            async with schema.get_db() as db:
                refreshed_second = await db_access.get_transaction(
                    db,
                    trans_id=int(second_row[schema.TRANS_ID]),
                )
                await trans_updater.mark_trans_as_failed(
                    db,
                    refreshed_second,
                    reason="executionResult was false",
                )

            retry_after_proven_failure = (
                await settlement_updater.retry_pending_prizedraw_payouts_for_spot(
                    spot_id=spot_id
                )
            )
            self.assertTrue(
                retry_after_proven_failure["ok"],
                retry_after_proven_failure,
            )
            self.assertTrue(
                retry_after_proven_failure["retried"],
                retry_after_proven_failure,
            )
            self.assertEqual(send.await_count, 3)

            retried_rows = await self._payout_rows(spot_id=spot_id)
            self.assertEqual(len(retried_rows), 3)
            second_claim_rows = [
                row
                for row in retried_rows
                if int(row[schema.TRANS_CLAIM_ID])
                == int(second_row[schema.TRANS_CLAIM_ID])
            ]
            self.assertEqual(len(second_claim_rows), 2)
            self.assertEqual(
                {
                    int(row[schema.TRANS_STATUS])
                    for row in second_claim_rows
                },
                {const.TRANS_STATUS_FAILED, const.TRANS_STATUS_PENDING},
            )
            self.assertEqual(
                {
                    int(row[schema.TRANS_AMOUNT])
                    for row in second_claim_rows
                },
                {int(second_row[schema.TRANS_AMOUNT])},
            )

            pending_retry = next(
                row
                for row in second_claim_rows
                if int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_PENDING
            )
            async with schema.get_db() as db:
                await trans_updater.mark_trans_as_confirmed(
                    db,
                    pending_retry,
                    block_number=501,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address=pending_retry[schema.TRANS_FROM_ADDRESS],
                        to_address=pending_retry[schema.TRANS_TO_ADDRESS],
                        amount=int(pending_retry[schema.TRANS_AMOUNT]),
                    ),
                )

            final_retry = (
                await settlement_updater.retry_pending_prizedraw_payouts_for_spot(
                    spot_id=spot_id
                )
            )
            self.assertTrue(final_retry["ok"], final_retry)
            self.assertEqual(final_retry["reason"], "no_pending_winners")
            self.assertEqual(send.await_count, 3)

        final_statuses = await self._claim_statuses(spot_id=spot_id)
        self.assertTrue(
            all(
                status == const.CLAIM_STATUS_SUCCESS
                for status in final_statuses.values()
            )
        )
        async with schema.get_db() as db:
            persisted_winners = await db_access.get_prizedraw_winner_claim_ids(
                db,
                spot_id=spot_id,
            )
        self.assertEqual(persisted_winners, chosen_claim_ids)
