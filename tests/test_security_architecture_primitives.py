from __future__ import annotations

import unittest

import claim_http
import claim_identity
import database as schema
import db_access
import security_metadata
from database import get_db, init_db


class SecurityMetadataTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()

    async def test_json_helpers_do_not_commit_callers_transaction(self):
        async with get_db() as db:
            await db.execute("BEGIN IMMEDIATE")
            await security_metadata.set_json(db, "security-test", {"answer": 42})
            self.assertTrue(db.in_transaction)
            self.assertEqual(await security_metadata.get_json(db, "security-test"), {"answer": 42})
            await db.rollback()
        async with get_db() as db:
            self.assertIsNone(await security_metadata.get_json(db, "security-test"))

    async def test_rate_bucket_is_bounded_and_requires_caller_serialization(self):
        async with get_db() as db:
            async with db_access.transaction(db, immediate=True):
                await security_metadata.delete(db, "security-test-rate-bucket")
                for now in (10, 11, 12):
                    allowed, _ = await security_metadata.admit_timestamp(
                        db, key="security-test-rate-bucket", now=now, window_seconds=60, limit=2
                    )
                    self.assertEqual(allowed, now != 12)
                self.assertEqual(await security_metadata.get_json(db, "security-test-rate-bucket"), [10, 11])

    async def test_malformed_ephemeral_json_can_be_deleted_fail_closed(self):
        async with get_db() as db:
            async with db_access.transaction(db, immediate=True):
                await db.execute(
                    f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} VALUES (?, ?)",
                    ("bad-security-json", "{"),
                )
                self.assertIsNone(await security_metadata.get_json(db, "bad-security-json"))
                row = await db.execute_fetchall(
                    f"SELECT 1 FROM {schema.APP_METADATA_TABLE_NAME} WHERE {schema.APP_METADATA_KEY} = ?",
                    ("bad-security-json",),
                )
                self.assertEqual(row, [])


class ClaimTrustPrimitiveTest(unittest.TestCase):
    def test_device_identifier_is_normalised_continuity_data(self):
        value = "AB" * 32
        self.assertEqual(claim_identity.clean_device_identifier(value), value.lower())
        with self.assertRaises(ValueError):
            claim_identity.clean_device_identifier("not-authentication")

    def test_asgi_body_replay_is_exactly_once(self):
        async def exercise():
            receive = claim_http.replay_receive(b'{"ok":true}')
            first = await receive()
            second = await receive()
            return first, second

        import asyncio
        first, second = asyncio.run(exercise())
        self.assertEqual(first["body"], b'{"ok":true}')
        self.assertEqual(second["type"], "http.disconnect")
