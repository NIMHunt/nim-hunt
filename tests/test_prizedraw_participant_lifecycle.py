from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

import cache
import constants as const
import database as schema
import db_access
import settlement_updater
import trans_updater


class PrizedrawParticipantLifecycleTest(unittest.IsolatedAsyncioTestCase):
    """Exercise a multi-user draw through the real SQLite and outbox layers."""

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
            owner_id = await db_access.create_user(
                db,
                device_id_hash="prizedraw-lifecycle-owner",
            )
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

            # create_spot() deliberately permits local placeholder addresses.
            # The production payout path correctly rejects those, so this
            # integration fixture uses a syntactically valid TestAlbatross
            # address while the external broadcast itself remains mocked.
            await db.execute(
                f"""
                UPDATE {schema.SPOT_TABLE_NAME}
                SET {schema.SPOT_DEPOSIT_ADDRESS} = ?,
                    {schema.SPOT_STATUS} = ?
                WHERE {schema.SPOT_ID} = ?;
                """,
                (
                    const.DEV_PLATFORM_FEE_ADDRESS,
                    const.SPOT_STATUS_PUBLISHED,
                    spot_id,
                ),
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

            claim_ids: list[int] = []
            for index in range(participant_count):
                participant_id = await db_access.create_user(
                    db,
                    device_id_hash=f"prizedraw-lifecycle-participant-{index}",
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

    async def test_draw_and_payouts_survive_independent_recovery_passes(self):
        total_value = 200_000_001
        spot_id, participant_claim_ids = await self._create_ready_prizedraw(
            participant_count=4,
            prize_count=2,
            total_value=total_value,
        )

        selected_claim_ids: list[int] = []
        broadcasts: list[tuple[str, int]] = []

        def choose_winners(population, winner_count):
            self.assertEqual(winner_count, 2)
            self.assertEqual(
                {int(row[schema.CLAIM_ID]) for row in population},
                set(participant_claim_ids),
            )
            winners = [population[1], population[-1]]
            selected_claim_ids[:] = sorted(
                int(row[schema.CLAIM_ID]) for row in winners
            )
            return winners

        async def fake_chain_send(*, spot, to_address, amount, memo=None):
            tx_hash = f"{len(broadcasts) + 1:064x}"
            broadcasts.append((tx_hash, int(amount)))
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
            self.assertTrue(settled["ok"], settled)
            self.assertTrue(settled["settled"], settled)
            self.assertTrue(settled["payout_retry"]["ok"], settled)
            self.assertEqual(settled["winner_claim_ids"], selected_claim_ids)
            sample.assert_called_once()
            self.assertEqual(send.await_count, 2)

            # A second settlement pass represents a later worker/process run. It
            # must read the completed state rather than selecting new winners.
            repeated = await settlement_updater.settle_prizedraw_spot_if_ready(
                spot_id=spot_id
            )
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
                set(selected_claim_ids),
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
                set(selected_claim_ids),
            )
            self.assertEqual(
                {
                    claim_id
                    for claim_id, status in statuses.items()
                    if status == const.CLAIM_STATUS_SUCCESS
                },
                set(participant_claim_ids) - set(selected_claim_ids),
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

            # One winner is confirmed and one still has a durable pending
            # transaction. Recovery must not send either winner again.
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
                current_second = await db_access.get_transaction(
                    db,
                    trans_id=int(second_row[schema.TRANS_ID]),
                )
                self.assertIsNotNone(current_second)
                await trans_updater.mark_trans_as_failed(
                    db,
                    current_second,
                    reason="executionResult was false",
                )

            # A failure proven on-chain may be retried, but only for the same
            # persisted winner and exact amount. The confirmed winner stays paid.
            retry_after_failure = (
                await settlement_updater.retry_pending_prizedraw_payouts_for_spot(
                    spot_id=spot_id
                )
            )
            self.assertTrue(retry_after_failure["ok"], retry_after_failure)
            self.assertTrue(retry_after_failure["retried"], retry_after_failure)
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
                {int(row[schema.TRANS_AMOUNT]) for row in second_claim_rows},
                {int(second_row[schema.TRANS_AMOUNT])},
            )

            retry_row = next(
                row
                for row in second_claim_rows
                if int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_PENDING
            )
            async with schema.get_db() as db:
                await trans_updater.mark_trans_as_confirmed(
                    db,
                    retry_row,
                    block_number=501,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address=retry_row[schema.TRANS_FROM_ADDRESS],
                        to_address=retry_row[schema.TRANS_TO_ADDRESS],
                        amount=int(retry_row[schema.TRANS_AMOUNT]),
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
        self.assertEqual(persisted_winners, selected_claim_ids)
