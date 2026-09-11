from __future__ import annotations

import asyncio
import hashlib
import json
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
        self.spots: dict[int, dict] = {}

    async def asyncTearDown(self):
        await self.context.__aexit__(None, None, None)
        schema.DB_PATH = self.old_path
        self.tmp.close()

    def spot(self, spot_id: int, *, metres: float = 0, radius: int = 50,
             password: int = 0, owner: int = 999) -> dict:
        value = {
            schema.SPOT_ID: spot_id,
            schema.SPOT_CREATED_BY: owner,
            schema.SPOT_LAT: 51.5 + metres / 111_195,
            schema.SPOT_LONG: -0.1,
            schema.SPOT_RADIUS: radius,
            schema.SPOT_USE_PASSWORD: password,
        }
        self.spots[spot_id] = value
        return value

    async def claim(self, spot_id: int, *, metres: float | None = None,
                    noise_metres: float = 0, now: int | None = None,
                    password: int = 0, owner: int = 999, corroborated: bool = False):
        centre = float((spot_id - 1) * 100 if metres is None else metres)
        spot = self.spot(spot_id, metres=centre, password=password, owner=owner)

        async def get_spot(_db, *, spot_id):
            return self.spots.get(int(spot_id))

        with mock.patch.object(db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=True)), \
             mock.patch.object(db_access, "get_spot", side_effect=get_spot):
            result = await guard.observe_signed_claim(
                self.db, user_id=self.user_id, spot=spot,
                lat=float(spot[schema.SPOT_LAT]) + noise_metres / 111_195,
                long=float(spot[schema.SPOT_LONG]), location_accuracy_metres=999_999,
                now=self.now if now is None else now, corroborated=corroborated,
            )
        await self.db.commit()
        return result

    async def find(self, spots: list[dict], *, lat: float, long: float, now: int):
        with mock.patch.object(guard, "_claimable_public_spots", mock.AsyncMock(return_value=spots)):
            return await guard.observe_find_location(
                self.db, user_id=self.user_id, lat=lat, long=long, now=now
            )

    async def mature(self, *, start: int | None = None):
        base = self.now if start is None else start
        result = None
        for index in range(1, 5):
            result = await self.claim(index, now=base + index)
        return result

    async def test_one_exact_centre_claim_is_non_punitive_and_bad_accuracy_does_not_erase_it(self):
        result = await self.claim(1)
        self.assertFalse(result["restricted"])
        self.assertEqual(result["state"]["centre_location_spot_ids"], [1])

    async def test_same_spot_retry_and_ordinary_noise_do_not_inflate_centre_evidence(self):
        await self.claim(1)
        await self.claim(1, now=self.now + guard.SIGNED_RETRY_WINDOW_SECONDS + 1)
        await self.claim(2, noise_metres=8, now=self.now + guard.SIGNED_RETRY_WINDOW_SECONDS + 2)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["centre_location_spot_ids"], [1])

    async def test_colocated_and_nearby_spots_are_one_physical_location(self):
        await self.claim(1, metres=0)
        await self.claim(2, metres=0, now=self.now + 1)
        await self.claim(3, metres=20, now=self.now + 2)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["centre_location_spot_ids"], [1])
        self.assertEqual(state["inside_streak_spot_ids"], [1])

    async def test_geographically_distinct_locations_build_evidence(self):
        for index in range(1, 4):
            await self.claim(index, now=self.now + index)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["centre_location_spot_ids"], [1, 2, 3])
        self.assertEqual(state["inside_streak_spot_ids"], [1, 2, 3])

    async def test_outside_resets_streak_but_later_b_c_d_e_can_mature(self):
        await self.claim(1)
        await self.find([], lat=0, long=0, now=self.now + 2)
        for index in range(2, 6):
            result = await self.claim(index, noise_metres=5, now=self.now + index + 2)
        self.assertFalse(result["restricted"])
        self.assertEqual(result["state"]["inside_streak_spot_ids"], [2, 3, 4, 5])
        self.assertEqual(result["state"]["browser_outside_observations"], 1)

    async def test_inside_find_poll_does_not_hide_later_outside_transition(self):
        await self.claim(1)
        own_or_other = self.spot(90, metres=0, radius=50)
        await self.find([own_or_other], lat=51.5, long=-0.1, now=self.now + 60)
        await self.find([], lat=0, long=0, now=self.now + 5 * 60)
        await self.claim(2, now=self.now + 6 * 60)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["browser_outside_observations"], 1)
        self.assertEqual(state["inside_streak_spot_ids"], [2])

    async def test_repeated_outside_polling_is_heavily_deduplicated(self):
        for second in range(20):
            await self.find([], lat=0, long=0, now=self.now + second)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["meaningful_observations"], 1)
        self.assertEqual(state["browser_outside_observations"], 1)

    async def test_restriction_does_not_slide_and_consumed_evidence_does_not_rearm(self):
        result = await self.mature()
        expiry = result["state"]["restricted_until"]
        self.assertTrue(result["restricted"])
        during = await self.claim(99, now=expiry - 1)
        self.assertTrue(during["restricted"])
        self.assertEqual(during["state"]["restricted_until"], expiry)
        self.assertFalse((await guard.current_decision(
            self.db, user_id=self.user_id, now=expiry
        ))["restricted"])
        first_after = await self.claim(100, now=expiry)
        self.assertFalse(first_after["restricted"])
        self.assertEqual(first_after["state"]["inside_streak_spot_ids"], [100])

    async def test_new_post_expiry_episode_can_restrict_again(self):
        first = await self.mature()
        expiry = first["state"]["restricted_until"]
        result = None
        for index in range(10, 14):
            result = await self.claim(index, now=expiry + index)
        self.assertTrue(result["restricted"])
        self.assertGreater(result["state"]["restricted_until"], expiry)

    async def test_password_unclaimable_and_own_spots_are_excluded(self):
        self.assertEqual((await self.claim(1, password=1))["reason"], "password_exempt")
        own = await self.claim(2, owner=self.user_id)
        self.assertEqual(own["reason"], "own_spot_exempt")
        with mock.patch.object(db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=False)):
            expired = await guard.observe_signed_claim(
                self.db, user_id=self.user_id, spot=self.spot(3), lat=51.5, long=-0.1,
                now=self.now,
            )
        self.assertEqual(expired["reason"], "spot_not_claimable")
        self.assertEqual((await guard.get_state(self.db, user_id=self.user_id))["meaningful_observations"], 0)

    async def test_own_spot_is_outside_but_overlapping_other_spot_remains_inside(self):
        await self.claim(1)
        own = self.spot(80, metres=500, owner=self.user_id)
        # The eligible lookup excludes `own`; no other Spot means ordinary outside.
        await self.find([], lat=float(own[schema.SPOT_LAT]), long=-0.1, now=self.now + 1)
        self.assertTrue((await guard.get_state(self.db, user_id=self.user_id))["outside_since_last_inside"])
        other = self.spot(81, metres=500, owner=777)
        await self.claim(2, now=self.now + 2)
        await self.find([other], lat=float(other[schema.SPOT_LAT]), long=-0.1, now=self.now + 3)
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertFalse(state["outside_since_last_inside"])
        self.assertEqual(state["browser_outside_observations"], 1)

    async def test_eligible_lookup_filters_ownership_server_side(self):
        class CursorDb:
            def __init__(self):
                self.params = None

            async def execute_fetchall(self, query, params):
                self.query, self.params = query, params
                return []

        fake = CursorDb()
        await guard._claimable_public_spots(fake, user_id=self.user_id)
        self.assertIn(f"{schema.SPOT_CREATED_BY} != ?", fake.query)
        self.assertEqual(fake.params, (self.user_id,))

    async def test_state_is_hard_bounded_after_many_distinct_spots(self):
        # Keep one mature signal from restricting by adding ordinary GPS noise.
        for index in range(1, 501):
            await self.claim(index, noise_metres=5, now=self.now + index)
        state = await guard.get_state(self.db, user_id=self.user_id)
        encoded = json.dumps(state)
        self.assertLessEqual(len(state["inside_streak_spot_ids"]), 4)
        self.assertLessEqual(len(state["centre_location_spot_ids"]), 3)
        self.assertEqual(state["meaningful_observations"], guard.MAX_DIAGNOSTIC_COUNT)
        self.assertLess(len(encoded), 700)
        self.assertNotIn("lat", state)
        self.assertNotIn("long", state)

    async def test_v1_migration_discards_old_lists_and_preserves_finite_expiry(self):
        await self.db.execute(
            f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} VALUES (?, ?)",
            (guard._key(self.user_id), json.dumps({
                "version": 1, "inside_spot_ids": list(range(100)),
                "near_centre_spot_ids": list(range(100)), "restricted_until": self.now + 20,
            })),
        )
        state = await guard.get_state(self.db, user_id=self.user_id)
        self.assertEqual(state["centre_location_spot_ids"], [])
        self.assertEqual(state["inside_streak_spot_ids"], [])
        self.assertEqual(state["restricted_until"], self.now + 20)

    async def test_concurrent_same_event_is_counted_once(self):
        other_context = schema.get_db()
        other_db = await other_context.__aenter__()
        spot = self.spot(12)

        async def get_spot(_db, *, spot_id):
            return self.spots.get(int(spot_id))

        try:
            with mock.patch.object(db_access, "is_spot_currently_claimable", mock.AsyncMock(return_value=True)), \
                 mock.patch.object(db_access, "get_spot", side_effect=get_spot):
                await asyncio.gather(*(
                    guard.observe_signed_claim(
                        db, user_id=self.user_id, spot=spot, lat=float(spot[schema.SPOT_LAT]),
                        long=-0.1, now=self.now,
                    ) for db in (self.db, other_db)
                ))
            state = await guard.get_state(self.db, user_id=self.user_id)
            self.assertEqual(state["meaningful_observations"], 1)
            self.assertEqual(state["centre_location_spot_ids"], [12])
        finally:
            await other_context.__aexit__(None, None, None)

    async def test_combined_evidence_never_bans(self):
        result = await self.mature()
        self.assertTrue(result["restricted"])
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        self.assertNotEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_BANNED)
