from __future__ import annotations

import hashlib
import json
import math
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

    async def _spot(
        self,
        db,
        *,
        suffix: str,
        lat: float,
        long: float,
        radius: int = 200,
        claim_duration: int = 0,
    ) -> int:
        owner_id = await db_access.create_user(
            db,
            device_id_hash=hashlib.sha256(
                f"location-owner-{suffix}".encode("utf-8")
            ).hexdigest(),
        )
        max_claims = 10
        spot_id = await db_access.create_spot(
            db,
            created_by=owner_id,
            title=f"Spot {suffix}",
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
        return int(spot_id)

    async def _claim(
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

    async def _guarded_claim(self, db, **kwargs) -> dict:
        with mock.patch.object(
            db_access,
            "create_claim",
            claim_location_guard.create_claim_with_impossible_travel_guard,
        ):
            return await self._claim(db, **kwargs)

    async def _guarded_rejection(self, db, **kwargs) -> ValueError:
        try:
            await self._guarded_claim(db, **kwargs)
        except ValueError as exc:
            # The public route catches this inside its transaction. Commit here
            # so direct tests reproduce the same durable security write.
            await db.commit()
            return exc
        self.fail("Expected the guarded claim attempt to be rejected")

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

    async def _marker(self, db, *, user_id: int) -> dict | None:
        return await claim_location_guard.get_claim_location_suspicion(
            db,
            user_id=user_id,
        )

    async def _replace_marker_times(
        self,
        db,
        *,
        user_id: int,
        attempted_at: int,
        retry_at: int,
    ) -> None:
        marker = await self._marker(db, user_id=user_id)
        self.assertIsNotNone(marker)
        marker["attempted_at"] = int(attempted_at)
        marker["retry_at"] = int(retry_at)
        marker["last_attempted_at"] = int(attempted_at)
        await db.execute(
            f"""
            UPDATE {schema.APP_METADATA_TABLE_NAME}
            SET {schema.APP_METADATA_VALUE} = ?
            WHERE {schema.APP_METADATA_KEY} = ?;
            """,
            (
                json.dumps(marker, separators=(",", ":"), sort_keys=True),
                f"{claim_location_guard.SUSPICION_METADATA_KEY_PREFIX}{user_id}",
            ),
        )

    async def _claim_count(self, db, *, user_id: int) -> int:
        cur = await db.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM {schema.CLAIM_TABLE_NAME}
            WHERE {schema.CLAIM_RECIPIENT} = ?;
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
        return int(row["n"])

    async def _scenario(
        self,
        db,
        *,
        suffix: str,
        target_long: float = 0.022,
        claim_duration: int = 0,
        trusted_age: int = 1,
        updated_age: int | None = None,
    ) -> tuple[int, int, int, dict]:
        user_id = await db_access.create_user(
            db,
            device_id_hash=hashlib.sha256(suffix.encode("utf-8")).hexdigest(),
        )
        trusted_id = await self._spot(
            db,
            suffix=f"{suffix}-a",
            lat=0.0,
            long=0.0,
            claim_duration=claim_duration,
        )
        target_id = await self._spot(
            db,
            suffix=f"{suffix}-b",
            lat=0.0,
            long=target_long,
        )
        claim = await self._claim(
            db,
            user_id=user_id,
            spot_id=trusted_id,
            lat=0.0,
            long=0.0,
        )
        now = await db_access.get_unixepoch(db)
        await self._set_claim_time(
            db,
            claim_id=int(claim[schema.CLAIM_ID]),
            claimed_at=now - int(trusted_age),
            updated_at=(
                None if updated_age is None else now - int(updated_age)
            ),
        )
        await db.commit()
        return user_id, trusted_id, target_id, claim

    async def test_thresholds_are_centralised_in_constants(self):
        self.assertEqual(const.CLAIM_LOCATION_HARD_BAN_MIN_DISTANCE_METRES, 3_000)
        self.assertEqual(
            const.CLAIM_LOCATION_HARD_BAN_MAX_SPEED_METRES_PER_SECOND,
            500,
        )
        self.assertEqual(
            const.CLAIM_LOCATION_SOFT_COOLDOWN_MIN_DISTANCE_METRES,
            1_000,
        )
        self.assertEqual(
            const.CLAIM_LOCATION_SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND,
            75,
        )

    async def test_commercial_flight_speed_causes_cooldown_not_ban(self):
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash="b" * 64)
            london_id = await self._spot(
                db,
                suffix="flight-london",
                lat=51.5074,
                long=-0.1278,
            )
            new_york_id = await self._spot(
                db,
                suffix="flight-new-york",
                lat=40.7128,
                long=-74.0060,
            )
            claim = await self._claim(
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
            decision = await claim_location_guard.get_claim_location_decision(
                db,
                user_id=user_id,
                target_spot_id=new_york_id,
            )
            user = await db_access.get_user_by_id(db, user_id=user_id)

        self.assertEqual(decision["action"], "cooldown", decision)
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_ACTIVE)

    async def test_normal_ground_travel_is_allowed(self):
        async with schema.get_db() as db:
            user_id, _, target_id, _ = await self._scenario(
                db,
                suffix="l-ground",
                target_long=0.2,
                trusted_age=15 * 60,
            )
            decision = await claim_location_guard.get_claim_location_decision(
                db,
                user_id=user_id,
                target_spot_id=target_id,
            )
        self.assertEqual(decision["action"], "allow", decision)

    async def test_three_kilometre_extreme_jump_bans_without_new_claim(self):
        async with schema.get_db() as db:
            user_id, _, target_id, _ = await self._scenario(
                db,
                suffix="c-hard",
                target_long=0.035,
            )
            exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=target_id,
                lat=0.0,
                long=0.035,
            )
            user = await db_access.get_user_by_id(db, user_id=user_id)
            count = await self._claim_count(db, user_id=user_id)
        self.assertIn("has been banned", str(exc))
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_BANNED)
        self.assertEqual(count, 1)

    async def test_soft_jump_starts_durable_calculated_global_cooldown(self):
        async with schema.get_db() as db:
            user_id, _, target_id, _ = await self._scenario(
                db,
                suffix="d-soft",
            )
            exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=target_id,
                lat=0.0,
                long=0.022,
            )
            marker = await self._marker(db, user_id=user_id)
            user = await db_access.get_user_by_id(db, user_id=user_id)
            count = await self._claim_count(db, user_id=user_id)

        self.assertIsInstance(exc, claim_location_guard.ClaimLocationCooldownError)
        self.assertIn("cannot claim any Spot", str(exc))
        minimum_distance = max(
            0.0,
            db_access.distance_metres(0.0, 0.0, 0.0, 0.022) - 400.0,
        )
        expected_retry_at = marker["attempted_at"] + math.ceil(
            minimum_distance
            / const.CLAIM_LOCATION_SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND
        ) + 1
        self.assertEqual(marker["retry_at"], expected_retry_at)
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_ACTIVE)
        self.assertEqual(count, 1)

        # A fresh connection proves the marker survived outside process memory.
        async with schema.get_db() as db:
            persisted = await self._marker(db, user_id=user_id)
        self.assertEqual(persisted, marker)

    async def test_nearby_attempts_are_blocked_without_restarting_timer(self):
        async with schema.get_db() as db:
            user_id, _, suspicious_id, _ = await self._scenario(
                db,
                suffix="e-near",
            )
            nearby_id = await self._spot(
                db,
                suffix="e-near-c",
                lat=0.0,
                long=0.024,
            )
            await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            first = await self._marker(db, user_id=user_id)
            nearby_exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=nearby_id,
                lat=0.0,
                long=0.024,
            )
            after_nearby = await self._marker(db, user_id=user_id)
            return_exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            after_return = await self._marker(db, user_id=user_id)
            user = await db_access.get_user_by_id(db, user_id=user_id)
            count = await self._claim_count(db, user_id=user_id)

        self.assertIsInstance(
            nearby_exc,
            claim_location_guard.ClaimLocationCooldownError,
        )
        self.assertIsInstance(
            return_exc,
            claim_location_guard.ClaimLocationCooldownError,
        )
        self.assertEqual(after_nearby["last_attempted_spot_id"], nearby_id)
        self.assertEqual(after_return["last_attempted_spot_id"], suspicious_id)
        self.assertEqual(after_nearby["retry_at"], first["retry_at"])
        self.assertEqual(after_return["retry_at"], first["retry_at"])
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_ACTIVE)
        self.assertEqual(count, 1)

    async def test_mercy_rule_protects_return_near_trusted_location(self):
        async with schema.get_db() as db:
            user_id, _, suspicious_id, _ = await self._scenario(
                db,
                suffix="f-mercy",
            )
            near_trusted_id = await self._spot(
                db,
                suffix="f-mercy-c",
                lat=0.0,
                long=0.001,
            )
            await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=near_trusted_id,
                lat=0.0,
                long=0.001,
            )
            user = await db_access.get_user_by_id(db, user_id=user_id)
            marker = await self._marker(db, user_id=user_id)

        self.assertIsInstance(exc, claim_location_guard.ClaimLocationCooldownError)
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_ACTIVE)
        self.assertEqual(marker["last_attempted_spot_id"], near_trusted_id)

    async def test_second_jump_inconsistent_with_trusted_and_original_bans(self):
        async with schema.get_db() as db:
            user_id, _, suspicious_id, _ = await self._scenario(
                db,
                suffix="g-second",
            )
            third_id = await self._spot(
                db,
                suffix="g-second-c",
                lat=0.022,
                long=0.0,
            )
            await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=third_id,
                lat=0.022,
                long=0.0,
            )
            user = await db_access.get_user_by_id(db, user_id=user_id)
            marker = await self._marker(db, user_id=user_id)
            count = await self._claim_count(db, user_id=user_id)

        self.assertIn("has been banned", str(exc))
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_BANNED)
        self.assertIsNone(marker)
        self.assertEqual(count, 1)

    async def test_consecutive_same_spot_retry_does_not_escalate(self):
        async with schema.get_db() as db:
            user_id, _, suspicious_id, _ = await self._scenario(
                db,
                suffix="h-repeat",
            )
            await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            before = await self._marker(db, user_id=user_id)
            exc = await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            after = await self._marker(db, user_id=user_id)
            user = await db_access.get_user_by_id(db, user_id=user_id)

        self.assertIsInstance(exc, claim_location_guard.ClaimLocationCooldownError)
        self.assertEqual(after, before)
        self.assertEqual(int(user[schema.USER_STATUS]), const.USER_STATUS_ACTIVE)

    async def test_expired_timer_allows_claim_and_clears_marker(self):
        async with schema.get_db() as db:
            user_id, _, suspicious_id, claim = await self._scenario(
                db,
                suffix="i-expire",
            )
            await self._guarded_rejection(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            now = await db_access.get_unixepoch(db)
            await self._set_claim_time(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
                claimed_at=now - 120,
            )
            await self._replace_marker_times(
                db,
                user_id=user_id,
                attempted_at=now - 120,
                retry_at=now - 1,
            )
            await db.commit()
            new_claim = await self._guarded_claim(
                db,
                user_id=user_id,
                spot_id=suspicious_id,
                lat=0.0,
                long=0.022,
            )
            await db.commit()
            marker = await self._marker(db, user_id=user_id)
            count = await self._claim_count(db, user_id=user_id)

        self.assertEqual(int(new_claim[schema.CLAIM_SPOT_ID]), suspicious_id)
        self.assertIsNone(marker)
        self.assertEqual(count, 2)

    async def test_pending_duration_heartbeat_is_recent_trusted_anchor(self):
        async with schema.get_db() as db:
            user_id, _, target_id, _ = await self._scenario(
                db,
                suffix="j-duration",
                target_long=0.035,
                claim_duration=60 * 60,
                trusted_age=2 * 60 * 60,
                updated_age=1,
            )
            decision = await claim_location_guard.get_claim_location_decision(
                db,
                user_id=user_id,
                target_spot_id=target_id,
            )
        self.assertEqual(decision["action"], "ban", decision)
        self.assertEqual(decision["elapsed_seconds"], 1)

    async def test_claim_api_commits_cooldown_without_creating_claim(self):
        device_hash = "a" * 64
        async with schema.get_db() as db:
            user_id = await db_access.create_user(db, device_id_hash=device_hash)
            trusted_id = await self._spot(
                db,
                suffix="api-a",
                lat=0.0,
                long=0.0,
            )
            target_id = await self._spot(
                db,
                suffix="api-b",
                lat=0.0,
                long=0.022,
            )
            claim = await self._claim(
                db,
                user_id=user_id,
                spot_id=trusted_id,
                lat=0.0,
                long=0.0,
            )
            now = await db_access.get_unixepoch(db)
            await self._set_claim_time(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
                claimed_at=now - 1,
            )
            await db.commit()

        payload = public_html.ClaimSpotRequest(
            device_id_hash=device_hash,
            wallet_available=True,
            location_available=True,
            lat=0.0,
            long=0.022,
            accuracy=5.0,
            payout_address=const.DEV_PLATFORM_FEE_ADDRESS,
        )
        with mock.patch.object(
            db_access,
            "create_claim",
            claim_location_guard.create_claim_with_impossible_travel_guard,
        ):
            response = await public_html.claim_spot_api(
                target_id,
                payload,
                BackgroundTasks(),
            )
        body = json.loads(response.body)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["code"], "claim_failed")
        self.assertIn("postponed", body["message"])
        async with schema.get_db() as db:
            marker = await self._marker(db, user_id=user_id)
            count = await self._claim_count(db, user_id=user_id)
        self.assertIsNotNone(marker)
        self.assertEqual(count, 1)


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
