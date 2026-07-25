from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from unittest import mock

import cache
import constants as const
import content_moderation
import database as schema
import db_access
import public_html


class ContentModerationTextTests(unittest.TestCase):
    def test_normal_and_whitespace_disguised_words_are_masked(self):
        censored, changed = content_moderation.censor_text(
            "A FUCK sign and F U C K notice"
        )
        self.assertTrue(changed)
        self.assertEqual(censored, "A #### sign and # # # # notice")

    def test_whitespace_is_preserved_while_letters_are_masked(self):
        censored, changed = content_moderation.censor_text("f\tu\nc k")
        self.assertTrue(changed)
        self.assertEqual(censored, "#\t#\n# #")

    def test_substrings_inside_innocent_words_are_not_blocked(self):
        censored, changed = content_moderation.censor_text(
            "Scunthorpe has a documented filtering problem."
        )
        self.assertFalse(changed)
        self.assertEqual(censored, "Scunthorpe has a documented filtering problem.")

    def test_optional_description_can_remain_none(self):
        self.assertEqual(content_moderation.censor_text(None), (None, False))


class ContentModerationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self.device_hash = hashlib.sha256(b"content-moderation-user").hexdigest()
        async with schema.get_db() as db:
            self.user_id = await db_access.create_user(
                db,
                device_id_hash=self.device_hash,
            )
            await db.commit()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()

    @staticmethod
    def _json(response):
        return json.loads(response.body.decode("utf-8"))

    async def _create_funded_spot(self, *, title: str, description: str) -> int:
        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", 0):
            async with schema.get_db() as db:
                spot_id = await db_access.create_spot(
                    db,
                    created_by=self.user_id,
                    title=title,
                    desc=description,
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=0,
                    max_claims_per_user=1,
                    max_total_claims=1,
                    total_value=const.MIN_STANDARD_CLAIM_PAYOUT,
                    starts_at=int(time.time()) + 3600,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
                spot = await db_access.get_spot(db, spot_id=spot_id)
                transaction_id = await db_access.create_spot_deposit_transaction(
                    db,
                    user_id=self.user_id,
                    spot_id=spot_id,
                    amount=db_access.spot_required_deposit_amount(spot),
                    from_address=const.DEV_PLATFORM_FEE_ADDRESS,
                    to_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                    tx_hash=f"moderation-deposit-{spot_id}",
                )
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=transaction_id,
                    block_number=123,
                )
                await db.commit()
                return int(spot_id)

    def _session_payload(self):
        return public_html.HomeSessionRequest(
            device_id_hash=self.device_hash,
            wallet_available=True,
        )

    async def test_rude_display_name_is_rejected_and_starts_one_hour_cooldown(self):
        response = await public_html.update_display_name(
            public_html.DisplayNameRequest(
                device_id_hash=self.device_hash,
                display_name="F U C K",
            )
        )
        body = self._json(response)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "inappropriate_display_name")
        self.assertGreaterEqual(body["moderation_retry_after_seconds"], 3599)

        async with schema.get_db() as db:
            user = await db_access.get_user_by_id(db, user_id=self.user_id)
            marker = await content_moderation.get_content_cooldown(
                db,
                user_id=self.user_id,
            )
        self.assertNotEqual(user[schema.USER_DISPLAY_NAME], "F U C K")
        self.assertIsNotNone(marker)
        retry_at = marker["retry_at"]

        blocked = await public_html.update_display_name(
            public_html.DisplayNameRequest(
                device_id_hash=self.device_hash,
                display_name="Clean Name",
            )
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self._json(blocked)["code"], "content_moderation_cooldown")

        async with schema.get_db() as db:
            marker_after_retry = await content_moderation.get_content_cooldown(
                db,
                user_id=self.user_id,
            )
        self.assertEqual(marker_after_retry["retry_at"], retry_at)

    async def test_expired_cooldown_is_cleared_and_clean_name_can_save(self):
        async with schema.get_db() as db:
            marker = await content_moderation.start_content_cooldown(
                db,
                user_id=self.user_id,
                reason="test",
                checked_at=100,
            )
            self.assertEqual(marker["retry_at"], 100 + const.CONTENT_MODERATION_COOLDOWN_SECONDS)
            expired = await content_moderation.get_content_cooldown(
                db,
                user_id=self.user_id,
                checked_at=marker["retry_at"],
            )
            await db.commit()
        self.assertIsNone(expired)

        response = await public_html.update_display_name(
            public_html.DisplayNameRequest(
                device_id_hash=self.device_hash,
                display_name="Clean Name",
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["user"]["display_name"], "Clean Name")

    async def test_publish_censors_spot_text_then_starts_cooldown(self):
        spot_id = await self._create_funded_spot(
            title="F U C K Party",
            description="This description is shit.",
        )

        response = await public_html.my_spots_publish_api(
            spot_id,
            self._session_payload(),
        )
        body = self._json(response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["content_censored"])
        self.assertTrue(body["title_censored"])
        self.assertTrue(body["description_censored"])

        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=spot_id)
            marker = await content_moderation.get_content_cooldown(
                db,
                user_id=self.user_id,
            )
        self.assertEqual(spot[schema.SPOT_STATUS], const.SPOT_STATUS_PUBLISHED)
        self.assertEqual(spot[schema.SPOT_TITLE], "# # # # Party")
        self.assertEqual(spot[schema.SPOT_DESC], "This description is ####.")
        self.assertIsNotNone(marker)

    async def test_active_moderation_cooldown_blocks_another_publish(self):
        first_spot = await self._create_funded_spot(
            title="Shit Event",
            description="First event.",
        )
        first_response = await public_html.my_spots_publish_api(
            first_spot,
            self._session_payload(),
        )
        self.assertEqual(first_response.status_code, 200)

        second_spot = await self._create_funded_spot(
            title="Clean Event",
            description="Second event.",
        )
        blocked = await public_html.my_spots_publish_api(
            second_spot,
            self._session_payload(),
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self._json(blocked)["code"], "content_moderation_cooldown")
        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=second_spot)
        self.assertEqual(spot[schema.SPOT_STATUS], const.SPOT_STATUS_DRAFT)


if __name__ == "__main__":
    unittest.main()
