import tempfile
import time
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import funding_fee_worker
import trans_updater


class CompletedSpotCreationFeeRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash=f"completed-fee-owner-{id(self)}",
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def _create_funded_completed_spot(self) -> int:
        fee = 2 * const.LUNA_PER_NIM
        with (
            mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", fee),
            mock.patch.object(
                const,
                "SPOT_FEE_ADDRESS",
                const.DEV_PLATFORM_FEE_ADDRESS,
            ),
        ):
            async with schema.get_db() as db:
                spot_id = await db_access.create_spot(
                    db,
                    created_by=self.owner_id,
                    title="Completed Fee Recovery",
                    desc="A one-claim Spot that finishes before fee recovery runs.",
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=0,
                    max_claims_per_user=1,
                    max_total_claims=1,
                    total_value=const.MIN_SPOT_TOTAL_VALUE,
                    starts_at=int(time.time()) + 3600,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
                spot = await db_access.get_spot(db, spot_id=spot_id)
                required = db_access.spot_required_deposit_amount(spot)
                deposit_id = await db_access.create_spot_deposit_transaction(
                    db,
                    user_id=self.owner_id,
                    spot_id=spot_id,
                    amount=required,
                    from_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                    to_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                    tx_hash=f"completed-fee-deposit-{time.time_ns()}",
                )
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=deposit_id,
                    block_number=123,
                )
                await db_access.publish_spot(db, spot_id=spot_id)
                await db.execute(
                    f"UPDATE {schema.SPOT_TABLE_NAME} "
                    f"SET {schema.SPOT_STATUS} = ? "
                    f"WHERE {schema.SPOT_ID} = ?;",
                    (const.SPOT_STATUS_COMPLETED, spot_id),
                )
                await db.commit()
                return spot_id

    async def test_completed_spot_remains_eligible_for_missing_creation_fee(self):
        spot_id = await self._create_funded_completed_spot()

        async with schema.get_db() as db:
            eligible = await funding_fee_worker.get_spot_ids_ready_for_creation_fee(db)

        self.assertIn(spot_id, eligible)

    async def test_completed_spot_can_create_and_submit_missing_creation_fee(self):
        spot_id = await self._create_funded_completed_spot()

        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=spot_id)
            fee = int(spot[schema.SPOT_CREATION_FEE])

            with mock.patch.object(
                trans_updater,
                "_submit_recorded_chain_send",
                mock.AsyncMock(return_value={"ok": True, "trans_id": 999}),
            ) as send:
                result = await funding_fee_worker.submit_spot_creation_fee_transaction(
                    db,
                    spot_id=spot_id,
                )

            self.assertTrue(result["ok"])
            send.assert_awaited_once()
            self.assertEqual(send.await_args.kwargs["amount"], fee)
            self.assertEqual(
                send.await_args.kwargs["to_address"],
                spot[schema.SPOT_CREATION_FEE_ADDRESS],
            )

            trans_id = await funding_fee_worker.create_spot_creation_fee_transaction(
                db,
                user_id=self.owner_id,
                spot_id=spot_id,
                amount=fee,
                from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
                tx_hash=f"completed-fee-{time.time_ns()}",
            )
            await db.commit()

            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )

        fee_rows = [
            row
            for row in transactions
            if int(row[schema.TRANS_ID]) == int(trans_id)
            and int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CREATION_FEE
        ]
        self.assertEqual(len(fee_rows), 1)
        self.assertEqual(fee_rows[0][schema.TRANS_AMOUNT], fee)
        self.assertEqual(
            fee_rows[0][schema.TRANS_TO_ADDRESS],
            spot[schema.SPOT_CREATION_FEE_ADDRESS],
        )


if __name__ == "__main__":
    unittest.main()
