from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from unittest import mock

from fastapi import BackgroundTasks

import cache
import constants as const
import database as schema
import db_access
import main
import public_html
import settlement_updater
import trans_updater


class FinancialConcurrencyIntegrationTest(unittest.IsolatedAsyncioTestCase):
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

    async def _gather_with_deadlock_timeout(self, *awaitables):
        """Run concurrent operations with one generous deadlock guard.

        Coordination inside the tests uses events rather than short sleeps. The
        timeout is not part of the race itself; it only stops CI hanging forever
        if a real locking regression is introduced.
        """
        return await asyncio.wait_for(
            asyncio.gather(*awaitables),
            timeout=5,
        )

    async def _create_published_spot(
        self,
        *,
        suffix: str,
        max_total_claims: int,
        is_prizedraw: bool = False,
        prize_count: int = 1,
    ) -> tuple[int, list[tuple[int, str]]]:
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(
                db,
                device_id_hash=f"owner-{suffix}",
            )
            total_value = (
                const.MIN_PRIZEDRAW_PRIZE_PAYOUT * prize_count
                if is_prizedraw
                else max(
                    const.MIN_SPOT_TOTAL_VALUE,
                    const.MIN_STANDARD_CLAIM_PAYOUT * max_total_claims,
                )
            )
            spot_id = await db_access.create_spot(
                db,
                created_by=owner_id,
                title=f"Concurrency {suffix}",
                lat=51.5,
                long=-0.1,
                radius=const.MIN_SPOT_RADIUS_METRES,
                claim_duration=0,
                max_claims_per_user=1,
                max_total_claims=max_total_claims,
                total_value=total_value,
                starts_at=int(time.time()) - 60,
                ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                is_prizedraw=is_prizedraw,
                auto_reverse_geocode=False,
            )
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
            if is_prizedraw:
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

            users: list[tuple[int, str]] = []
            for index in range(max_total_claims + 1):
                device_hash = f"{index + 1:064x}"
                user_id = await db_access.create_user(
                    db,
                    device_id_hash=device_hash,
                )
                users.append((user_id, device_hash))
            await db.commit()
        return spot_id, users

    async def _create_ready_prizedraw(
        self,
        *,
        suffix: str,
    ) -> tuple[int, list[int]]:
        spot_id, users = await self._create_published_spot(
            suffix=suffix,
            max_total_claims=4,
            is_prizedraw=True,
            prize_count=2,
        )
        async with schema.get_db() as db:
            claim_ids: list[int] = []
            for user_id, _device_hash in users[:4]:
                claim = await db_access.create_claim_attempt(
                    db,
                    spot_id=spot_id,
                    user_id=user_id,
                    lat=51.5,
                    long=-0.1,
                    location_accuracy_metres=5.0,
                    payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
                )
                claim_ids.append(int(claim[schema.CLAIM_ID]))
            await db.commit()
        return spot_id, claim_ids

    async def test_simultaneous_final_standard_claims_are_serialised(self):
        spot_id, users = await self._create_published_spot(
            suffix="standard",
            max_total_claims=1,
        )
        original_identify = public_html._identify_private_page_user
        arrivals = 0
        both_identified = asyncio.Event()

        async def identify_then_release_together(db, payload):
            nonlocal arrivals
            result = await original_identify(db, payload)
            arrivals += 1
            if arrivals == 2:
                both_identified.set()
            await both_identified.wait()
            return result

        async def submit(device_hash: str):
            payload = public_html.ClaimSpotRequest(
                device_id_hash=device_hash,
                wallet_available=True,
                location_available=True,
                lat=51.5,
                long=-0.1,
                accuracy=5.0,
                payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
            )
            return await public_html.claim_spot_api(
                spot_id,
                payload,
                BackgroundTasks(),
            )

        with (
            mock.patch.object(
                public_html,
                "_identify_private_page_user",
                side_effect=identify_then_release_together,
            ),
            mock.patch.object(
                public_html,
                "_notify_all_cache_for_spot_owner_change",
                mock.AsyncMock(),
            ),
            mock.patch.object(
                public_html,
                "_notify_capacity_cleanup_cache",
                mock.AsyncMock(),
            ),
        ):
            responses = await self._gather_with_deadlock_timeout(
                submit(users[0][1]),
                submit(users[1][1]),
            )

        self.assertEqual(arrivals, 2)
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        rejected = next(response for response in responses if response.status_code == 409)
        rejected_body = json.loads(rejected.body)
        self.assertIn("remaining claim capacity", rejected_body["message"])

        async with schema.get_db() as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT {schema.CLAIM_STATUS}
                FROM {schema.CLAIM_TABLE_NAME}
                WHERE {schema.CLAIM_SPOT_ID} = ?;
                """,
                (spot_id,),
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0][schema.CLAIM_STATUS]), const.CLAIM_STATUS_SUCCESS)

    async def test_simultaneous_prizedraw_settlement_selects_once(self):
        spot_id, claim_ids = await self._create_ready_prizedraw(
            suffix="prizedraw-concurrency",
        )
        sample_calls = 0
        workers_started = 0
        both_workers_started = asyncio.Event()
        payout_retry = mock.AsyncMock(
            return_value={
                "ok": True,
                "spot_id": spot_id,
                "retried": False,
                "payouts": [],
            }
        )

        def choose_winners(population, winner_count):
            nonlocal sample_calls
            sample_calls += 1
            self.assertEqual(winner_count, 2)
            return population[:winner_count]

        async def settle_after_both_workers_start():
            nonlocal workers_started
            workers_started += 1
            if workers_started == 2:
                both_workers_started.set()
            await both_workers_started.wait()
            return await settlement_updater.settle_prizedraw_spot_if_ready(
                spot_id=spot_id
            )

        with (
            mock.patch.object(
                settlement_updater.secrets.SystemRandom,
                "sample",
                side_effect=choose_winners,
            ),
            mock.patch.object(
                settlement_updater,
                "retry_pending_prizedraw_payouts_for_spot",
                payout_retry,
            ),
            mock.patch.object(cache, "notify_spot_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_claim_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_user_changed", mock.AsyncMock()),
        ):
            results = await self._gather_with_deadlock_timeout(
                settle_after_both_workers_start(),
                settle_after_both_workers_start(),
            )

        self.assertEqual(workers_started, 2)
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(sum(bool(result.get("settled")) for result in results), 1)
        self.assertEqual(sample_calls, 1)
        payout_retry.assert_awaited_once_with(spot_id=spot_id)

        skipped = next(result for result in results if not result.get("settled"))
        self.assertEqual(skipped.get("reason"), "not_published")

        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=spot_id)
            winners = await db_access.get_prizedraw_winner_claim_ids(
                db,
                spot_id=spot_id,
            )
            payout_rows = [
                row
                for row in await db_access.get_transactions_by_spot(
                    db,
                    spot_id=spot_id,
                    limit=db_access.MAX_LIMIT,
                )
                if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CLAIM
            ]

        self.assertEqual(int(spot[schema.SPOT_STATUS]), const.SPOT_STATUS_COMPLETED)
        self.assertEqual(len(winners), 2)
        self.assertTrue(set(winners).issubset(set(claim_ids)))
        self.assertEqual(payout_rows, [])

    async def test_settled_prizedraw_creates_one_payout_per_winner(self):
        spot_id, claim_ids = await self._create_ready_prizedraw(
            suffix="prizedraw-payouts",
        )
        sends: list[tuple[str, int]] = []

        def choose_winners(population, winner_count):
            self.assertEqual(winner_count, 2)
            return population[:winner_count]

        async def fake_send(*, spot, to_address, amount, memo=None):
            sends.append((str(to_address), int(amount)))
            return trans_updater.SubmittedChainTransaction(
                tx_hash=f"{len(sends):064x}",
                from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                to_address=to_address,
                amount=int(amount),
            )

        with (
            mock.patch.object(
                settlement_updater.secrets.SystemRandom,
                "sample",
                side_effect=choose_winners,
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=fake_send,
            ),
            mock.patch.object(cache, "notify_spot_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_claim_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_user_changed", mock.AsyncMock()),
            mock.patch.object(cache, "notify_transaction_changed", mock.AsyncMock()),
        ):
            result = await settlement_updater.settle_prizedraw_spot_if_ready(
                spot_id=spot_id
            )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["settled"], result)
        self.assertTrue(result["payout_retry"]["ok"], result)
        self.assertEqual(len(sends), 2)

        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=spot_id)
            winners = await db_access.get_prizedraw_winner_claim_ids(
                db,
                spot_id=spot_id,
            )
            payout_rows = [
                row
                for row in await db_access.get_transactions_by_spot(
                    db,
                    spot_id=spot_id,
                    limit=db_access.MAX_LIMIT,
                )
                if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CLAIM
            ]

        self.assertEqual(int(spot[schema.SPOT_STATUS]), const.SPOT_STATUS_COMPLETED)
        self.assertEqual(len(winners), 2)
        self.assertTrue(set(winners).issubset(set(claim_ids)))
        self.assertEqual(len(payout_rows), 2)
        self.assertEqual(
            {int(row[schema.TRANS_CLAIM_ID]) for row in payout_rows},
            set(winners),
        )
        self.assertEqual(
            sum(int(row[schema.TRANS_AMOUNT]) for row in payout_rows),
            int(spot[schema.SPOT_TOTAL_VALUE]),
        )
        self.assertEqual(
            sorted(amount for _address, amount in sends),
            sorted(int(row[schema.TRANS_AMOUNT]) for row in payout_rows),
        )


class TransactionHealthEndpointTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _diagnostics(*, local_intents: int = 0, refresher_running: bool = True):
        return {
            "local_intent_count": local_intents,
            "refresher": {
                "running": refresher_running,
                "healthy": True,
            },
            "settlement": {
                "running": True,
                "healthy": True,
            },
        }

    async def test_transaction_health_is_green_only_when_financial_workers_are_healthy(self):
        with mock.patch.object(
            main,
            "funding_flow_diagnostics",
            mock.AsyncMock(return_value=self._diagnostics()),
        ):
            response = await main.transaction_healthz()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.body)["ok"])

    async def test_transaction_health_flags_unresolved_local_outbox_intent(self):
        with mock.patch.object(
            main,
            "funding_flow_diagnostics",
            mock.AsyncMock(return_value=self._diagnostics(local_intents=1)),
        ):
            response = await main.transaction_healthz()

        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.body)["ok"])
