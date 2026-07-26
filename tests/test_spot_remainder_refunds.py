from __future__ import annotations

import asyncio
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

FUNDING_ADDRESS = const.DEV_PLATFORM_FEE_ADDRESS


class SpotRemainderRefundIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self._hash_counter = 1

        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash=f"remainder-owner-{id(self)}",
            )
            await db.commit()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()

    def _tx_hash(self) -> str:
        value = f"{self._hash_counter:064x}"
        self._hash_counter += 1
        return value

    async def _create_spot(
        self,
        *,
        total_value: int,
        max_total_claims: int,
        claim_duration: int = 0,
        status: int = const.SPOT_STATUS_PUBLISHED,
        is_prizedraw: bool = False,
        prize_count: int = 1,
        expired: bool = True,
    ) -> int:
        starts_at = int(time.time())
        if expired:
            starts_at -= const.MIN_SPOT_ENDS_AFTER_SECONDS + 60
        else:
            starts_at -= 60

        async with schema.get_db() as db:
            if is_prizedraw:
                spot_id = await db_access.create_prizedraw(
                    db,
                    created_by=self.owner_id,
                    title="Remainder Prizedraw",
                    desc="Prizedraw remainder test.",
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=claim_duration,
                    max_claims_per_user=1,
                    max_total_claims=max_total_claims,
                    total_value=total_value,
                    prize_count=prize_count,
                    starts_at=starts_at,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
            else:
                spot_id = await db_access.create_spot(
                    db,
                    created_by=self.owner_id,
                    title="Remainder Standard",
                    desc="Standard remainder test.",
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=claim_duration,
                    max_claims_per_user=1,
                    max_total_claims=max_total_claims,
                    total_value=total_value,
                    starts_at=starts_at,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
            await db.execute(
                f"""
                UPDATE {schema.SPOT_TABLE_NAME}
                SET {schema.SPOT_DEPOSIT_ADDRESS} = ?,
                    {schema.SPOT_STATUS} = ?
                WHERE {schema.SPOT_ID} = ?;
                """,
                (FUNDING_ADDRESS, int(status), int(spot_id)),
            )
            await db.commit()
        return int(spot_id)

    async def _spot(self, spot_id: int):
        async with schema.get_db() as db:
            return await db_access.get_spot(db, spot_id=spot_id)

    async def _insert_transaction(
        self,
        *,
        spot_id: int,
        trans_type: int,
        amount: int,
        status: int = const.TRANS_STATUS_CONFIRMED,
        claim_id: int | None = None,
        user_id: int | None = None,
        from_address: str = FUNDING_ADDRESS,
        to_address: str = FUNDING_ADDRESS,
    ) -> int:
        async with schema.get_db() as db:
            trans_id = await db_access._create_transaction(
                db,
                user_id=int(self.owner_id if user_id is None else user_id),
                spot_id=int(spot_id),
                claim_id=claim_id,
                trans_type=int(trans_type),
                amount=int(amount),
                from_address=from_address,
                to_address=to_address,
                tx_hash=self._tx_hash(),
            )
            if status == const.TRANS_STATUS_CONFIRMED:
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=trans_id,
                    block_number=100 + trans_id,
                )
            elif status == const.TRANS_STATUS_FAILED:
                await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
            await db.commit()
            return int(trans_id)

    async def _fund_and_pay_fee(self, spot_id: int, *, deposit_amount: int) -> int:
        spot = await self._spot(spot_id)
        fee = int(db_access.spot_creation_fee_amount(spot))
        await self._insert_transaction(
            spot_id=spot_id,
            trans_type=const.TRANS_TYPE_FILL_SPOT,
            amount=deposit_amount,
            from_address=FUNDING_ADDRESS,
            to_address=FUNDING_ADDRESS,
        )
        if fee > 0:
            await self._insert_transaction(
                spot_id=spot_id,
                trans_type=const.TRANS_TYPE_CREATION_FEE,
                amount=fee,
            )
        return fee

    async def _create_claim(self, spot_id: int, *, status: int) -> tuple[int, int]:
        async with schema.get_db() as db:
            user_id = await db_access.create_user(
                db,
                device_id_hash=f"claimant-{spot_id}-{time.time_ns()}",
            )
            claim_id = await db_access.create_claim(
                db,
                spot_id=spot_id,
                user_id=user_id,
                lat=51.5,
                long=-0.1,
                accuracy=1.0,
                payout_address=FUNDING_ADDRESS,
            )
            if status == const.CLAIM_STATUS_SUCCESS:
                await db_access.set_claim_status_to_success(db, claim_id=claim_id)
            elif status == const.CLAIM_STATUS_FAILED:
                await db_access.set_claim_status_to_failed(db, claim_id=claim_id)
            await db.commit()
            return int(claim_id), int(user_id)

    def _fake_send(self, sends: list[tuple[str, int]]):
        async def fake_send(*, spot, to_address, amount, memo=None):
            sends.append((to_address, int(amount)))
            return trans_updater.SubmittedChainTransaction(
                tx_hash=self._tx_hash(),
                from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                to_address=to_address,
                amount=int(amount),
            )

        return fake_send

    async def test_expired_standard_refunds_unclaimed_rewards_and_overfunding(self):
        total_value = max(
            const.MIN_SPOT_TOTAL_VALUE,
            3 * const.MIN_STANDARD_CLAIM_PAYOUT,
        )
        overfunding = 777
        spot_id = await self._create_spot(
            total_value=total_value,
            max_total_claims=3,
        )
        spot = await self._spot(spot_id)
        fee = int(db_access.spot_creation_fee_amount(spot))
        await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=total_value + fee + overfunding,
        )
        claim_id, claimant_id = await self._create_claim(
            spot_id,
            status=const.CLAIM_STATUS_SUCCESS,
        )
        claim_amount = total_value // 3
        await self._insert_transaction(
            spot_id=spot_id,
            trans_type=const.TRANS_TYPE_CLAIM,
            amount=claim_amount,
            claim_id=claim_id,
            user_id=claimant_id,
        )

        sends: list[tuple[str, int]] = []
        resolve = mock.AsyncMock(return_value=FUNDING_ADDRESS)
        with (
            mock.patch.object(
                trans_updater,
                "resolve_nimiq_pay_payout_address",
                resolve,
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=self._fake_send(sends),
            ),
        ):
            result = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )

        expected = total_value + fee + overfunding - fee - claim_amount
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["refunded"], result)
        self.assertEqual(result["remainder_amount"], expected)
        self.assertEqual(sends, [(FUNDING_ADDRESS, expected)])
        resolve.assert_awaited_once()
        self.assertTrue(resolve.await_args.kwargs["source_tx_hash"])

        async with schema.get_db() as db:
            spot_after = await db_access.get_spot(db, spot_id=spot_id)
            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )
        refunds = [
            row
            for row in transactions
            if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_REMAINDER_REFUND
        ]
        self.assertEqual(int(spot_after[schema.SPOT_STATUS]), const.SPOT_STATUS_COMPLETED)
        self.assertEqual(len(refunds), 1)
        self.assertEqual(int(refunds[0][schema.TRANS_AMOUNT]), expected)
        self.assertEqual(int(refunds[0][schema.TRANS_STATUS]), const.TRANS_STATUS_PENDING)

    async def test_pending_duration_claim_survives_public_end_and_blocks_refund(self):
        spot_id = await self._create_spot(
            total_value=const.MIN_SPOT_TOTAL_VALUE,
            max_total_claims=1,
            claim_duration=600,
        )
        spot = await self._spot(spot_id)
        fee = await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=int(spot[schema.SPOT_TOTAL_VALUE]) + int(spot[schema.SPOT_CREATION_FEE]),
        )
        self.assertGreaterEqual(fee, 0)
        claim_id, _ = await self._create_claim(
            spot_id,
            status=const.CLAIM_STATUS_PENDING,
        )

        send = mock.AsyncMock()
        with mock.patch.object(
            trans_updater,
            "submit_spot_remainder_refund_transaction",
            send,
        ):
            result = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["deferred"])
        self.assertEqual(result["reason"], "pending_claims")
        send.assert_not_awaited()
        async with schema.get_db() as db:
            spot_after = await db_access.get_spot(db, spot_id=spot_id)
            claim_after = await db_access.get_claim(db, claim_id=claim_id)
        self.assertEqual(int(spot_after[schema.SPOT_STATUS]), const.SPOT_STATUS_PUBLISHED)
        self.assertEqual(int(claim_after[schema.CLAIM_STATUS]), const.CLAIM_STATUS_PENDING)

    async def test_successful_standard_claim_must_be_paid_before_remainder(self):
        spot_id = await self._create_spot(
            total_value=max(
                const.MIN_SPOT_TOTAL_VALUE,
                2 * const.MIN_STANDARD_CLAIM_PAYOUT,
            ),
            max_total_claims=2,
        )
        spot = await self._spot(spot_id)
        await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=int(spot[schema.SPOT_TOTAL_VALUE]) + int(spot[schema.SPOT_CREATION_FEE]),
        )
        await self._create_claim(spot_id, status=const.CLAIM_STATUS_SUCCESS)

        result = await settlement_updater.settle_spot_remainder_if_ready(
            spot_id=spot_id,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["deferred"])
        self.assertEqual(result["reason"], "claim_payouts_unconfirmed")
        self.assertEqual(result["unconfirmed_claim_payout_count"], 1)
        self.assertEqual(
            int((await self._spot(spot_id))[schema.SPOT_STATUS]),
            const.SPOT_STATUS_PUBLISHED,
        )

    async def test_pending_refund_is_idempotent_and_failed_refund_retries_exact_amount(self):
        total_value = const.MIN_SPOT_TOTAL_VALUE
        spot_id = await self._create_spot(
            total_value=total_value,
            max_total_claims=1,
        )
        spot = await self._spot(spot_id)
        fee = await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=total_value + int(spot[schema.SPOT_CREATION_FEE]),
        )
        self.assertEqual(fee, int(spot[schema.SPOT_CREATION_FEE]))
        sends: list[tuple[str, int]] = []

        with (
            mock.patch.object(
                trans_updater,
                "resolve_nimiq_pay_payout_address",
                mock.AsyncMock(return_value=FUNDING_ADDRESS),
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=self._fake_send(sends),
            ),
        ):
            first = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )
            second = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )

        self.assertTrue(first["refunded"])
        self.assertTrue(second["deferred"])
        self.assertEqual(second["reason"], "financial_transaction_pending")
        self.assertEqual(sends, [(FUNDING_ADDRESS, total_value)])

        async with schema.get_db() as db:
            rows = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )
            pending = next(
                row
                for row in rows
                if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_REMAINDER_REFUND
                and int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_PENDING
            )
            await db_access.set_transaction_status_to_failed(
                db,
                trans_id=int(pending[schema.TRANS_ID]),
            )
            await db.commit()

        with (
            mock.patch.object(
                trans_updater,
                "resolve_nimiq_pay_payout_address",
                mock.AsyncMock(return_value=FUNDING_ADDRESS),
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=self._fake_send(sends),
            ),
        ):
            retry = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )

        self.assertTrue(retry["refunded"], retry)
        self.assertEqual(retry["remainder_amount"], total_value)
        self.assertEqual(sends, [(FUNDING_ADDRESS, total_value), (FUNDING_ADDRESS, total_value)])

    async def test_confirmed_refund_marks_remainder_settled_and_leaves_queue(self):
        total_value = const.MIN_SPOT_TOTAL_VALUE
        spot_id = await self._create_spot(
            total_value=total_value,
            max_total_claims=1,
        )
        spot = await self._spot(spot_id)
        await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=total_value + int(spot[schema.SPOT_CREATION_FEE]),
        )
        await self._insert_transaction(
            spot_id=spot_id,
            trans_type=const.TRANS_TYPE_REMAINDER_REFUND,
            amount=total_value,
        )

        result = await settlement_updater.settle_spot_remainder_if_ready(
            spot_id=spot_id,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "no_remainder")

        async with schema.get_db() as db:
            self.assertTrue(
                await db_access.is_spot_remainder_settled(db, spot_id=spot_id)
            )
            queued = await db_access.get_spot_ids_ready_for_remainder_refund(db)
        self.assertNotIn(spot_id, queued)

    async def test_completed_prizedraw_refunds_only_overfunding_after_prizes(self):
        total_value = 2 * const.MIN_PRIZEDRAW_PRIZE_PAYOUT
        overfunding = 456
        spot_id = await self._create_spot(
            total_value=total_value,
            max_total_claims=4,
            status=const.SPOT_STATUS_COMPLETED,
            is_prizedraw=True,
            prize_count=2,
        )
        spot = await self._spot(spot_id)
        await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=total_value + int(spot[schema.SPOT_CREATION_FEE]) + overfunding,
        )
        for amount in (total_value // 2, total_value // 2):
            claim_id, claimant_id = await self._create_claim(
                spot_id,
                status=const.CLAIM_STATUS_SUCCESS,
            )
            await self._insert_transaction(
                spot_id=spot_id,
                trans_type=const.TRANS_TYPE_CLAIM,
                amount=amount,
                claim_id=claim_id,
                user_id=claimant_id,
            )

        sends: list[tuple[str, int]] = []
        with (
            mock.patch.object(
                trans_updater,
                "resolve_nimiq_pay_payout_address",
                mock.AsyncMock(return_value=FUNDING_ADDRESS),
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=self._fake_send(sends),
            ),
        ):
            result = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )

        self.assertTrue(result["refunded"], result)
        self.assertEqual(result["remainder_amount"], overfunding)
        self.assertEqual(sends, [(FUNDING_ADDRESS, overfunding)])

    async def test_empty_prizedraw_returns_entire_prize_pool(self):
        total_value = const.MIN_PRIZEDRAW_PRIZE_PAYOUT
        spot_id = await self._create_spot(
            total_value=total_value,
            max_total_claims=4,
            status=const.SPOT_STATUS_COMPLETED,
            is_prizedraw=True,
            prize_count=1,
        )
        spot = await self._spot(spot_id)
        await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=total_value + int(spot[schema.SPOT_CREATION_FEE]),
        )

        sends: list[tuple[str, int]] = []
        with (
            mock.patch.object(
                trans_updater,
                "resolve_nimiq_pay_payout_address",
                mock.AsyncMock(return_value=FUNDING_ADDRESS),
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=self._fake_send(sends),
            ),
        ):
            result = await settlement_updater.settle_spot_remainder_if_ready(
                spot_id=spot_id,
            )

        self.assertTrue(result["refunded"], result)
        self.assertEqual(result["remainder_amount"], total_value)
        self.assertEqual(sends, [(FUNDING_ADDRESS, total_value)])

    async def test_concurrent_remainder_workers_create_only_one_active_refund(self):
        total_value = const.MIN_SPOT_TOTAL_VALUE
        spot_id = await self._create_spot(
            total_value=total_value,
            max_total_claims=1,
        )
        spot = await self._spot(spot_id)
        await self._fund_and_pay_fee(
            spot_id,
            deposit_amount=total_value + int(spot[schema.SPOT_CREATION_FEE]),
        )
        sends: list[tuple[str, int]] = []

        async def slow_resolve(address, *, source_tx_hash=None, **kwargs):
            await asyncio.sleep(0.03)
            return address

        with (
            mock.patch.object(
                trans_updater,
                "resolve_nimiq_pay_payout_address",
                side_effect=slow_resolve,
            ),
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                side_effect=self._fake_send(sends),
            ),
        ):
            results = await asyncio.gather(
                settlement_updater.settle_spot_remainder_if_ready(spot_id=spot_id),
                settlement_updater.settle_spot_remainder_if_ready(spot_id=spot_id),
            )

        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(sum(bool(result.get("refunded")) for result in results), 1)
        self.assertEqual(len(sends), 1)
        async with schema.get_db() as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT *
                FROM {schema.TRANS_TABLE_NAME}
                WHERE {schema.TRANS_SPOT_ID} = ?
                  AND {schema.TRANS_TYPE} = ?
                  AND {schema.TRANS_STATUS} != ?;
                """,
                (
                    spot_id,
                    const.TRANS_TYPE_REMAINDER_REFUND,
                    const.TRANS_STATUS_FAILED,
                ),
            )
        self.assertEqual(len(rows), 1)


class DurationCapacitySemanticsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def test_pending_duration_claims_do_not_reserve_final_capacity(self):
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="duration-owner")
            spot_id = await db_access.create_spot(
                db,
                created_by=owner_id,
                title="Duration Capacity",
                lat=51.5,
                long=-0.1,
                radius=100,
                claim_duration=600,
                max_claims_per_user=1,
                max_total_claims=1,
                total_value=const.MIN_SPOT_TOTAL_VALUE,
                starts_at=int(time.time()) - 60,
                ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                auto_reverse_geocode=False,
            )
            await db.execute(
                f"UPDATE {schema.SPOT_TABLE_NAME} SET {schema.SPOT_STATUS} = ? WHERE {schema.SPOT_ID} = ?;",
                (const.SPOT_STATUS_PUBLISHED, spot_id),
            )
            claim_ids = []
            for index in range(2):
                user_id = await db_access.create_user(
                    db,
                    device_id_hash=f"duration-user-{index}",
                )
                claim_ids.append(
                    await db_access.create_claim(
                        db,
                        spot_id=spot_id,
                        user_id=user_id,
                        lat=51.5,
                        long=-0.1,
                        accuracy=1.0,
                        payout_address=FUNDING_ADDRESS,
                    )
                )
            self.assertTrue(
                await db_access.is_spot_claim_capacity_available(db, spot_id=spot_id)
            )
            first = await db_access.promote_pending_claim_to_success_if_capacity_available(
                db,
                claim_id=claim_ids[0],
            )
            second = await db_access.get_claim(db, claim_id=claim_ids[1])
            await db.commit()

        self.assertEqual(int(first[schema.CLAIM_STATUS]), const.CLAIM_STATUS_SUCCESS)
        self.assertEqual(int(second[schema.CLAIM_STATUS]), const.CLAIM_STATUS_FAILED)
        self.assertEqual(first["capacity_cleanup"]["failed_claim_ids"], [claim_ids[1]])
