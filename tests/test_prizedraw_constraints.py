import tempfile
import unittest

import constants as const
import database as schema
import db_access


class PrizedrawConstraintDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()
        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(db, device_id_hash="prizedraw-owner")
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def test_finite_prizedraw_requires_at_least_two_participants(self):
        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "at least 2"):
                await db_access.create_prizedraw(
                    db,
                    created_by=self.owner_id,
                    title="One participant",
                    max_total_claims=1,
                    max_claims_per_user=0,
                    prize_count=1,
                )

    async def test_finite_prizedraw_limits_are_strictly_below_total(self):
        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "max_claims_per_user must be less"):
                await db_access.create_prizedraw(
                    db,
                    created_by=self.owner_id,
                    title="Equal user limit",
                    max_total_claims=2,
                    max_claims_per_user=2,
                    prize_count=1,
                )

            with self.assertRaisesRegex(ValueError, "prize_count must be less"):
                await db_access.create_prizedraw(
                    db,
                    created_by=self.owner_id,
                    title="Equal prize count",
                    max_total_claims=2,
                    max_claims_per_user=1,
                    prize_count=2,
                )

    async def test_valid_finite_and_unlimited_prizedraws_are_accepted(self):
        async with schema.get_db() as db:
            finite_id = await db_access.create_prizedraw(
                db,
                created_by=self.owner_id,
                title="Finite",
                max_total_claims=2,
                max_claims_per_user=1,
                prize_count=1,
            )
            unlimited_id = await db_access.create_prizedraw(
                db,
                created_by=self.owner_id,
                title="Unlimited",
                max_total_claims=0,
                max_claims_per_user=const.MAX_SPOT_MAX_CLAIMS_PER_USER,
                prize_count=const.MAX_PRIZEDRAW_PRIZE_COUNT,
            )
            await db.commit()

            self.assertGreater(finite_id, 0)
            self.assertGreater(unlimited_id, finite_id)

    async def test_draft_update_validates_spot_and_prizedraw_fields_together(self):
        async with schema.get_db() as db:
            spot_id = await db_access.create_prizedraw(
                db,
                created_by=self.owner_id,
                title="Editable",
            )
            await db_access.modify_draft_spot(
                db,
                spot_id=spot_id,
                max_claims_per_user=4,
                max_total_claims=5,
                prize_count=4,
            )
            await db.commit()

            spot = await db_access.get_spot(db, spot_id=spot_id)
            prizedraw = await db_access.get_prizedraw(db, spot_id=spot_id)
            self.assertEqual(spot[schema.SPOT_MAX_CLAIMS_PER_USER], 4)
            self.assertEqual(spot[schema.SPOT_MAX_TOTAL_CLAIMS], 5)
            self.assertEqual(prizedraw[schema.PRIZEDRAW_PRIZE_COUNT], 4)

            with self.assertRaisesRegex(ValueError, "must be less"):
                await db_access.modify_draft_spot(
                    db,
                    spot_id=spot_id,
                    max_total_claims=4,
                )
