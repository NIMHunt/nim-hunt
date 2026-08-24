from __future__ import annotations

import json
import tempfile
import unittest

import cache
import claim_security
import claim_security_maintenance
import database as schema


class ClaimSecurityMaintenanceTest(unittest.IsolatedAsyncioTestCase):
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

    async def _put(self, db, key: str, value) -> None:
        await db.execute(
            f"""
            INSERT OR REPLACE INTO {schema.APP_METADATA_TABLE_NAME} (
                {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}
            ) VALUES (?, ?);
            """,
            (key, json.dumps(value, separators=(",", ":"), sort_keys=True)),
        )

    async def _get(self, db, key: str):
        cur = await db.execute(
            f"SELECT {schema.APP_METADATA_VALUE} AS value "
            f"FROM {schema.APP_METADATA_TABLE_NAME} "
            f"WHERE {schema.APP_METADATA_KEY} = ?;",
            (key,),
        )
        row = await cur.fetchone()
        return None if row is None else json.loads(row["value"])

    async def test_cleanup_removes_expired_challenge_and_session(self):
        async with schema.get_db() as db:
            now_row = await db.execute_fetchall("SELECT unixepoch() AS now;")
            now = int(now_row[0]["now"])
            challenge_key = f"{claim_security.CHALLENGE_PREFIX}old"
            session_key = f"{claim_security.SESSION_PREFIX}old"
            await self._put(db, challenge_key, {"expires_at": now - 1})
            await self._put(db, session_key, {"expires_at": now - 1})
            await db.commit()

        result = await claim_security_maintenance.cleanup_expired_claim_security_metadata()
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted_count"], 2)

        async with schema.get_db() as db:
            self.assertIsNone(await self._get(db, challenge_key))
            self.assertIsNone(await self._get(db, session_key))

    async def test_cleanup_keeps_live_session_and_durable_claim_record(self):
        async with schema.get_db() as db:
            now_row = await db.execute_fetchall("SELECT unixepoch() AS now;")
            now = int(now_row[0]["now"])
            session_key = f"{claim_security.SESSION_PREFIX}live"
            claim_key = f"{claim_security.CLAIM_RECORD_PREFIX}42"
            await self._put(db, session_key, {"expires_at": now + 3600})
            await self._put(db, claim_key, {"claim_id": 42, "manual_review": False})
            await db.commit()

        await claim_security_maintenance.cleanup_expired_claim_security_metadata()

        async with schema.get_db() as db:
            self.assertIsNotNone(await self._get(db, session_key))
            self.assertIsNotNone(await self._get(db, claim_key))

    async def test_cleanup_prunes_rate_bucket_instead_of_dropping_live_entries(self):
        async with schema.get_db() as db:
            now_row = await db.execute_fetchall("SELECT unixepoch() AS now;")
            now = int(now_row[0]["now"])
            rate_key = f"{claim_security.RATE_PREFIX}challenge:device:test"
            old = now - claim_security.AUTH_RATE_WINDOW_SECONDS - 5
            live = now - 5
            await self._put(db, rate_key, [old, live])
            await db.commit()

        result = await claim_security_maintenance.cleanup_expired_claim_security_metadata()
        self.assertEqual(result["updated_count"], 1)

        async with schema.get_db() as db:
            self.assertEqual(await self._get(db, rate_key), [live])

    async def test_durable_claim_rows_cannot_starve_ephemeral_cleanup_limit(self):
        async with schema.get_db() as db:
            now_row = await db.execute_fetchall("SELECT unixepoch() AS now;")
            now = int(now_row[0]["now"])
            for claim_id in range(20):
                await self._put(
                    db,
                    f"{claim_security.CLAIM_RECORD_PREFIX}{claim_id:04d}",
                    {"claim_id": claim_id, "manual_review": False},
                )
            session_key = f"{claim_security.SESSION_PREFIX}expired-after-durable-rows"
            await self._put(db, session_key, {"expires_at": now - 1})
            await db.commit()

        result = await claim_security_maintenance.cleanup_expired_claim_security_metadata(limit=1)
        self.assertEqual(result["checked_count"], 1)
        self.assertEqual(result["deleted_count"], 1)

        async with schema.get_db() as db:
            self.assertIsNone(await self._get(db, session_key))
            self.assertIsNotNone(
                await self._get(db, f"{claim_security.CLAIM_RECORD_PREFIX}0000")
            )

    async def test_live_first_page_cannot_starve_expired_rows_after_limit(self):
        async with schema.get_db() as db:
            now_row = await db.execute_fetchall("SELECT unixepoch() AS now;")
            now = int(now_row[0]["now"])

            # More live rows than one cleanup pass can inspect. Before cursor
            # pagination, every pass selected these same first 500 keys forever.
            for index in range(501):
                await self._put(
                    db,
                    f"{claim_security.CHALLENGE_PREFIX}{index:04d}",
                    {"expires_at": now + 3600},
                )

            expired_key = f"{claim_security.CHALLENGE_PREFIX}9999-expired"
            await self._put(db, expired_key, {"expires_at": now - 1})
            await db.commit()

        first = await claim_security_maintenance.cleanup_expired_claim_security_metadata(
            limit=500
        )
        self.assertEqual(first["checked_count"], 500)
        self.assertEqual(first["deleted_count"], 0)

        async with schema.get_db() as db:
            self.assertIsNotNone(await self._get(db, expired_key))
            self.assertEqual(
                await self._get(db, claim_security_maintenance.CLEANUP_CURSOR_KEY),
                f"{claim_security.CHALLENGE_PREFIX}0499",
            )

        second = await claim_security_maintenance.cleanup_expired_claim_security_metadata(
            limit=500
        )
        self.assertEqual(second["checked_count"], 2)
        self.assertEqual(second["deleted_count"], 1)

        async with schema.get_db() as db:
            self.assertIsNone(await self._get(db, expired_key))
            self.assertIsNotNone(
                await self._get(db, f"{claim_security.CHALLENGE_PREFIX}0500")
            )

        third = await claim_security_maintenance.cleanup_expired_claim_security_metadata(
            limit=500
        )
        self.assertTrue(third["wrapped"])
        self.assertEqual(third["checked_count"], 500)


if __name__ == "__main__":
    unittest.main()
