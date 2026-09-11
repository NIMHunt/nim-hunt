from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

from starlette.requests import Request

import cache
import claim_security
import database as schema
import db_access
import user_registration_security as registration


class UserRegistrationSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def test_home_and_wallet_verification_share_creation_quota(self):
        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                with (
                    mock.patch.object(registration, "SOURCE_HOURLY_LIMIT", 1),
                    mock.patch.object(registration, "SOURCE_DAILY_LIMIT", 12),
                    mock.patch.object(registration, "GLOBAL_HOURLY_LIMIT", 500),
                ):
                    first_id, created = await registration.get_or_create_public_user(
                        db, device_id_hash="a" * 64, source_network_hash="venue"
                    )
                    self.assertTrue(created)
                    with self.assertRaises(registration.RegistrationRateLimited):
                        await registration.get_or_create_public_user(
                            db, device_id_hash="b" * 64, source_network_hash="venue"
                        )

                    # Existing USERs are unaffected even after the boundary is full.
                    same_id, created = await registration.get_or_create_public_user(
                        db, device_id_hash="a" * 64, source_network_hash="venue"
                    )
        self.assertEqual(same_id, first_id)
        self.assertFalse(created)

    async def test_signed_wallet_verification_uses_registration_boundary(self):
        challenge_id = "challenge-12345"
        async with schema.get_db() as db:
            now = await db_access.get_unixepoch(db)
            await claim_security._metadata_set(
                db,
                f"{claim_security.CHALLENGE_PREFIX}{challenge_id}",
                {"device_id_hash": "c" * 64, "message": "signed challenge",
                 "created_at": now, "expires_at": now + 60},
            )
            await db.commit()

        request = Request({"type": "http", "method": "POST", "path": "/api/security/verify",
                           "headers": [], "client": ("127.0.0.1", 1234)})
        payload = claim_security.SecurityVerifyRequest(
            device_id_hash="c" * 64,
            challenge_id=challenge_id,
            public_key="d" * 64,
            signature="e" * 128,
        )
        limited = registration.RegistrationRateLimited(retry_at=123)
        with (
            mock.patch.object(claim_security, "_verify_signature", new=mock.AsyncMock(
                return_value="NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF")),
            mock.patch.object(registration, "get_or_create_public_user",
                              new=mock.AsyncMock(side_effect=limited)) as boundary,
        ):
            response = await claim_security.security_verify(payload, request)

        self.assertEqual(response.status_code, 429)
        boundary.assert_awaited_once()
        self.assertNotEqual(boundary.await_args.kwargs.get("source_network_hash"), None)

    async def test_venue_preburst_then_twenty_devices_can_register(self):
        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                ids = []
                for index in range(25):  # five hostile, then twenty attendees
                    user_id, created = await registration.get_or_create_public_user(
                        db,
                        device_id_hash=f"{index:064x}",
                        source_network_hash="shared-venue",
                    )
                    self.assertTrue(created)
                    ids.append(user_id)
        self.assertEqual(len(set(ids)), 25)

    async def test_source_ceiling_is_durable_but_existing_user_still_works(self):
        with (
            mock.patch.object(registration, "SOURCE_BURST_LIMIT", 3),
            mock.patch.object(registration, "SOURCE_HOURLY_LIMIT", 3),
        ):
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    for index in range(3):
                        await registration.get_or_create_public_user(
                            db, device_id_hash=f"{index:064x}", source_network_hash="venue"
                        )
                    with self.assertRaises(registration.RegistrationRateLimited):
                        await registration.get_or_create_public_user(
                            db, device_id_hash="f" * 64, source_network_hash="venue"
                        )
                    existing, created = await registration.get_or_create_public_user(
                        db, device_id_hash=f"{0:064x}", source_network_hash="venue"
                    )
        self.assertGreater(existing, 0)
        self.assertFalse(created)

    async def test_twenty_devices_can_complete_wallet_authentication_on_one_ip(self):
        request = Request({"type": "http", "method": "POST", "path": "/challenge",
                           "headers": [], "client": ("198.51.100.8", 1234)})
        with mock.patch.object(
            claim_security,
            "_verify_signature",
            new=mock.AsyncMock(return_value="NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"),
        ):
            for index in range(20):
                device = f"{index:064x}"
                response = await claim_security.security_challenge(
                    claim_security.SecurityDeviceRequest(device_id_hash=device), request
                )
                self.assertEqual(response.status_code, 200)
                challenge_id = json.loads(response.body)["challenge_id"]
                verified = await claim_security.security_verify(
                    claim_security.SecurityVerifyRequest(
                        device_id_hash=device,
                        challenge_id=challenge_id,
                        public_key="d" * 64,
                        signature="e" * 128,
                    ),
                    request,
                )
                self.assertEqual(verified.status_code, 200)


if __name__ == "__main__":
    unittest.main()
