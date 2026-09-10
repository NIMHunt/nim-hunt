from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
