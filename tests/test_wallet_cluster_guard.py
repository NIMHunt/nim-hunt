from __future__ import annotations

import asyncio
import hashlib
import tempfile
from unittest import IsolatedAsyncioTestCase, mock

import cache
import constants as const
import database as schema
import db_access
import fresh_claim_guard
import wallet_cluster_guard as guard


class WalletClusterGuardTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()
        self.context = schema.get_db()
        self.db = await self.context.__aenter__()
        self.now = await db_access.get_unixepoch(self.db)
        self.address_patch = mock.patch.object(
            guard.trans_updater, "_validate_nimiq_address",
            side_effect=lambda value, **_kwargs: value,
        )
        self.address_patch.start()
        self.normalise_patch = mock.patch.object(
            guard.trans_updater, "_normalise_address_for_compare",
            side_effect=lambda value: str(value) if value else None,
        )
        self.normalise_patch.start()

    async def asyncTearDown(self):
        self.address_patch.stop()
        self.normalise_patch.stop()
        await self.context.__aexit__(None, None, None)
        schema.DB_PATH = self.old_path
        self.tmp.close()

    def inbound(self, signer: str, source: str, *, age=40 * 86400,
                amount=1_000_000, suffix="00", from_type=0):
        return {"hash": (suffix * 64)[:64], "sender": source,
                "recipient": signer, "value": amount, "fromType": from_type,
                "toType": 0, "timestamp": (self.now - age) * 1000,
                "blockNumber": 10, "executionResult": True}

    def rpc(self, origins: dict[str, tuple[str, int, int]], *, services=()):
        async def call(address, **_kwargs):
            self.assertFalse(self.db.in_transaction)
            if address in origins:
                source, timestamp, amount = origins[address]
                return {"data": [self.inbound(
                    address, source, age=self.now - timestamp, amount=amount,
                    suffix=hashlib.sha256(address.encode()).hexdigest()[:2]) ],
                    "metadata": None}
            if address in services:
                return {"data": [self.inbound(
                    f"recipient-{index}", address, suffix=f"{index:02x}")
                    for index in range(const.CLAIM_FUNDING_SOURCE_SAMPLE_SIZE)],
                    "metadata": None}
            return {"data": [], "metadata": None}
        return mock.AsyncMock(side_effect=call)

    async def observe_many(self, origins, *, services=()):
        rpc = self.rpc(origins, services=services)
        results = []
        with mock.patch.object(
                guard.trans_updater, "get_chain_transactions_by_address", rpc):
            for signer in origins:
                results.append(await guard.observe(
                    self.db, signer_address=signer, now=self.now))
        return results, rpc

    async def test_independent_funders_are_normal(self):
        origins = {f"signer-{i}": (f"source-{i}", self.now - i, 1000 + i)
                   for i in range(6)}
        results, _ = await self.observe_many(origins)
        self.assertTrue(all(not result["evidence"] for result in results))

    async def test_two_shared_recipients_are_not_evidence(self):
        results, _ = await self.observe_many({
            "a": ("friend", self.now - 10, 1000),
            "b": ("friend", self.now - 20, 1000),
        })
        self.assertTrue(all(not result["evidence"] for result in results))

    async def test_small_tight_cluster_and_similar_pattern_are_evidence(self):
        origins = {f"signer-{i}": ("small-source", self.now - i * 60, 1000)
                   for i in range(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS)}
        results, _ = await self.observe_many(origins)
        self.assertTrue(results[-1]["evidence"])
        self.assertTrue(results[-1]["similar_pattern"])
        self.assertEqual(results[-1]["member_count"], len(origins))

    async def test_dissimilar_amounts_do_not_strengthen_evidence(self):
        origins = {f"signer-{i}": ("small-source", self.now - i, 1000 + i * 1000)
                   for i in range(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS)}
        results, _ = await self.observe_many(origins)
        self.assertTrue(results[-1]["evidence"])
        self.assertFalse(results[-1]["similar_pattern"])

    async def test_repeated_lookup_does_not_duplicate_membership_or_rpc(self):
        rpc = self.rpc({"a": ("friend", self.now - 10, 1000)})
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            await guard.observe(self.db, signer_address="a", now=self.now)
            await guard.observe(self.db, signer_address="a", now=self.now + 1)
        source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('friend')}")
        self.assertEqual(len(source["members"]), 1)
        self.assertEqual(rpc.await_count, 2)  # signer history + one source sample

    async def test_full_source_page_is_service_like(self):
        count = const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS
        origins = {f"signer-{i}": ("faucet", self.now - i, 1000)
                   for i in range(count)}
        results, _ = await self.observe_many(origins, services={"faucet"})
        self.assertTrue(all(not result["evidence"] for result in results))
        source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('faucet')}")
        self.assertTrue(source["service_like"])

    async def test_wide_time_window_is_not_cluster_evidence(self):
        origins = {f"signer-{i}": (
            "giveaway", self.now - i * (const.CLAIM_FUNDING_CLUSTER_WINDOW_SECONDS + 1), 1000)
            for i in range(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS)}
        results, _ = await self.observe_many(origins)
        self.assertFalse(results[-1]["evidence"])

    async def test_contract_and_failed_inbound_are_not_origins(self):
        transactions = [self.inbound("a", "staking", from_type=3),
                        {**self.inbound("a", "failed"), "executionResult": False}]
        rpc = mock.AsyncMock(side_effect=[{"data": transactions}])
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            result = await guard.observe(self.db, signer_address="a", now=self.now)
        self.assertFalse(result["evidence"])
        self.assertEqual(rpc.await_count, 1)

    async def test_rpc_outage_is_unknown_and_short_cached(self):
        rpc = mock.AsyncMock(side_effect=RuntimeError("offline"))
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            first = await guard.observe(self.db, signer_address="a", now=self.now)
            second = await guard.observe(self.db, signer_address="a", now=self.now + 1)
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(second["status"], "unknown")
        rpc.assert_awaited_once()

    async def test_bounded_unexhausted_history_is_unknown(self):
        page = [self.inbound("a", "source", suffix=f"{i:02x}")
                for i in range(const.CLAIM_FUNDING_HISTORY_PAGE_SIZE)]
        # Unique final cursor on each bounded page.
        calls = 0
        async def rpc(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {"data": [{**tx, "hash": f"{calls:02x}" + tx["hash"][2:]}
                             for tx in page], "metadata": None}
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            result = await guard.observe(self.db, signer_address="a", now=self.now)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(calls, const.CLAIM_FUNDING_HISTORY_MAX_PAGES)

    async def test_concurrent_misses_reconcile_one_member(self):
        async def run():
            async with schema.get_db() as connection:
                return await guard.observe(connection, signer_address="a", now=self.now)
        rpc = self.rpc({"a": ("friend", self.now - 1, 1000)})
        with mock.patch.object(guard.trans_updater, "get_chain_transactions_by_address", rpc):
            results = await asyncio.gather(run(), run())
        self.assertTrue(all(result["status"] == "observed" for result in results))
        source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('friend')}")
        self.assertEqual(len(source["members"]), 1)

    async def test_cluster_observation_never_changes_user_status(self):
        user = await db_access.create_user(
            self.db, device_id_hash=hashlib.sha256(b"cluster-user").hexdigest())
        await self.db.commit()
        origins = {f"signer-{i}": ("small-source", self.now - i, 1000)
                   for i in range(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS)}
        await self.observe_many(origins)
        row = await db_access.get_user_by_id(self.db, user_id=user)
        self.assertEqual(row[schema.USER_STATUS], const.USER_STATUS_ACTIVE)


class WalletClusterPolicyTests(IsolatedAsyncioTestCase):
    async def test_password_spot_semantics_bypass_observation(self):
        db = mock.Mock(in_transaction=False)
        with mock.patch.object(guard, "observe", mock.AsyncMock()) as observe:
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="invalid",
                spot={schema.SPOT_USE_PASSWORD: 1}, ip=None, lat=0, long=0)
        self.assertTrue(result["allowed"])
        observe.assert_not_awaited()

    async def test_cluster_plus_existing_behaviour_restriction_stays_restricted(self):
        db = mock.Mock(in_transaction=False)
        with (mock.patch.object(fresh_claim_guard.db_access, "get_user_by_id",
                               mock.AsyncMock(return_value={schema.USER_STATUS: 1})),
              mock.patch.object(fresh_claim_guard.db_access, "get_unixepoch",
                               mock.AsyncMock(return_value=100)),
              mock.patch.object(fresh_claim_guard, "_behaviour_allows_public_claim",
                               mock.AsyncMock(return_value=False)),
              mock.patch.object(guard, "observe", mock.AsyncMock()) as observe):
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="signer",
                spot={schema.SPOT_USE_PASSWORD: 0}, ip=None, lat=0, long=0)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "behavioural_temporary_restriction")
        observe.assert_not_awaited()

    async def test_cluster_plus_weak_independent_anomaly_is_temporary_restriction(self):
        db = mock.Mock(in_transaction=False)
        with (mock.patch.object(fresh_claim_guard.db_access, "get_user_by_id",
                               mock.AsyncMock(return_value={schema.USER_STATUS: 1})),
              mock.patch.object(fresh_claim_guard.db_access, "get_unixepoch",
                               mock.AsyncMock(return_value=100)),
              mock.patch.object(fresh_claim_guard, "_behaviour_allows_public_claim",
                               mock.AsyncMock(return_value=True)),
              mock.patch.object(fresh_claim_guard, "_recent_weak_behaviour_anomaly",
                               mock.AsyncMock(return_value=True)),
              mock.patch.object(guard, "observe", mock.AsyncMock(return_value={
                  "status": "observed", "evidence": True,
                  "similar_pattern": True}))):
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="signer",
                spot={schema.SPOT_USE_PASSWORD: 0}, ip=None, lat=0, long=0)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "corroborated_temporary_restriction")

    async def test_suspicious_aged_signer_requires_location_corroboration(self):
        db = mock.Mock(in_transaction=False)
        user = {schema.USER_STATUS: 1}
        with (mock.patch.object(fresh_claim_guard.db_access, "get_user_by_id",
                               mock.AsyncMock(return_value=user)),
              mock.patch.object(fresh_claim_guard.db_access, "get_unixepoch",
                               mock.AsyncMock(return_value=100)),
              mock.patch.object(fresh_claim_guard, "_behaviour_allows_public_claim",
                               mock.AsyncMock(return_value=True)),
              mock.patch.object(guard, "observe", mock.AsyncMock(return_value={
                  "status": "observed", "evidence": True})),
              mock.patch.object(fresh_claim_guard, "signer_or_account_trusted",
                               mock.AsyncMock(return_value={"trusted": True,
                                                            "reason": "signer_established"})),
              mock.patch.object(fresh_claim_guard, "first_location_decision",
                               mock.AsyncMock(return_value={"allowed": False,
                                                            "reason": "first_location_mismatch_restriction"}))):
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="signer",
                spot={schema.SPOT_USE_PASSWORD: 0}, ip="8.8.8.8", lat=0, long=0)
        self.assertFalse(result["allowed"])

    async def test_cluster_alone_with_verified_location_is_allowed(self):
        db = mock.Mock(in_transaction=False)
        with (mock.patch.object(fresh_claim_guard.db_access, "get_user_by_id",
                               mock.AsyncMock(return_value={schema.USER_STATUS: 1})),
              mock.patch.object(fresh_claim_guard.db_access, "get_unixepoch",
                               mock.AsyncMock(return_value=100)),
              mock.patch.object(fresh_claim_guard, "_behaviour_allows_public_claim",
                               mock.AsyncMock(return_value=True)),
              mock.patch.object(guard, "observe", mock.AsyncMock(return_value={
                  "status": "observed", "evidence": True})),
              mock.patch.object(fresh_claim_guard, "signer_or_account_trusted",
                               mock.AsyncMock(return_value={"trusted": True})),
              mock.patch.object(fresh_claim_guard, "first_location_decision",
                               mock.AsyncMock(return_value={"allowed": True,
                                                            "reason": "first_location_verified"}))):
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="signer",
                spot={schema.SPOT_USE_PASSWORD: 0}, ip="8.8.8.8", lat=0, long=0)
        self.assertTrue(result["allowed"])
