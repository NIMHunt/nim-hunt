from __future__ import annotations

import hashlib
import tempfile
import unittest

import admin_store
import cache
import constants as const
import database as schema
import db_access
import security_events


class SecurityEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self.context = schema.get_db()
        self.db = await self.context.__aenter__()
        self.user_id = await db_access.create_user(
            self.db, device_id_hash=hashlib.sha256(b"security-event-user").hexdigest()
        )
        await self.db.commit()

    async def asyncTearDown(self):
        await self.context.__aexit__(None, None, None)
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def _temporary(self, *, created_at: int, expires_at: int, metadata=None):
        return await security_events.record_decision(
            self.db, user_id=self.user_id, code="fresh_account_location_anomaly",
            decision_type=security_events.DECISION_TEMPORARY_RESTRICTION,
            created_at=created_at, expires_at=expires_at, metadata=metadata,
        )

    async def test_restriction_episode_is_deduplicated_and_later_episode_is_recorded(self):
        self.assertIsNotNone(await self._temporary(created_at=100, expires_at=200))
        self.assertIsNone(await self._temporary(created_at=101, expires_at=200))
        self.assertIsNotNone(await self._temporary(created_at=300, expires_at=400))
        rows = await self.db.execute_fetchall(
            f"SELECT * FROM {security_events.TABLE_NAME} ORDER BY id"
        )
        self.assertEqual(len(rows), 2)

    async def test_automatic_ban_and_current_moderation_status_are_represented(self):
        now = await db_access.get_unixepoch(self.db)
        await db_access.set_user_status_to_banned(self.db, user_id=self.user_id)
        await security_events.record_decision(
            self.db, user_id=self.user_id, code="impossible_claim_travel",
            decision_type=security_events.DECISION_AUTOMATIC_BAN, created_at=now,
        )
        await self.db.commit()
        overview = await admin_store.security_overview(self.db)
        event = overview["events"][0]
        self.assertEqual(event["decision_type"], "automatic_ban")
        self.assertEqual(event["user_status"], const.USER_STATUS_BANNED)
        self.assertEqual(overview["automatic_bans_24h"], 1)

    async def test_expired_restriction_is_not_current(self):
        now = await db_access.get_unixepoch(self.db)
        await self._temporary(created_at=now - 20, expires_at=now - 1)
        await self.db.commit()
        overview = await admin_store.security_overview(self.db)
        self.assertEqual(overview["restricted_now"], 0)
        self.assertEqual(overview["active"], [])

    async def test_sensitive_values_cannot_be_persisted_as_diagnostics(self):
        await self._temporary(
            created_at=100, expires_at=200,
            metadata={"gps": "51.5,-0.1", "ip": "203.0.113.4",
                      "wallet": "NQ00 SECRET", "signal_count": 2},
        )
        row = await (await self.db.execute(
            f"SELECT metadata FROM {security_events.TABLE_NAME}"
        )).fetchone()
        self.assertEqual(row["metadata"], '{"signal_count":2}')

    async def test_event_history_is_bounded(self):
        await self._temporary(created_at=1, expires_at=2)
        now = security_events.RETENTION_SECONDS + 100
        await self._temporary(created_at=now, expires_at=now + 10)
        count = await (await self.db.execute(
            f"SELECT COUNT(*) FROM {security_events.TABLE_NAME}"
        )).fetchone()
        self.assertEqual(count[0], 1)


if __name__ == "__main__":
    unittest.main()
