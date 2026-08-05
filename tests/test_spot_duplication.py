from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
from spot_duplicate import (
    DuplicateSpotError,
    duplicate_owned_spot_as_draft,
    duplicate_spot_configuration,
)


class SpotDuplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()
        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash="a" * 64,
            )
            self.other_id = await db_access.create_user(
                db,
                device_id_hash="b" * 64,
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def test_standard_duplicate_is_clean_and_resets_an_elapsed_start(self):
        old_fee = 12_345
        new_fee = 67_890
        now = 2_000_000_000
        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", old_fee):
            async with schema.get_db() as db:
                source_id = await db_access.create_spot(
                    db,
                    created_by=self.owner_id,
                    title="Original Standard",
                    desc="Copied description",
                    lat=55.95,
                    long=-3.19,
                    radius=300,
                    claim_duration=600,
                    max_claims_per_user=2,
                    max_total_claims=3,
                    total_value=const.MIN_SPOT_TOTAL_VALUE,
                    starts_at=now - 100,
                    ends_at=7 * 24 * 60 * 60,
                    use_password=True,
                    city="Edinburgh",
                    country="United Kingdom",
                    auto_reverse_geocode=False,
                )
                source = await db_access.get_spot(db, spot_id=source_id)
                await db_access.create_spot_deposit_transaction(
                    db,
                    user_id=self.owner_id,
                    spot_id=source_id,
                    amount=100,
                    from_address="NQ SOURCE",
                    to_address=str(source[schema.SPOT_DEPOSIT_ADDRESS]),
                    tx_hash="source-deposit-history",
                )
                await db_access.create_claim_code(
                    db,
                    spot_id=source_id,
                    claim_code="SOURCECODE1",
                )
                await db.commit()

        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", new_fee):
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    copy_id = await duplicate_owned_spot_as_draft(
                        db,
                        source_spot_id=source_id,
                        user_id=self.owner_id,
                        title="Copied Standard",
                        now=now,
                        draft_limit=10,
                    )
                source = await db_access.get_spot(db, spot_id=source_id)
                copy = await db_access.get_spot(db, spot_id=copy_id)
                copy_transactions = await db_access.get_transactions_by_spot(
                    db,
                    spot_id=copy_id,
                )
                copy_codes = await db_access.get_claim_codes(db, spot_id=copy_id)
                copy_claims = await db_access.get_claims(
                    db,
                    spot_id=copy_id,
                    include_failed=True,
                )

        self.assertNotEqual(copy_id, source_id)
        self.assertEqual(copy[schema.SPOT_STATUS], const.SPOT_STATUS_DRAFT)
        self.assertEqual(copy[schema.SPOT_TITLE], "Copied Standard")
        self.assertEqual(copy[schema.SPOT_DESC], source[schema.SPOT_DESC])
        self.assertEqual(copy[schema.SPOT_LAT], source[schema.SPOT_LAT])
        self.assertEqual(copy[schema.SPOT_LONG], source[schema.SPOT_LONG])
        self.assertEqual(copy[schema.SPOT_RADIUS], source[schema.SPOT_RADIUS])
        self.assertEqual(
            copy[schema.SPOT_MAX_TOTAL_CLAIMS],
            source[schema.SPOT_MAX_TOTAL_CLAIMS],
        )
        self.assertEqual(copy[schema.SPOT_ENDS_AT], source[schema.SPOT_ENDS_AT])
        self.assertIsNone(copy[schema.SPOT_STARTS_AT])
        self.assertNotEqual(copy[schema.SPOT_LINK], source[schema.SPOT_LINK])
        self.assertNotEqual(
            copy[schema.SPOT_DEPOSIT_ADDRESS],
            source[schema.SPOT_DEPOSIT_ADDRESS],
        )
        self.assertNotEqual(
            copy[schema.SPOT_DEPOSIT_KEY_INDEX],
            source[schema.SPOT_DEPOSIT_KEY_INDEX],
        )
        self.assertEqual(copy[schema.SPOT_CREATION_FEE], new_fee)
        self.assertEqual(copy_transactions, [])
        self.assertEqual(copy_codes, [])
        self.assertEqual(copy_claims, [])

    async def test_prizedraw_duplicate_preserves_future_schedule_and_prize_rules(self):
        now = 2_000_000_000
        future_start = now + 86_400
        async with schema.get_db() as db:
            source_id = await db_access.create_prizedraw(
                db,
                created_by=self.owner_id,
                title="Original Draw",
                desc="Draw details",
                lat=51.5,
                long=-0.1,
                radius=250,
                claim_duration=0,
                max_claims_per_user=1,
                max_total_claims=8,
                total_value=2 * const.MIN_PRIZEDRAW_PRIZE_PAYOUT,
                prize_count=2,
                starts_at=future_start,
                ends_at=3 * 24 * 60 * 60,
                city="London",
                country="United Kingdom",
                auto_reverse_geocode=False,
            )
            await db.commit()
            async with db_access.transaction(db, immediate=True):
                copy_id = await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=source_id,
                    user_id=self.owner_id,
                    title="Copied Draw",
                    now=now,
                    draft_limit=10,
                )
            copy = await db_access.get_spot_owner_summary(db, spot_id=copy_id)

        self.assertEqual(copy[schema.SPOT_STARTS_AT], future_start)
        self.assertEqual(copy[schema.SPOT_ENDS_AT], 3 * 24 * 60 * 60)
        self.assertEqual(copy[schema.SPOT_USE_PASSWORD], 0)
        self.assertEqual(copy[schema.PRIZEDRAW_PRIZE_COUNT], 2)

    async def test_duplicate_enforces_source_ownership_and_draft_limit(self):
        async with schema.get_db() as db:
            source_id = await db_access.create_spot(
                db,
                created_by=self.owner_id,
                title="Ownership Source",
            )
            await db.commit()

            with self.assertRaises(DuplicateSpotError) as not_owner:
                await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=source_id,
                    user_id=self.other_id,
                    title="Forbidden Copy",
                    now=2_000_000_000,
                    draft_limit=10,
                )
            self.assertEqual(not_owner.exception.code, "not_owner")

            with self.assertRaises(DuplicateSpotError) as limited:
                await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=source_id,
                    user_id=self.owner_id,
                    title="Excess Copy",
                    now=2_000_000_000,
                    draft_limit=1,
                )
            self.assertEqual(limited.exception.code, "draft_limit_reached")

    def test_configuration_is_an_explicit_non_operational_whitelist(self):
        source = {
            schema.SPOT_DESC: "Description",
            schema.SPOT_LAT: 1.0,
            schema.SPOT_LONG: 2.0,
            schema.SPOT_RADIUS: 100,
            schema.SPOT_CLAIM_DURATION: 0,
            schema.SPOT_MAX_CLAIMS_PER_USER: 1,
            schema.SPOT_MAX_TOTAL_CLAIMS: 2,
            schema.SPOT_TOTAL_VALUE: const.MIN_SPOT_TOTAL_VALUE,
            schema.SPOT_STARTS_AT: None,
            schema.SPOT_ENDS_AT: const.MIN_SPOT_ENDS_AFTER_SECONDS,
            schema.SPOT_USE_PASSWORD: 0,
            schema.SPOT_CITY: "Here",
            schema.SPOT_COUNTRY: "There",
            schema.SPOT_ID: 99,
            schema.SPOT_LINK: "old-link",
            schema.SPOT_DEPOSIT_ADDRESS: "old-wallet",
            schema.SPOT_STATUS: const.SPOT_STATUS_COMPLETED,
        }
        config = duplicate_spot_configuration(
            source,
            title="Whitelisted Copy",
            now=2_000_000_000,
        )["create_kwargs"]
        self.assertNotIn(schema.SPOT_ID, config)
        self.assertNotIn(schema.SPOT_LINK, config)
        self.assertNotIn(schema.SPOT_DEPOSIT_ADDRESS, config)
        self.assertNotIn(schema.SPOT_STATUS, config)
        self.assertEqual(config["title"], "Whitelisted Copy")
