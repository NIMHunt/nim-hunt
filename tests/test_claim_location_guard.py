from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from fastapi import BackgroundTasks

import cache
import claim_location_guard
import constants as const
import database as schema
import db_access
import public_html


class ClaimLocationGuardTest(unittest.IsolatedAsyncioTestCase):
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

    async def _create_published_spot(
        self,
        db,
        *,
        owner_suffix: str,
        title: str,
        lat: float,
        long: float,
        radius: int = 200,
        claim_duration: int = 0,
    ) -> int:
        owner_id = await db_access.create_user(
            db,
            device_id_hash=f"location-owner-{owner_suffix}",
        )
        max_claims = 10
        spot_id = await db_access.create_spot(
            db,
            created_by=owner_id,
            title=title,
            lat=lat,
            long=long,
            radius=radius,
            claim_duration=claim_duration,
            max_claims_per_user=1,
            max_total_claims=max_claims,
            total_value=max_claims * const.MIN_STANDARD_CLAIM_PAYOUT,
            starts_at=int(time.time()) - 60,
            ends_at=24 * 60 * 60,
            auto_reverse_geocode=False,
        )
        await db.execute(
            f"""
            UPDATE {schema.SPOT_TABLE_NAME}
            SET {schema.SPOT_STATUS} = ?
            WHERE {schema.SPOT_ID} = ?;
            """,
            (const.SPOT_STATUS_PUBLISHED, spot_id),
        )
        return spot_id

    async def _create_user_claim(
        self,
        db,
        *,
        user_id: int,
        spot_id: int,
        lat: float,
        long: float,
    ) -> dict:
        return await db_access.create_claim_attempt(
            db,
            spot_id=spot_id,
            user_id=user_id,
            lat=lat,
            long=long,
            location_accuracy_metres=5.0,
            payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
        )

    async def _set_claim_time(
        self,
        db,
        *,
        claim_id: int,
        claimed_at: int,
        updated_at: int | None = None,
    ) -> None:
        await db.execute(
            f"""
            UPDATE {schema.CLAIM_TABLE_NAME}
            SET {schema.CLAIM_CLAIMED_AT} = ?,
                {schema.CLAIM_UPDATED_AT} = ?
            WHERE {schema.CLAIM_ID} = ?;
            """,
            (
                int(claimed_at),
                int(claimed_at if updated_at is None else updated_at),
                int(claim_id),
            ),
        )

    async def test_commercial_flight_speed_is_not_flagged(self):
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash="b" * 64)
            london_id = await self._create_published_spot(
                db,
                owner_suffix="london",
                title="London",
                lat=51.5074,
                long=-0.1278,
            )
            new_york_id = await self._create_published_spot(
                db,
                owner_suffix="new-york",
                title="New York",
                lat=40.7128,
                long=-74.0060,
            )
            claim = await self._create_user_claim(
                db,
                user_id=user_id,
                spot_id=london_id,
                lat=51.5074,
                long=-0.1278,
            )
            now = await db_access.get_unixepoch(db)
            await self._set_claim_time(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
                claimed_at=now - (8 * 60 * 60),
            )

            check = await claim_location_guard.get_impossible_claim_travel_check(
                db,
                user_id=user_id,
                target_spot_id=new_york_id,
            )

        self.assertFalse(check["detected"], check)

    async def test_short_distance_is_not_flagged_even_when_time_is_tiny(self):
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash="d" * 64)
            glasgow_id = await self._create_published_spot(
                db,
                owner_suffix="glasgow",
                title="Glasgow",
                lat=55.8642,
                long=-4.2518,
            )
            edinburgh_id = await self._create_published_spot(
                db,
                owner_suffix="edinburgh",
                title="Edinburgh",
                lat=55.9533,
                long=-3.1883,
            )
            claim = await self._create_user_claim(
                db,
                user_id=user_id,
                spot_id=glasgow_id,
                lat=55.8642,
                long=-4.2518,
            )
            now = await db_access.get_unixepoch(db)
            await self._set_claim_time(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
                claimed_at=now - 1,
            )

            check = await claim_location_guard.get_impossible_claim_travel_check(
                db,
                user_id=user_id,
                target_spot_id=edinburgh_id,
            )

        self.assertFalse(check["detected"], check)

    async def test_pending_duration_heartbeat_is_recent_location_anchor(self):
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash="c" * 64)
            london_id = await self._create_published_spot(
                db,
                owner_suffix="duration-london",
                title="Duration London",
                lat=51.5074,
                long=-0.1278,
                claim_duration=60 * 60,
            )
            paris_id = await self._create_published_spot(
                db,
                owner_suffix="duration-paris",
                title="Duration Paris",
                lat=48.8566,
                long=2.3522,
            )
            claim = await self._create_user_claim(
                db,
                user_id=user_id,
                spot_id=london_id,
                lat=51.5074,
                long=-0.1278,
            )
            self.assertEqual(
                int(claim[schema.CLAIM_STATUS]),
                const.CLAIM_STATUS_PENDING,
            )
            now = await db_access.get_unixepoch(db)
            await self._set_claim_time(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
                claimed_at=now - (2 * 60 * 60),
                updated_at=now - 60,
            )

            check = await claim_location_guard.get_impossible_claim_travel_check(
                db,
                user_id=user_id,
                target_spot_id=paris_id,
            )

        self.assertTrue(check["detected"], check)
        self.assertEqual(check["elapsed_seconds"], 60)

    async def test_claim_api_commits_ban_without_creating_impossible_claim(self):
        device_hash = "a" * 64
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash=device_hash)
            london_id = await self._create_published_spot(
                db,
                owner_suffix="api-london",
                title="API London",
                lat=51.5074,
                long=-0.1278,
            )
            paris_id = await self._create_published_spot(
                db,
                owner_suffix="api-paris",
                title="API Paris",
                lat=48.8566,
                long=2.3522,
            )
            claim = await self._create_user_claim(
                db,
                user_id=user_id,
                spot_id=london_id,
                lat=51.5074,
                long=-0.1278,
            )
            now = await db_access.get_unixepoch(db)
            await self._set_claim_time(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
                claimed_at=now - (5 * 60),
            )
            await db.commit()

        payload = public_html.ClaimSpotRequest(
            device_id_hash=device_hash,
            wallet_available=True,
            location_available=True,
            lat=48.8566,
            long=2.3522,
            accuracy=5.0,
            payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
        )
        with mock.patch.object(
            db_access,
            "create_claim",
            claim_location_guard.create_claim_with_impossible_travel_guard,
        ):
            response = await public_html.claim_spot_api(
                paris_id,
                payload,
                BackgroundTasks(),
            )
        body = json.loads(response.body)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["code"], "claim_failed")
        self.assertIn("has been banned", body["message"])

        async with schema.get_db() as db:
            user = await db_access.get_user_by_id(db, user_id=user_id)
            claim_rows = await db.execute_fetchall(
                f"SELECT {schema.CLAIM_ID} FROM {schema.CLAIM_TABLE_NAME} "
                f"WHERE {schema.CLAIM_RECIPIENT} = ?;",
                (user_id,),
            )

        self.assertIsNotNone(user)
        self.assertEqual(
            int(user[schema.USER_STATUS]),
            const.USER_STATUS_BANNED,
        )
        self.assertEqual(len(claim_rows), 1)


class ClaimLocationGuardRuntimeTest(unittest.TestCase):
    def test_public_runtime_installs_guard_after_claim_code_policy(self):
        env = os.environ.copy()
        env.update(
            {
                "NIMHUNT_DEPLOYMENT_MODE": "development",
                "NIMHUNT_NIMIQ_NETWORK": "TestAlbatross",
                "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC": "1",
            }
        )
        command = (
            "import db_access, funding_flow, claim_location_guard; "
            "funding_flow.install(); "
            "assert db_access.create_claim is "
            "claim_location_guard.create_claim_with_impossible_travel_guard"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
