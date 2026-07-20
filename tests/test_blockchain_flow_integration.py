from __future__ import annotations

import tempfile
from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import database as schema
import db_access
import trans_updater


HASH_1 = "11" * 32
HASH_2 = "22" * 32
HASH_3 = "33" * 32
FUNDING_ADDRESS = const.DEV_PLATFORM_FEE_ADDRESS


class BlockchainFlowIntegrationTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def create_owner_spot(self):
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="owner-blockchain")
            spot_id = await db_access.create_spot(db, created_by=owner_id, title="Chain Spot")
            await db.commit()
            spot = await db_access.get_spot(db, spot_id=spot_id)
        return owner_id, spot_id, spot

    async def record_confirmed_deposit(self, *, owner_id, spot_id, spot, tx_hash=HASH_1):
        required = db_access.spot_required_deposit_amount(spot)
        async with schema.get_db() as db:
            record = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=required,
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                tx_hash=tx_hash,
            )
            await db.commit()
            row = await db_access.get_transaction(db, trans_id=record["trans_id"])
            verified = trans_updater.VerifiedChainDetails(
                ok=True,
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                amount=required,
            )
            await trans_updater.mark_trans_as_confirmed(
                db,
                row,
                block_number=123,
                verified_details=verified,
            )
        return record, required

    async def test_deposit_recording_is_idempotent_and_chain_confirmation_updates_funding(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        first, required = await self.record_confirmed_deposit(
            owner_id=owner_id, spot_id=spot_id, spot=spot
        )
        async with schema.get_db() as db:
            repeated = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=required,
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                tx_hash=HASH_1.upper(),
            )
            total = await db_access.get_confirmed_spot_deposit_total(db, spot_id=spot_id)
            transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=20)

        self.assertTrue(repeated["already_recorded"])
        self.assertEqual(repeated["trans_id"], first["trans_id"])
        self.assertEqual(total, required)
        fills = [row for row in transactions if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_FILL_SPOT]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0][schema.TRANS_FROM_ADDRESS], FUNDING_ADDRESS)

    async def test_same_hash_cannot_be_attached_to_another_spot(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        await self.record_confirmed_deposit(owner_id=owner_id, spot_id=spot_id, spot=spot)
        async with schema.get_db() as db:
            other_id = await db_access.create_spot(db, created_by=owner_id, title="Other Chain Spot")
            other = await db_access.get_spot(db, spot_id=other_id)
            with self.assertRaisesRegex(ValueError, "different record"):
                await trans_updater.record_spot_deposit_transaction(
                    db,
                    user_id=owner_id,
                    spot_id=other_id,
                    amount=db_access.spot_required_deposit_amount(other),
                    from_address=FUNDING_ADDRESS,
                    to_address=other[schema.SPOT_DEPOSIT_ADDRESS],
                    tx_hash=HASH_1,
                )

    async def test_stale_unseen_user_deposit_is_released_only_after_address_history_proves_absence(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        async with schema.get_db() as db:
            record = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=db_access.spot_required_deposit_amount(spot),
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                tx_hash=HASH_1,
            )
            await db.execute(
                f"UPDATE {schema.TRANS_TABLE_NAME} SET {schema.TRANS_CREATED_AT} = unixepoch() - 3600 WHERE {schema.TRANS_ID} = ?;",
                (record["trans_id"],),
            )
            await db.commit()
            row = await db_access.get_transaction(db, trans_id=record["trans_id"])

        pending = trans_updater.ChainTransactionStatus(
            status="pending", tx_hash=HASH_1, reason="hash not found yet"
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=[]),
            ),
        ):
            result = await trans_updater.check_pending_transaction(
                row,
                user_deposit_stale_after_seconds=1,
            )
        self.assertEqual(result.status, "failed")
        self.assertIn("validity window", result.reason)

    async def test_claim_payout_is_broadcast_once_and_reuses_the_database_guard(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        async with schema.get_db() as db:
            claimant_id = await db_access.create_user(db, device_id_hash="claimant-blockchain")
            claim_id = await db_access.create_claim(
                db,
                spot_id=spot_id,
                user_id=claimant_id,
                lat=1.0,
                long=2.0,
                accuracy=1.0,
                payout_address=FUNDING_ADDRESS,
            )
            await db.commit()

            submitted = trans_updater.SubmittedChainTransaction(
                tx_hash=HASH_2,
                from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                to_address=FUNDING_ADDRESS,
                amount=100_000,
            )
            with (
                mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
                mock.patch.object(
                    trans_updater,
                    "submit_chain_send_from_spot_deposit",
                    mock.AsyncMock(return_value=submitted),
                ) as send,
            ):
                first = await trans_updater.submit_claim_reward_transaction(
                    db, claim_id=claim_id, amount=100_000
                )
                second = await trans_updater.submit_claim_reward_transaction(
                    db, claim_id=claim_id, amount=100_000
                )

            transactions = await db_access.get_transactions_by_claim(db, claim_id=claim_id)

        self.assertFalse(first["already_exists"])
        self.assertTrue(second["already_exists"])
        send.assert_awaited_once()
        payouts = [row for row in transactions if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CLAIM]
        self.assertEqual(len(payouts), 1)
        self.assertEqual(payouts[0][schema.TRANS_TX_HASH], HASH_2)

    async def test_repeated_cancellation_does_not_broadcast_refund_or_fee_twice(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        await self.record_confirmed_deposit(owner_id=owner_id, spot_id=spot_id, spot=spot)
        counter = 0

        async def fake_send(*, spot, to_address, amount, memo=None):
            nonlocal counter
            counter += 1
            return trans_updater.SubmittedChainTransaction(
                tx_hash=(HASH_2 if counter == 1 else HASH_3),
                from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                to_address=to_address,
                amount=amount,
            )

        async with schema.get_db() as db:
            with (
                mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
                mock.patch.object(
                    trans_updater,
                    "submit_chain_send_from_spot_deposit",
                    side_effect=fake_send,
                ) as send,
            ):
                first = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=const.SPOT_CANCELLATION_FEE,
                    fee_address=FUNDING_ADDRESS,
                )
                second = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=const.SPOT_CANCELLATION_FEE,
                    fee_address=FUNDING_ADDRESS,
                )
            transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)

        self.assertTrue(first["cancellation_pending"])
        self.assertTrue(second["cancellation_pending"])
        self.assertLessEqual(send.await_count, 2)
        outgoing = [
            row for row in transactions
            if int(row[schema.TRANS_TYPE]) in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
        ]
        self.assertLessEqual(len(outgoing), 2)
