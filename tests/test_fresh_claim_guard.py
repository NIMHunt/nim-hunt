from __future__ import annotations

import asyncio
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
        await guard.ensure_activity_rollout_marker(self.db)
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

    async def test_old_account_without_active_days_does_not_bypass_signer_lookup(self):
        await self.db.execute(
            f"UPDATE {schema.USER_TABLE_NAME} SET {schema.USER_CREATED_AT}=? WHERE {schema.USER_ID}=?",
            (self.now - 31 * 86400, self.user_id))
        await self.db.commit()
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        rpc = mock.AsyncMock(return_value={"data": []})
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            result = await guard.signer_or_account_trusted(
                self.db, user=user, signer_address=self.address, now=self.now)
        self.assertFalse(result["trusted"])
        rpc.assert_awaited_once()

    async def test_old_account_with_two_earlier_active_days_is_trusted(self):
        await self.db.execute(
            f"UPDATE {schema.USER_TABLE_NAME} SET {schema.USER_CREATED_AT}=? WHERE {schema.USER_ID}=?",
            (self.now - 31 * 86400, self.user_id))
        await self.db.commit()
        await guard.record_meaningful_activity(self.db, user_id=self.user_id,
                                               now=self.now - 2 * 86400)
        await guard.record_meaningful_activity(self.db, user_id=self.user_id,
                                               now=self.now - 86400)
        # A second request on one of those dates cannot manufacture a third day.
        await guard.record_meaningful_activity(self.db, user_id=self.user_id,
                                               now=self.now - 86400 + 60)
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        rpc = mock.AsyncMock()
        result = await guard.signer_or_account_trusted(
            self.db, user=user, signer_address=self.address, now=self.now)
        self.assertTrue(result["trusted"])
        rpc.assert_not_awaited()

    async def test_same_ip_gps_policy_and_bounded_first_presence(self):
        outside = mock.AsyncMock(return_value=False)
        with mock.patch.object(guard, "_inside_active_public_spot", outside):
            first = await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=0, long=0,
                now=self.now)
            consistent = await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=0.001, long=0.001,
                now=self.now + 60)
            plausible = await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=2, long=2,
                now=self.now + 8 * 3600)
            implausible = await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=40, long=40,
                now=self.now + 8 * 3600 + 1)
            duplicate = await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=40, long=40,
                now=self.now + 8 * 3600 + 1)
        self.assertFalse(first["first_inside_public_spot"])
        self.assertFalse(consistent["contradiction"] or plausible["contradiction"])
        self.assertTrue(implausible["contradiction"])
        self.assertFalse(duplicate["contradiction"])
        state = await guard._get(self.db, guard._key(guard.GPS_PREFIX, self.user_id))
        self.assertTrue(state["ordinary_presence_before_reward"])
        self.assertEqual(state["same_ip_contradiction_count"], 1)

    async def test_first_inside_and_shared_ip_are_user_scoped(self):
        other = await db_access.create_user(
            self.db, device_id_hash=hashlib.sha256(b"other").hexdigest())
        await self.db.commit()
        with mock.patch.object(guard, "_inside_active_public_spot",
                               mock.AsyncMock(return_value=True)):
            first = await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=40, long=40,
                now=self.now)
            other_first = await guard.record_gps_observation(
                self.db, user_id=other, ip="8.8.8.8", lat=-40, long=-40,
                now=self.now)
        self.assertTrue(first["first_inside_public_spot"])
        self.assertFalse(first["contradiction"] or other_first["contradiction"])
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        self.assertEqual(user[schema.USER_STATUS], const.USER_STATUS_ACTIVE)

    async def test_concurrent_identical_jump_counts_once(self):
        with mock.patch.object(guard, "_inside_active_public_spot",
                               mock.AsyncMock(return_value=False)):
            await guard.record_gps_observation(
                self.db, user_id=self.user_id, ip="8.8.8.8", lat=0, long=0,
                now=self.now)
            async def jump():
                async with schema.get_db() as connection:
                    return await guard.record_gps_observation(
                        connection, user_id=self.user_id, ip="8.8.8.8",
                        lat=40, long=40, now=self.now + 1)
            results = await asyncio.gather(jump(), jump())
        self.assertEqual(sum(bool(item["contradiction"]) for item in results), 1)
        state = await guard._get(self.db, guard._key(guard.GPS_PREFIX, self.user_id))
        self.assertEqual(state["same_ip_contradiction_count"], 1)

    async def test_activity_rollout_grandfathers_existing_account(self):
        await self.db.execute(
            f"UPDATE {schema.USER_TABLE_NAME} SET {schema.USER_CREATED_AT}=? WHERE {schema.USER_ID}=?",
            (self.now - 31 * 86400, self.user_id))
        await guard._set(self.db, guard.ACTIVITY_ROLLOUT_KEY, {
            "activated_at": self.now, "legacy_max_user_id": self.user_id,
        })
        await self.db.commit()
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        rpc = mock.AsyncMock()
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            result = await guard.signer_or_account_trusted(
                self.db, user=user, signer_address=self.address, now=self.now)
        self.assertTrue(result["trusted"])
        rpc.assert_not_awaited()

    async def test_provider_opinion_shift_marks_ip_signal_unreliable(self):
        provider = mock.AsyncMock(side_effect=[
            {"latitude": 28.6, "longitude": 77.2, "country_name": "India"},
            {"latitude": 40.4, "longitude": -3.7, "country_name": "Spain"},
        ])
        key = guard._key(guard.LOCATION_PREFIX, self.user_id)
        with mock.patch.object(guard, "lookup_ip_location", provider):
            first = await self.location()
            state = await guard._get(self.db, key)
            self.now = int(state["retry_at"]) + 1
            shifted = await self.location()
        self.assertFalse(first["allowed"])
        self.assertTrue(shifted["allowed"])
        state = await guard._get(self.db, key)
        self.assertEqual(state["last_result"], "provider_unreliable")
        self.assertEqual(state["mismatch_count"], 1)

    async def test_history_failure_is_not_cached_as_no_history(self):
        rpc = mock.AsyncMock(side_effect=[RuntimeError("offline"),
                                         {"data": [self.tx(40 * 86400)]}])
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            first = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now)
            failed_cache = await guard._get(
                self.db, guard._key(guard.SIGNER_PREFIX, self.address)
            )
            repeated = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address, now=self.now + 1)
            recovered = await guard.signer_or_account_trusted(
                self.db, user=self.user, signer_address=self.address,
                now=self.now + const.CLAIM_SIGNER_HISTORY_FAILURE_RETRY_SECONDS + 1)
        self.assertFalse(first["trusted"] or repeated["trusted"])
        self.assertEqual(first["reason"], "signer_history_unavailable")
        self.assertEqual(failed_cache["result"], "provider_failure")
        self.assertIsNone(failed_cache["oldest_confirmed_at"])
        self.assertTrue(recovered["trusted"])
        self.assertEqual(rpc.await_count, 2)
        cached = await guard._get(self.db, guard._key(guard.SIGNER_PREFIX, self.address))
        self.assertTrue(cached["trusted"])
        self.assertNotEqual(cached["result"], "provider_failure")

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

    async def test_mismatch_cooldown_deduplicates_and_never_bans_by_itself(self):
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
                self.now = int(state["retry_at"]) + 1
                result = await self.location()
                state = await guard._get(self.db, key)
                self.assertEqual(state["mismatch_count"], expected)
        self.assertFalse(result["allowed"])
        self.assertEqual(
            state["retry_at"], self.now + const.CLAIM_FIRST_LOCATION_SECOND_COOLDOWN_SECONDS
        )
        user = await db_access.get_user_by_id(self.db, user_id=self.user_id)
        self.assertEqual(user[schema.USER_STATUS], const.USER_STATUS_ACTIVE)

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

    async def test_external_calls_run_before_immediate_write_transactions(self):
        async def rpc(*_args, **_kwargs):
            self.assertFalse(self.db.in_transaction)
            return {"data": [self.tx(40 * 86400)]}

        async def provider(_ip):
            self.assertFalse(self.db.in_transaction)
            return {"latitude": 43.65, "longitude": -79.38,
                    "country_name": "Canada"}

        with (mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc),
              mock.patch.object(guard, "lookup_ip_location", provider)):
            result = await guard.public_claim_decision(
                self.db, user_id=self.user_id, signer_address=self.address,
                spot={schema.SPOT_USE_PASSWORD: 0, schema.SPOT_COUNTRY: "Canada"},
                ip="8.8.8.8", lat=43.65, long=-79.38)
        self.assertTrue(result["allowed"])

    async def test_active_immediate_transaction_never_launches_external_io(self):
        rpc = mock.AsyncMock()
        provider = mock.AsyncMock()
        with (mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc),
              mock.patch.object(guard, "lookup_ip_location", provider),
              self.assertRaisesRegex(RuntimeError, "no active transaction")):
            async with db_access.transaction(self.db, immediate=True):
                await guard.public_claim_decision(
                    self.db, user_id=self.user_id, signer_address=self.address,
                    spot={schema.SPOT_USE_PASSWORD: 0, schema.SPOT_COUNTRY: "Canada"},
                    ip="8.8.8.8", lat=43.65, long=-79.38)
        rpc.assert_not_awaited()
        provider.assert_not_awaited()

    async def test_find_spots_style_results_survive_reopen_and_avoid_repeat_io(self):
        rpc = mock.AsyncMock(return_value={"data": [self.tx(40 * 86400)]})
        provider = mock.AsyncMock(return_value={"latitude": 43.65, "longitude": -79.38,
                                                "country_name": "Canada"})
        spot = {schema.SPOT_USE_PASSWORD: 0, schema.SPOT_COUNTRY: "Canada"}
        with (mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc),
              mock.patch.object(guard, "lookup_ip_location", provider)):
            first = await guard.public_claim_decision(
                self.db, user_id=self.user_id, signer_address=self.address,
                spot=spot, ip="8.8.8.8", lat=43.65, long=-79.38)
            await self.db_context.__aexit__(None, None, None)
            self.db_context = schema.get_db()
            self.db = await self.db_context.__aenter__()
            second = await guard.public_claim_decision(
                self.db, user_id=self.user_id, signer_address=self.address,
                spot=spot, ip="8.8.8.8", lat=43.65, long=-79.38)
        self.assertTrue(first["allowed"] and second["allowed"])
        rpc.assert_awaited_once()
        provider.assert_awaited_once()

    async def test_stale_mismatch_cannot_overwrite_concurrent_verification(self):
        key = guard._key(guard.LOCATION_PREFIX, self.user_id)

        async def provider(_ip):
            async with schema.get_db() as other:
                async with db_access.transaction(other, immediate=True):
                    await guard._set(other, key, {
                        "verified_at": self.now,
                        "decision_at": self.now,
                        "last_result": "match",
                        "mismatch_count": 0,
                    })
            return {"latitude": 28.6, "longitude": 77.2,
                    "country_name": "India"}

        with mock.patch.object(guard, "lookup_ip_location", provider):
            result = await self.location()
        self.assertTrue(result["allowed"])
        state = await guard._get(self.db, key)
        self.assertIsNotNone(state["verified_at"])
        self.assertEqual(state["mismatch_count"], 0)
