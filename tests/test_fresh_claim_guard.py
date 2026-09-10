from __future__ import annotations

import hashlib
import tempfile
from unittest import IsolatedAsyncioTestCase, mock

import cache
import constants as const
import database as schema
import db_access
import fresh_claim_guard as guard


class FreshClaimGuardTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self.db_context = schema.get_db()
        self.db = await self.db_context.__aenter__()
        await guard.ensure_rollout_marker(self.db)
        self.user_id = await db_access.create_user(
            self.db, device_id_hash=hashlib.sha256(b"new-user").hexdigest()
        )
        await self.db.commit()
        self.user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        self.now = await db_access.get_unixepoch(self.db)
        self.address = const.DEV_PLATFORM_FEE_ADDRESS

    async def asyncTearDown(self):
        await self.db_context.__aexit__(None, None, None)
        schema.DB_PATH = self.old_path
        self.tmp.close()

    def tx(self, age: int) -> dict:
        return {"hash": "ab" * 32, "blockNumber": 10, "executionResult": True,
                "timestamp": (self.now - age) * 1000}

    async def test_new_signer_requires_old_not_merely_recent_activity(self):
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address",
                               mock.AsyncMock(return_value={"data": [self.tx(2 * 86400)]})):
            result = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now)
        self.assertFalse(result["trusted"])

    async def test_old_signer_is_permanently_cached(self):
        rpc = mock.AsyncMock(return_value={"data": [self.tx(40 * 86400)]})
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            first = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now)
            second = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now + 86400)
        self.assertTrue(first["trusted"] and second["trusted"])
        self.assertEqual(rpc.await_count, 1)

    async def test_old_account_bypasses_signer_lookup(self):
        await self.db.execute(
            f"UPDATE {schema.USER_TABLE_NAME} SET {schema.USER_CREATED_AT}=? WHERE {schema.USER_ID}=?",
            (self.now - 31 * 86400, self.user_id))
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        rpc = mock.AsyncMock()
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            result = await guard.signer_or_account_trusted(
                self.db, user=user, signer_address=self.address, now=self.now)
        self.assertTrue(result["trusted"])
        rpc.assert_not_awaited()

    async def test_history_failure_is_not_cached_as_no_history(self):
        rpc = mock.AsyncMock(side_effect=RuntimeError("offline"))
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            for _ in range(2):
                result = await guard.signer_or_account_trusted(
                    self.db, user=self.user, signer_address=self.address, now=self.now)
        self.assertFalse(result["trusted"])
        self.assertEqual(rpc.await_count, 2)

    async def test_negative_cache_refreshes(self):
        rpc = mock.AsyncMock(side_effect=[{"data": []}, {"data": [self.tx(40 * 86400)]}])
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            first = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now)
            cached = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now + 1)
            refreshed = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address,
                now=self.now + const.CLAIM_SIGNER_HISTORY_NEGATIVE_CACHE_SECONDS + 1)
        self.assertFalse(first["trusted"] or cached["trusted"])
        self.assertTrue(refreshed["trusted"])
        self.assertEqual(rpc.await_count, 2)

    async def location(self, *, country="Canada", lat=43.65, long=-79.38, ip="8.8.8.8"):
        return await guard.first_location_decision(
            self.db, user=self.user, ip=ip, gps_lat=lat, gps_long=long,
            gps_country=country, now=self.now)

    async def test_matching_and_same_country_locations_verify_once(self):
        provider = mock.AsyncMock(return_value={"latitude": 1, "longitude": 1,
                                                "country_name": "Canada"})
        with mock.patch.object(guard, "lookup_ip_location", provider):
            result = await self.location()
            again = await self.location(lat=-30, long=120)
        self.assertTrue(result["allowed"] and again["allowed"])
        provider.assert_awaited_once()

    async def test_nearby_cross_border_is_allowed(self):
        with mock.patch.object(guard, "lookup_ip_location", mock.AsyncMock(return_value={
                "latitude": 43.0, "longitude": -79.0, "country_name": "United States"})):
            self.assertTrue((await self.location())["allowed"])

    async def test_mismatch_cooldown_deduplicates_and_escalates_independent_events(self):
        provider = mock.AsyncMock(return_value={"latitude": 28.6, "longitude": 77.2,
                                                "country_name": "India"})
        key = guard._key(guard.LOCATION_PREFIX, self.user_id)
        with mock.patch.object(guard, "lookup_ip_location", provider):
            first = await self.location()
            duplicate = await self.location()
            self.assertFalse(first["allowed"] or duplicate["allowed"])
            self.assertEqual(provider.await_count, 1)
            for expected in (2, 3):
                state = await guard._get(self.db, key)
                state["retry_at"] = self.now
                await guard._set(self.db, key, state)
                result = await self.location()
                state = await guard._get(self.db, key)
                self.assertEqual(state["mismatch_count"], expected)
        self.assertTrue(result["banned"])
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        self.assertEqual(user[schema.USER_STATUS], const.USER_STATUS_BANNED)

    async def test_provider_failure_and_private_ip_are_unknown_not_strikes(self):
        provider = mock.AsyncMock(side_effect=TimeoutError())
        with mock.patch.object(guard, "lookup_ip_location", provider):
            result = await self.location()
        self.assertEqual(result["reason"], "first_location_provider_unavailable")
        state = await guard._get(self.db, guard._key(guard.LOCATION_PREFIX, self.user_id))
        self.assertEqual(state["mismatch_count"], 0)
        self.assertIsNone(guard.genuine_client_ip({"client": ("127.0.0.1", 123)}))

    async def test_password_spot_bypasses_only_this_guard(self):
        result = await guard.public_claim_decision(
            self.db, user_id=self.user_id, signer_address="not even inspected",
            spot={schema.SPOT_USE_PASSWORD: 1}, ip=None, lat=0, long=0)
        self.assertTrue(result["allowed"])

    async def test_legacy_user_is_grandfathered(self):
        await self.db.execute(
            f"UPDATE {schema.APP_METADATA_TABLE_NAME} SET {schema.APP_METADATA_VALUE}=? "
            f"WHERE {schema.APP_METADATA_KEY}=?",
            ('{"activated_at":%d,"legacy_max_user_id":%d}' % (self.now, self.user_id), guard.ROLLOUT_KEY))
        result = await guard.first_location_decision(
            self.db, user=self.user, ip=None, gps_lat=0, gps_long=0,
            gps_country=None, now=self.now)
        self.assertTrue(result["allowed"])
