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

    async def test_genuinely_broad_outgoing_source_is_service_like(self):
        count = const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS
        origins = {f"signer-{i}": ("faucet", self.now - i, 1000)
                   for i in range(count)}
        results, _ = await self.observe_many(origins, services={"faucet"})
        self.assertTrue(all(not result["evidence"] for result in results))
        source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('faucet')}")
        self.assertTrue(source["service_like"])

    async def test_full_page_with_two_outgoing_recipients_is_not_a_service(self):
        source = "busy-small-source"
        page = [self.inbound(
            f"recipient-{index % 2}", source, suffix=f"{index:02x}")
            for index in range(const.CLAIM_FUNDING_SOURCE_SAMPLE_SIZE)]
        rpc = mock.AsyncMock(return_value={"data": page, "metadata": None})
        with mock.patch.object(
                guard.trans_updater, "get_chain_transactions_by_address", rpc):
            breadth = await guard._source_breadth(source)
        self.assertEqual(breadth, "unknown")

    async def test_fiftieth_claimant_does_not_make_cluster_safe_and_storage_is_bounded(self):
        origins = {f"signer-{i}": ("small-source", self.now - i, 1000)
                   for i in range(50)}
        results, _ = await self.observe_many(origins)
        self.assertTrue(results[48]["evidence"])
        self.assertTrue(results[49]["evidence"])
        source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('small-source')}")
        self.assertFalse(source["service_like"])
        self.assertLessEqual(
            len(source["members"]), const.CLAIM_FUNDING_CLUSTER_MEMBER_LIMIT)
        self.assertLessEqual(
            source["member_count"], const.CLAIM_FUNDING_CLUSTER_MEMBER_LIMIT + 1)

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
        self.assertFalse(first["evidence"] or second["evidence"])
        rpc.assert_awaited_once()
        cached = await fresh_claim_guard._get(
            self.db, f"{guard.ORIGIN_PREFIX}{guard._hash('a')}")
        self.assertEqual(cached["result"], "provider_failure")
        self.assertNotIn("source_hash", cached)

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

    async def test_authoritative_refresh_moves_member_to_new_source(self):
        origins = {name: ("source-a", self.now - index, 1000)
                   for index, name in enumerate(("a", "b", "c", "d", "e"))}
        await self.observe_many(origins)
        origin_key = f"{guard.ORIGIN_PREFIX}{guard._hash('a')}"
        cached = await fresh_claim_guard._get(self.db, origin_key)
        cached["refresh_at"] = self.now
        await fresh_claim_guard._set(self.db, origin_key, cached)
        await self.db.commit()
        self.now += 1
        origins["a"] = ("source-b", self.now - 10, 2000)
        await self.observe_many(origins)
        old_source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('source-a')}")
        new_source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('source-b')}")
        authoritative = await fresh_claim_guard._get(self.db, origin_key)
        self.assertNotIn(guard._hash("a"), old_source["members"])
        self.assertFalse(guard._cluster_state(old_source)["evidence"])
        self.assertIn(guard._hash("a"), new_source["members"])
        self.assertEqual(authoritative["source_hash"], guard._hash("source-b"))
        self.assertEqual((authoritative["first_funding_at"],
                          authoritative["first_funding_amount"]),
                         (self.now - 10, 2000))

    async def test_refresh_to_no_origin_removes_old_membership(self):
        await self.observe_many({"a": ("source-a", self.now - 20, 1000)})
        origin_key = f"{guard.ORIGIN_PREFIX}{guard._hash('a')}"
        cached = await fresh_claim_guard._get(self.db, origin_key)
        cached["refresh_at"] = self.now
        await fresh_claim_guard._set(self.db, origin_key, cached)
        await self.db.commit()
        self.now += 1
        rpc = mock.AsyncMock(return_value={"data": [], "metadata": None})
        with mock.patch.object(
                guard.trans_updater, "get_chain_transactions_by_address", rpc):
            result = await guard.observe(self.db, signer_address="a", now=self.now)
        old_source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('source-a')}")
        authoritative = await fresh_claim_guard._get(self.db, origin_key)
        self.assertFalse(result["evidence"])
        self.assertNotIn(guard._hash("a"), old_source["members"])
        self.assertIsNone(authoritative["source_hash"])

    async def test_saturated_source_change_downgrades_ghost_evidence(self):
        origins = {f"signer-{i}": ("source-a", self.now - i, 1000)
                   for i in range(const.CLAIM_FUNDING_CLUSTER_MEMBER_LIMIT + 1)}
        await self.observe_many(origins)
        signer = "signer-0"
        origin_key = f"{guard.ORIGIN_PREFIX}{guard._hash(signer)}"
        cached = await fresh_claim_guard._get(self.db, origin_key)
        cached["refresh_at"] = self.now
        await fresh_claim_guard._set(self.db, origin_key, cached)
        await self.db.commit()
        self.now += 1
        await self.observe_many({signer: ("source-b", self.now, 2000)})
        old_source = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('source-a')}")
        self.assertTrue(old_source["membership_uncertain"])
        self.assertFalse(guard._cluster_state(old_source)["evidence"])

    async def test_concurrent_different_origins_keep_winner_metadata_consistent(self):
        release_a = asyncio.Event()

        async def history(address):
            del address
            if asyncio.current_task().get_name() == "origin-a":
                await release_a.wait()
                await asyncio.sleep(0.05)
                return [self.inbound("a", "source-a", age=20, amount=1000)]
            release_a.set()
            return [self.inbound("a", "source-b", age=10, amount=2000)]

        async def run(name):
            async with schema.get_db() as connection:
                task = asyncio.current_task()
                task.set_name(name)
                return await guard.observe(connection, signer_address="a", now=self.now)

        with (mock.patch.object(guard, "_complete_history", side_effect=history),
              mock.patch.object(guard, "_source_breadth",
                                mock.AsyncMock(return_value="ordinary"))):
            await asyncio.gather(run("origin-a"), run("origin-b"))
        origin = await fresh_claim_guard._get(
            self.db, f"{guard.ORIGIN_PREFIX}{guard._hash('a')}")
        self.assertEqual(origin["source_hash"], guard._hash("source-b"))
        self.assertEqual((origin["first_funding_at"], origin["first_funding_amount"]),
                         (self.now - 10, 2000))
        source_b = await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('source-b')}")
        self.assertEqual(source_b["members"][guard._hash("a")],
                         {"timestamp": self.now - 10, "amount": 2000})
        self.assertIsNone(await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}{guard._hash('source-a')}"))

    async def test_concurrent_no_origin_cannot_create_ghost_cluster(self):
        release_empty = asyncio.Event()

        async def history(address):
            del address
            if asyncio.current_task().get_name() == "empty":
                await release_empty.wait()
                await asyncio.sleep(0.05)
                return []
            release_empty.set()
            return [self.inbound("a", "source-b", age=10, amount=2000)]

        async def run(name):
            async with schema.get_db() as connection:
                asyncio.current_task().set_name(name)
                return await guard.observe(connection, signer_address="a", now=self.now)

        with (mock.patch.object(guard, "_complete_history", side_effect=history),
              mock.patch.object(guard, "_source_breadth",
                                mock.AsyncMock(return_value="ordinary"))):
            await asyncio.gather(run("empty"), run("valid"))
        origin = await fresh_claim_guard._get(
            self.db, f"{guard.ORIGIN_PREFIX}{guard._hash('a')}")
        self.assertEqual(origin["source_hash"], guard._hash("source-b"))
        self.assertIsNone(await fresh_claim_guard._get(
            self.db, f"{guard.SOURCE_PREFIX}None"))

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

    async def test_funding_unknown_does_not_block_established_claimant(self):
        db = mock.Mock(in_transaction=False)
        with (mock.patch.object(fresh_claim_guard.db_access, "get_user_by_id",
                               mock.AsyncMock(return_value={schema.USER_STATUS: 1})),
              mock.patch.object(fresh_claim_guard.db_access, "get_unixepoch",
                               mock.AsyncMock(return_value=100)),
              mock.patch.object(fresh_claim_guard, "_behaviour_allows_public_claim",
                               mock.AsyncMock(return_value=True)),
              mock.patch.object(guard, "observe", mock.AsyncMock(return_value={
                  "status": "unknown", "evidence": False})),
              mock.patch.object(fresh_claim_guard, "signer_or_account_trusted",
                               mock.AsyncMock(return_value={"trusted": True})),
              mock.patch.object(fresh_claim_guard, "first_location_decision",
                               mock.AsyncMock(return_value={"allowed": True,
                                                            "reason": "ip_geolocation_unreliable"}))):
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="signer",
                spot={schema.SPOT_USE_PASSWORD: 0}, ip=None, lat=0, long=0)
        self.assertTrue(result["allowed"])

    async def test_funding_unknown_does_not_weaken_signer_history_unknown(self):
        db = mock.Mock(in_transaction=False)
        with (mock.patch.object(fresh_claim_guard.db_access, "get_user_by_id",
                               mock.AsyncMock(return_value={schema.USER_STATUS: 1})),
              mock.patch.object(fresh_claim_guard.db_access, "get_unixepoch",
                               mock.AsyncMock(return_value=100)),
              mock.patch.object(fresh_claim_guard, "_behaviour_allows_public_claim",
                               mock.AsyncMock(return_value=True)),
              mock.patch.object(guard, "observe", mock.AsyncMock(return_value={
                  "status": "unknown", "evidence": False})),
              mock.patch.object(fresh_claim_guard, "signer_or_account_trusted",
                               mock.AsyncMock(return_value={
                                   "trusted": False,
                                   "reason": "signer_history_unavailable"}))):
            result = await fresh_claim_guard.public_claim_decision(
                db, user_id=1, signer_address="signer",
                spot={schema.SPOT_USE_PASSWORD: 0}, ip=None, lat=0, long=0)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "signer_history_unavailable")

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
