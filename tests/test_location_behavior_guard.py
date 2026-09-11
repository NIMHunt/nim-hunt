from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from unittest import mock

import cache
import constants as const
import database as schema
import db_access
import location_behavior_guard as guard


class LocationBehaviorGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self.context = schema.get_db()
        self.db = await self.context.__aenter__()
        self.user_id = await db_access.create_user(
            self.db, device_id_hash=hashlib.sha256(b"behaviour-user").hexdigest()
        )
        await self.db.commit()
        self.now = await db_access.get_unixepoch(self.db)

    async def asyncTearDown(self):
        await self.context.__aexit__(None, None, None)
        schema.DB_PATH = self.old_path
        self.tmp.close()

    @staticmethod
    def spot(spot_id: int, *, lat: float = 51.5, long: float = -0.1, radius: int = 50,
             password: int = 0) -> dict:
        return {
            schema.SPOT_ID: spot_id,
            schema.SPOT_LAT: lat,
            schema.SPOT_LONG: long,
            schema.SPOT_RADIUS: radius,
            schema.SPOT_USE_PASSWORD: password,
        }

    async def claim(self, spot_id: int, *, offset_metres: float = 0, now: int | None = None,
                    password: int = 0, corroborated: bool = False):
        # Latitude is approximately 111,195 metres per degree here.
        spot = self.spot(spot_id, password=password)
        with mock.patch.object(db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=True)):
            result = await guard.observe_signed_claim(
                self.db, user_id=self.user_id, spot=spot,
                lat=51.5 + offset_metres / 111_195, long=-0.1,
                location_accuracy_metres=999_999, now=self.now if now is None else now,
                corroborated=corroborated,
            )
        await self.db.commit()
        return result

    async def test_one_exact_centre_claim_records_without_punishment(self):
        result = await self.claim(1)
        self.assertFalse(result["restricted"])
        self.assertEqual(result["state"]["near_centre_spot_ids"], [1])

    async def test_same_spot_retries_do_not_inflate_distinct_centre_evidence(self):
        await self.claim(1)
        await self.claim(1, now=self.now + guard.OBSERVATION_WINDOW_SECONDS + 1)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["near_centre_spot_ids"], [1])
        self.assertEqual(state["signed_inside_observations"], 2)

    async def test_distinct_near_centres_accumulate_but_noise_does_not(self):
        await self.claim(1, offset_metres=0.4)
        await self.claim(2, offset_metres=1.2, now=self.now + 1)
        await self.claim(3, offset_metres=8, now=self.now + 2)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["near_centre_spot_ids"], [1, 2])
        self.assertEqual(state["inside_spot_ids"], [1, 2, 3])

    async def test_find_polling_is_deduplicated_and_outside_history_is_normal(self):
        with mock.patch.object(guard, "_claimable_public_spots", mock.AsyncMock(return_value=[])):
            for second in range(20):
                await guard.observe_find_location(
                    self.db, user_id=self.user_id, lat=0, long=0, now=self.now + second
                )
            await guard.observe_find_location(
                self.db, user_id=self.user_id, lat=1, long=1,
                now=self.now + guard.FIND_OBSERVATION_WINDOW_SECONDS + 1,
            )
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["meaningful_observations"], 2)
        self.assertEqual(state["browser_outside_observations"], 2)

    async def test_outside_between_spots_breaks_inside_only_sequence(self):
        await self.claim(1)
        with mock.patch.object(guard, "_claimable_public_spots", mock.AsyncMock(return_value=[])):
            await guard.observe_find_location(
                self.db, user_id=self.user_id, lat=0, long=0, now=self.now + 1
            )
        await self.claim(2, now=self.now + 2)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["inside_transitions_without_outside"], 0)
        self.assertEqual(state["browser_outside_observations"], 1)

    async def test_delayed_distinct_inside_only_appearances_accumulate(self):
        for index in range(1, 5):
            result = await self.claim(index, offset_metres=5, now=self.now + index * 7 * 86400)
        self.assertFalse(result["restricted"])  # inside-only is supporting evidence, not a ban
        self.assertEqual(result["state"]["inside_transitions_without_outside"], 3)

    async def test_combined_evidence_temporarily_restricts_without_banning(self):
        for index in range(1, 5):
            result = await self.claim(
                index, offset_metres=0.5, now=self.now + index * guard.OBSERVATION_WINDOW_SECONDS
            )
        self.assertTrue(result["restricted"])
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        self.assertNotEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_BANNED)

    async def test_independent_fresh_location_anomaly_corroborates_one_pattern(self):
        for index in range(1, 4):
            result = await self.claim(index, now=self.now + index, corroborated=True)
        self.assertTrue(result["restricted"])

    async def test_password_and_unclaimable_spots_are_excluded(self):
        password = await self.claim(1, password=1)
        self.assertEqual(password["reason"], "password_exempt")
        with mock.patch.object(db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=False)):
            expired = await guard.observe_signed_claim(
                self.db, user_id=self.user_id, spot=self.spot(2), lat=51.5, long=-0.1,
                now=self.now,
            )
        self.assertEqual(expired["reason"], "spot_not_claimable")
        self.assertEqual((await guard.get_state(self.db, user_id=self.user_id))["meaningful_observations"], 0)

    async def test_server_coordinates_radius_and_no_raw_history_storage(self):
        spot = self.spot(9, lat=20, long=30, radius=25)
        with mock.patch.object(db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=True)):
            await guard.observe_signed_claim(
                self.db, user_id=self.user_id, spot=spot, lat=20, long=30,
                location_accuracy_metres=1_000_000, now=self.now,
            )
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["near_centre_spot_ids"], [9])
        self.assertNotIn("lat", state)
        self.assertNotIn("long", state)
        self.assertLess(len(str(state)), 1_000)

    async def test_concurrent_same_event_is_counted_once(self):
        other_context = schema.get_db()
        other_db = await other_context.__aenter__()
        try:
            spot = self.spot(12)
            with mock.patch.object(
                db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=True)
            ):
                await asyncio.gather(*(
                    guard.observe_signed_claim(
                        db,
                        user_id=self.user_id,
                        spot=spot,
                        lat=51.5,
                        long=-0.1,
                        now=self.now,
                    )
                    for db in (self.db, other_db)
                ))
            state = await guard.get_state(self.db, user_id=self.user_id)
            self.assertEqual(state["meaningful_observations"], 1)
            self.assertEqual(state["near_centre_spot_ids"], [12])
        finally:
            await other_context.__aexit__(None, None, None)
