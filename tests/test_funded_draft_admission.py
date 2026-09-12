from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import draft_creation
import funding_fee_worker


class FundedDraftAdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await schema.init_db()

        async with schema.get_db() as db:
            self.user_id = await db_access.create_user(
                db,
                device_id_hash="a" * 64,
            )
            self.other_user_id = await db_access.create_user(
                db,
                device_id_hash="b" * 64,
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def _create_admitted_spot(self, *, user_id: int) -> int:
        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                reservation = await db_access.reserve_draft_creation(
                    db,
                    user_id=int(user_id),
                )
        self.assertIsNotNone(reservation)

        deposit_record = await draft_creation.derive_admitted_deposit(
            reservation_id=reservation["id"],
            user_id=int(user_id),
            key_index=reservation["deposit_key_index"],
        )

        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                return await db_access.create_spot(
                    db,
                    created_by=int(user_id),
                    title="Admission Test Spot",
                    deposit_record=deposit_record,
                )

    async def _record_deposit(
        self,
        *,
        spot_id: int,
        user_id: int,
        amount: int,
        confirmed: bool,
        suffix: str,
    ) -> int:
        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=int(spot_id))
            trans_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=int(user_id),
                spot_id=int(spot_id),
                amount=int(amount),
                from_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                to_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                tx_hash=f"funded-admission-{suffix}-{spot_id}",
            )
            if confirmed:
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=trans_id,
                    block_number=123,
                )
            await db.commit()
            return trans_id

    async def _required_deposit(self, spot_id: int) -> int:
        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=int(spot_id))
            return db_access.spot_required_deposit_amount(spot)

    async def test_only_full_confirmed_funding_releases_user_capacity(self):
        with (
            mock.patch.object(const, "DRAFT_CREATION_LIMIT_PER_USER", 1),
            mock.patch.object(const, "DRAFT_CREATION_GLOBAL_LIMIT", 100),
        ):
            spot_id = await self._create_admitted_spot(user_id=self.user_id)
            required = await self._required_deposit(spot_id)

            # A full but merely pending wallet submission is not enough. The
            # anti-spam admission remains charged until the chain confirms it.
            pending_id = await self._record_deposit(
                spot_id=spot_id,
                user_id=self.user_id,
                amount=required,
                confirmed=False,
                suffix="pending",
            )
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    blocked = await funding_fee_worker.reserve_draft_creation(
                        db,
                        user_id=self.user_id,
                    )
            self.assertIsNone(blocked)

            # Once that exact full deposit confirms, the paid-for Spot no
            # longer consumes the rolling creation allowance.
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    await db_access.set_transaction_status_to_confirmed(
                        db,
                        trans_id=pending_id,
                        block_number=124,
                    )
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    replacement = await funding_fee_worker.reserve_draft_creation(
                        db,
                        user_id=self.user_id,
                    )
            self.assertIsNotNone(replacement)

    async def test_partial_confirmed_funding_remains_counted(self):
        with (
            mock.patch.object(const, "DRAFT_CREATION_LIMIT_PER_USER", 1),
            mock.patch.object(const, "DRAFT_CREATION_GLOBAL_LIMIT", 100),
        ):
            spot_id = await self._create_admitted_spot(user_id=self.user_id)
            required = await self._required_deposit(spot_id)
            await self._record_deposit(
                spot_id=spot_id,
                user_id=self.user_id,
                amount=required - 1,
                confirmed=True,
                suffix="partial",
            )

            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    blocked = await funding_fee_worker.reserve_draft_creation(
                        db,
                        user_id=self.user_id,
                    )
            self.assertIsNone(blocked)

    async def test_fully_funded_spot_also_releases_global_capacity(self):
        with (
            mock.patch.object(const, "DRAFT_CREATION_LIMIT_PER_USER", 10),
            mock.patch.object(const, "DRAFT_CREATION_GLOBAL_LIMIT", 1),
        ):
            spot_id = await self._create_admitted_spot(user_id=self.user_id)
            required = await self._required_deposit(spot_id)
            await self._record_deposit(
                spot_id=spot_id,
                user_id=self.user_id,
                amount=required,
                confirmed=True,
                suffix="global",
            )

            # Cleanup is deliberately global, otherwise funded admissions from
            # prolific users could still exhaust the shared 200/hour ceiling.
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    reservation = await funding_fee_worker.reserve_draft_creation(
                        db,
                        user_id=self.other_user_id,
                    )
            self.assertIsNotNone(reservation)


if __name__ == "__main__":
    unittest.main()
