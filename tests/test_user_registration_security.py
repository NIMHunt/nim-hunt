from __future__ import annotations

import unittest
from unittest import mock

import aiosqlite
from starlette.requests import Request

import constants as const
import database as schema
import db_access
import user_registration_security


class UserRegistrationSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute(schema.CREATE_APP_METADATA_TABLE)
        await self.db.execute(schema.CREATE_USER_TABLE)

    async def asyncTearDown(self):
        await self.db.close()

    @staticmethod
    def _request(ip: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/home/session",
                "headers": [(b"x-real-ip", ip.encode("ascii"))],
                "client": ("100.64.0.2", 443),
                "server": ("testserver", 443),
                "scheme": "https",
                "query_string": b"",
            }
        )

    async def test_public_registration_limit_blocks_fresh_spoofed_devices(self):
        request = self._request("203.0.113.10")
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(user_registration_security, "HOURLY_LIMIT", 2),
            mock.patch.object(user_registration_security, "DAILY_LIMIT", 10),
        ):
            first = await user_registration_security.get_or_create_user(
                self.db, request=request, device_id_hash="a" * 64
            )
            second = await user_registration_security.get_or_create_user(
                self.db, request=request, device_id_hash="b" * 64
            )
            with self.assertRaises(user_registration_security.RegistrationRateLimited):
                await user_registration_security.get_or_create_user(
                    self.db, request=request, device_id_hash="c" * 64
                )

        self.assertEqual(first, (1, True))
        self.assertEqual(second, (2, True))
        self.assertEqual(await db_access.count_users(self.db), 2)

    async def test_existing_device_is_not_blocked_by_registration_limit(self):
        request = self._request("198.51.100.20")
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(user_registration_security, "HOURLY_LIMIT", 1),
            mock.patch.object(user_registration_security, "DAILY_LIMIT", 1),
        ):
            created = await user_registration_security.get_or_create_user(
                self.db, request=request, device_id_hash="d" * 64
            )
            existing = await user_registration_security.get_or_create_user(
                self.db, request=request, device_id_hash="d" * 64
            )

        self.assertEqual(created, (1, True))
        self.assertEqual(existing, (1, False))

    async def test_limits_are_separate_per_validated_source_ip(self):
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(user_registration_security, "HOURLY_LIMIT", 1),
            mock.patch.object(user_registration_security, "DAILY_LIMIT", 1),
        ):
            first = await user_registration_security.get_or_create_user(
                self.db,
                request=self._request("192.0.2.1"),
                device_id_hash="e" * 64,
            )
            second = await user_registration_security.get_or_create_user(
                self.db,
                request=self._request("192.0.2.2"),
                device_id_hash="f" * 64,
            )

        self.assertEqual(first, (1, True))
        self.assertEqual(second, (2, True))


if __name__ == "__main__":
    unittest.main()
