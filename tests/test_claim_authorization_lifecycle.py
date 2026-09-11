from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from unittest import mock

import cache
import claim_authorization
import claim_security
import constants as const
import database as schema
import db_access
import fresh_claim_guard

WALLET_A = "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"
WALLET_B = "NQ48 LH6Q 7PFD LJYF 7PGB NJXL F8CX GHTJ YEKG"


class ClaimAuthorizationLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self.device = hashlib.sha256(b"claim-device").hexdigest()
        self.token = "session-token"
        async with schema.get_db() as db:
            self.user_id = await db_access.create_user(db, device_id_hash=self.device)
            owner = await db_access.create_user(
                db, device_id_hash=hashlib.sha256(b"owner").hexdigest()
            )
            self.spot_id = await db_access.create_spot(
                db,
                created_by=owner,
                title="Authorization lifecycle",
                lat=51.5,
                long=-0.1,
                radius=100,
                claim_duration=0,
                max_claims_per_user=10,
                max_total_claims=20,
                total_value=20 * const.MIN_STANDARD_CLAIM_PAYOUT,
                starts_at=int(time.time()) - 60,
                ends_at=3600,
                auto_reverse_geocode=False,
            )
            await db.execute(
                f"UPDATE {schema.SPOT_TABLE_NAME} SET {schema.SPOT_STATUS}=? WHERE {schema.SPOT_ID}=?",
                (const.SPOT_STATUS_PUBLISHED, self.spot_id),
            )
            await claim_security._metadata_set(
                db,
                claim_security._session_key(self.token),
                {
                    "user_id": self.user_id,
                    "device_id_hash": self.device,
                    "wallet_address": WALLET_A,
                    "created_at": 100,
                    "expires_at": 10_000,
                },
            )
            # These tests exercise authorization lifecycle rather than the
            # independent fresh-signer policy. Establish the fixture signer in
            # the same durable cache used by that policy.
            await fresh_claim_guard._set(
                db,
                fresh_claim_guard._key(fresh_claim_guard.SIGNER_PREFIX, WALLET_A),
                {"address": WALLET_A, "trusted": True, "checked_at": 100},
            )
            await db.commit()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def _authorization(
        self, *, now=1000, spot_id=None, device=None, lat=51.5, long=-0.1, accuracy=5
    ):
        fixed = claim_authorization.canonical_location(lat, long, accuracy)
        challenge_id = f"{now + 90:012d}.abcdefghijklmnopqrstuvwxyzABCDEFGH"
        key = claim_security._claim_authorization_key(challenge_id)
        message = claim_authorization.build_message(
            environment=claim_security._claim_environment(),
            network=claim_security._claim_network(),
            spot_id=spot_id or self.spot_id,
            receiving_wallet=WALLET_A,
            device=device or self.device,
            latitude_e6=fixed[0],
            longitude_e6=fixed[1],
            accuracy_cm=fixed[2],
            nonce="a" * 64,
            issued_at=now,
            expires_at=now + 90,
        )
        record = {
            "version": 2,
            "status": "issued",
            "message": message,
            "message_hash": claim_security._sha256_text(message),
            "nonce_hash": "n",
            "spot_id": spot_id or self.spot_id,
            "action": "claim",
            "device_id_hash": device or self.device,
            "receiving_wallet": WALLET_A,
            "latitude_e6": fixed[0],
            "longitude_e6": fixed[1],
            "accuracy_cm": fixed[2],
            "environment": claim_security._claim_environment(),
            "network": claim_security._claim_network(),
            "issued_at": now,
            "expires_at": now + 90,
        }
        async with schema.get_db() as db:
            await claim_security._metadata_set(db, key, record)
            await db.commit()
        return challenge_id, key

    async def _submit(
        self,
        challenge_id,
        *,
        signer=WALLET_A,
        spot_id=None,
        device=None,
        lat=51.5,
        long=-0.1,
        accuracy=5,
        verifier=None,
        patch_runtime=True,
    ):
        body = json.dumps(
            {
                "device_id_hash": device or self.device,
                "wallet_available": True,
                "lat": lat,
                "long": long,
                "accuracy": accuracy,
                "claim_authorization": {
                    "challenge_id": challenge_id,
                    "public_key": "1" * 64,
                    "signature": "2" * 128,
                },
            }
        ).encode()
        called = 0

        async def app(_scope, _receive, send):
            nonlocal called
            called += 1
            async with schema.get_db() as db:
                claim_id = await db_access.create_claim(
                    db,
                    spot_id=spot_id or self.spot_id,
                    user_id=self.user_id,
                    lat=lat,
                    long=long,
                    accuracy=1,
                    payout_address=WALLET_A,
                )
                await db.commit()
            response = json.dumps({"ok": True, "claim": {"id": claim_id}}).encode()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": response})

        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        messages = []

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": f"/api/spot/{spot_id or self.spot_id}/claim",
            "headers": [
                (
                    b"cookie",
                    f"{claim_security.SESSION_COOKIE_NAME}={self.token}".encode(),
                )
            ],
            "client": ("127.0.0.1", 1),
        }
        verify = verifier or mock.AsyncMock(return_value=signer)

        async def invoke():
            await claim_security.guard_http_request(app, scope, receive, send)

        if patch_runtime:
            with (
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
                mock.patch.object(claim_security, "_INSTALLED", True),
                mock.patch.object(claim_security, "_verify_signature", verify),
                mock.patch.object(
                    db_access, "get_unixepoch", mock.AsyncMock(return_value=1000)
                ),
            ):
                await invoke()
        else:
            await invoke()
        status = next(
            item["status"] for item in messages if item["type"] == "http.response.start"
        )
        return called, status, verify

    async def test_valid_authorization_creates_once_and_retires_challenge(self):
        challenge, key = await self._authorization()
        called, status, _ = await self._submit(challenge)
        self.assertEqual((called, status), (1, 200))
        async with schema.get_db() as db:
            self.assertIsNone(await claim_security._metadata_get(db, key))
            gps = await fresh_claim_guard._get(
                db, fresh_claim_guard._key(fresh_claim_guard.GPS_PREFIX, self.user_id)
            )
            activity = await fresh_claim_guard._get(
                db, fresh_claim_guard._key(fresh_claim_guard.ACTIVITY_PREFIX, self.user_id)
            )
            self.assertEqual((gps["last_lat"], gps["last_long"]), (51.5, -0.1))
            self.assertEqual(activity["first_public_claim_attempt_at"], 1000)
            cur = await db.execute(
                f"SELECT COUNT(*) AS n FROM {schema.CLAIM_TABLE_NAME}"
            )
            self.assertEqual((await cur.fetchone())["n"], 1)
        called, status, _ = await self._submit(challenge)
        self.assertEqual((called, status), (0, 409))

    async def test_find_spots_anchor_then_signed_claim_does_not_add_evidence(self):
        async with schema.get_db() as db:
            await fresh_claim_guard.record_gps_observation(
                db, user_id=self.user_id, ip=None, lat=51.5, long=-0.1, now=995
            )
        challenge, _ = await self._authorization()
        called, status, _ = await self._submit(challenge)
        self.assertEqual((called, status), (1, 200))
        async with schema.get_db() as db:
            gps = await fresh_claim_guard._get(
                db, fresh_claim_guard._key(fresh_claim_guard.GPS_PREFIX, self.user_id)
            )
        self.assertEqual(int(gps.get("same_ip_contradiction_count") or 0), 0)
        self.assertEqual(gps["last_observed_at"], 995)

    async def test_wallet_change_rejects_before_claim(self):
        challenge, _ = await self._authorization()
        called, status, _ = await self._submit(challenge, signer=WALLET_B)
        self.assertEqual((called, status), (0, 409))

    async def test_each_location_field_and_spot_tamper_rejects(self):
        cases = (
            {"lat": 51.6},
            {"long": -0.2},
            {"accuracy": 6},
            {"spot_id": self.spot_id + 1},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                challenge, _ = await self._authorization()
                called, status, verify = await self._submit(challenge, **changes)
                self.assertEqual((called, status), (0, 409))
                verify.assert_not_awaited()

    async def test_device_change_rejects_before_verifier(self):
        challenge, _ = await self._authorization()
        called, status, verify = await self._submit(challenge, device="b" * 64)
        self.assertEqual((called, status), (0, 401))
        verify.assert_not_awaited()

    async def test_expired_authorization_rejects(self):
        challenge, _ = await self._authorization(now=900)
        called, status, verify = await self._submit(challenge)
        self.assertEqual((called, status), (0, 409))
        verify.assert_not_awaited()

    async def test_invalid_signature_gets_only_one_expensive_attempt(self):
        challenge, _ = await self._authorization()
        verify = mock.AsyncMock(side_effect=ValueError("bad signature"))
        first = await self._submit(challenge, verifier=verify)
        second = await self._submit(challenge, verifier=verify)
        self.assertEqual((first[0], first[1], second[0], second[1]), (0, 401, 0, 409))
        self.assertEqual(verify.await_count, 1)

    async def test_concurrent_replay_creates_at_most_one_claim(self):
        challenge, _ = await self._authorization()
        verify = mock.AsyncMock(return_value=WALLET_A)
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(claim_security, "_INSTALLED", True),
            mock.patch.object(claim_security, "_verify_signature", verify),
            mock.patch.object(
                db_access, "get_unixepoch", mock.AsyncMock(return_value=1000)
            ),
        ):
            results = await asyncio.gather(
                self._submit(challenge, verifier=verify, patch_runtime=False),
                self._submit(challenge, verifier=verify, patch_runtime=False),
            )
        self.assertEqual(sum(called for called, _status, _verify in results), 1)

    async def test_cleanup_is_bounded_and_preserves_live_or_recent_processing(self):
        async with schema.get_db() as db:
            for index in range(200):
                expiry = 100 + index
                challenge = f"{expiry:012d}.{'x' * 30}{index:04d}"
                await claim_security._metadata_set(
                    db,
                    claim_security._claim_authorization_key(challenge),
                    {
                        "status": "issued",
                        "expires_at": expiry,
                    },
                )
            live = f"{2000:012d}.{'y' * 34}"
            processing = f"{1090:012d}.{'z' * 34}"
            await claim_security._metadata_set(
                db,
                claim_security._claim_authorization_key(live),
                {"status": "issued", "expires_at": 2000},
            )
            await claim_security._metadata_set(
                db,
                claim_security._claim_authorization_key(processing),
                {"status": "processing", "expires_at": 1090},
            )
            for _ in range(5):
                removed = await claim_security._cleanup_claim_authorizations(
                    db, now=1000
                )
                self.assertLessEqual(
                    removed, claim_security.CLAIM_AUTHORIZATION_CLEANUP_BATCH
                )
            await db.commit()
            cur = await db.execute(
                f"SELECT {schema.APP_METADATA_KEY} FROM {schema.APP_METADATA_TABLE_NAME} WHERE {schema.APP_METADATA_KEY} LIKE ?",
                (f"{claim_security.CLAIM_AUTHORIZATION_PREFIX}%",),
            )
            keys = {row["key"] for row in await cur.fetchall()}
        self.assertEqual(
            keys,
            {
                claim_security._claim_authorization_key(live),
                claim_security._claim_authorization_key(processing),
            },
        )

    async def test_durable_issuance_bucket_bounds_one_device(self):
        async with schema.get_db() as db:
            results = []
            for _ in range(claim_security.CLAIM_AUTHORIZATION_RATE_PER_DEVICE + 3):
                allowed, _retry = await claim_security._rate_limit_bucket(
                    db,
                    key=f"{claim_security.RATE_PREFIX}claim_authorization:device:{self.device}",
                    now=1000,
                    window_seconds=claim_security.CLAIM_AUTHORIZATION_RATE_WINDOW_SECONDS,
                    limit=claim_security.CLAIM_AUTHORIZATION_RATE_PER_DEVICE,
                )
                results.append(allowed)
            await db.commit()
        self.assertEqual(
            results.count(True), claim_security.CLAIM_AUTHORIZATION_RATE_PER_DEVICE
        )
        self.assertEqual(results[-3:], [False, False, False])


if __name__ == "__main__":
    unittest.main()
