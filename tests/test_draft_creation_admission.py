from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import draft_creation_admission as admission
import wallet


class DraftCreationAdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await schema.init_db()
        async with schema.get_db() as db:
            async with db_access.transaction(db):
                self.user_id = await db_access.create_user(db, device_id_hash="a" * 64)

    async def asyncTearDown(self):
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def _reserve(self):
        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                return await admission.reserve(db, user_id=self.user_id)

    async def test_deleted_or_absent_drafts_still_reach_user_rolling_limit(self):
        with mock.patch.object(const, "DRAFT_CREATION_PER_USER_LIMIT", 3):
            for _ in range(3):
                reservation_id, _ = await self._reserve()
                async with schema.get_db() as db:
                    async with db_access.transaction(db, immediate=True):
                        await admission.consume(db, reservation_id=reservation_id, user_id=self.user_id)
            with self.assertRaises(admission.DraftAdmissionLimited):
                await self._reserve()

    async def test_failed_derivation_reservation_can_be_released(self):
        reservation_id, _ = await self._reserve()
        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                await admission.release(db, reservation_id=reservation_id, user_id=self.user_id)
        with mock.patch.object(const, "DRAFT_CREATION_PER_USER_LIMIT", 1):
            replacement, _ = await self._reserve()
        self.assertNotEqual(replacement, reservation_id)

    async def test_concurrent_requests_cannot_bypass_user_quota(self):
        async def attempt():
            try:
                await self._reserve()
                return True
            except admission.DraftAdmissionLimited:
                return False

        with mock.patch.object(const, "DRAFT_CREATION_PER_USER_LIMIT", 4):
            results = await asyncio.gather(*(attempt() for _ in range(12)))
        self.assertEqual(sum(results), 4)

    async def test_global_quota_applies_across_users(self):
        async with schema.get_db() as db:
            async with db_access.transaction(db):
                other = await db_access.create_user(db, device_id_hash="b" * 64)
        with mock.patch.object(const, "DRAFT_CREATION_GLOBAL_LIMIT", 2):
            await self._reserve()
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    await admission.reserve(db, user_id=other)
            with self.assertRaises(admission.DraftAdmissionLimited):
                await self._reserve()

    async def test_derivation_is_not_called_after_failed_admission(self):
        with mock.patch.object(const, "DRAFT_CREATION_PER_USER_LIMIT", 1):
            await self._reserve()
            derive = mock.AsyncMock()
            with mock.patch.object(admission, "derive", derive):
                with self.assertRaises(admission.DraftAdmissionLimited):
                    await self._reserve()
            derive.assert_not_awaited()

    async def test_derivation_concurrency_is_bounded_and_addresses_distinct(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def derive(index):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return wallet.DerivedSpotAddress(f"address-{index}", index, f"path-{index}", 1)

        old = admission._DERIVATION_SEMAPHORE
        admission._DERIVATION_SEMAPHORE = asyncio.Semaphore(2)
        try:
            with mock.patch.object(wallet, "derive_spot_deposit_address", side_effect=derive):
                records = await asyncio.gather(*(admission.derive(key_index=i) for i in range(8)))
        finally:
            admission._DERIVATION_SEMAPHORE = old
        self.assertLessEqual(peak, 2)
        self.assertEqual(len({record.address for record in records}), 8)

    async def test_external_derivation_runs_without_sqlite_writer_lock(self):
        def derive(index):
            connection = sqlite3.connect(schema.DB_PATH, timeout=0.1)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            finally:
                connection.close()
            return wallet.DerivedSpotAddress("address", index, "path", 1)

        with mock.patch.object(wallet, "derive_spot_deposit_address", side_effect=derive):
            record = await admission.derive(key_index=7)
        self.assertEqual(record.key_index, 7)


if __name__ == "__main__":
    unittest.main()
