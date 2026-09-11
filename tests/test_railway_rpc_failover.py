import asyncio
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

import railway_start


def _fake_app_module(*, verification_error=None, recovered_network="MainAlbatross"):
    calls = []
    worker_calls = []
    recorded_send_calls = []

    async def verify_public_rpc_network():
        if verification_error is not None:
            raise verification_error

    async def refresh_chain_head_height(*_args, **_kwargs):
        calls.append("refresh_chain_head_height")
        return 123

    def rpc_post(*, rpc_url, method, params, timeout_seconds):
        calls.append(method)
        if method == "getLatestBlock":
            return {"network": recovered_network, "number": 123}
        if method == "getBlockNumber":
            return 123
        return {"ok": True}

    async def recorded_send(*_args, **_kwargs):
        recorded_send_calls.append(True)
        return {"ok": True}

    async def start_settlement_refresher(*_args, **kwargs):
        worker_calls.append(("settlement", dict(kwargs)))

    async def start_transaction_refresher(*_args, **kwargs):
        worker_calls.append(("transactions", dict(kwargs)))

    trans_updater = SimpleNamespace(
        refresh_chain_head_height=refresh_chain_head_height,
        _json_rpc_post_sync=rpc_post,
        _unwrap_rpc_result=lambda result: (result, None),
        _submit_recorded_chain_send=recorded_send,
        start_transaction_refresher=start_transaction_refresher,
    )
    settlement_updater = SimpleNamespace(
        start_settlement_refresher=start_settlement_refresher,
    )
    const = SimpleNamespace(
        PUBLIC_DEPLOYMENT=True,
        NIMIQ_NETWORK="MainAlbatross",
        NIMIQ_RPC_URL="https://rpc.nimiqwatch.com",
        NIMIQ_RPC_TIMEOUT_SECONDS=12,
    )

    aliases = {
        "mainalbatross": "MainAlbatross",
        "mainnet": "MainAlbatross",
        "testalbatross": "TestAlbatross",
        "testnet": "TestAlbatross",
    }

    def canonical_network(value):
        clean = str(value or "").strip().lower().replace("-", "").replace("_", "")
        return aliases.get(clean, str(value or "").strip())

    module = SimpleNamespace(
        const=const,
        trans_updater=trans_updater,
        settlement_updater=settlement_updater,
        verify_public_rpc_network=verify_public_rpc_network,
        _canonical_rpc_network_name=canonical_network,
        app=object(),
    )
    return module, calls, worker_calls, recorded_send_calls


class RailwayDegradedRpcStartupTest(unittest.TestCase):
    def test_http_rpc_outage_is_temporary(self):
        error = RuntimeError(
            "Public deployment RPC network validation failed: getLatestBlock returned HTTP 503"
        )
        self.assertTrue(railway_start._temporary_rpc_validation_failure(error))

    def test_wrong_network_is_not_temporary(self):
        error = RuntimeError(
            "Public deployment RPC network validation failed: Configured Nimiq RPC "
            "serves TestAlbatross, expected MainAlbatross"
        )
        self.assertFalse(railway_start._temporary_rpc_validation_failure(error))

    def test_missing_network_identity_is_not_temporary(self):
        error = RuntimeError(
            "Public deployment RPC network validation failed: Nimiq RPC getLatestBlock "
            "did not expose a network"
        )
        self.assertFalse(railway_start._temporary_rpc_validation_failure(error))

    def test_temporary_rpc_failure_allows_degraded_startup(self):
        module, _calls, _worker_calls, _send_calls = _fake_app_module(
            verification_error=RuntimeError(
                "Public deployment RPC network validation failed: getLatestBlock returned HTTP 503"
            )
        )
        state = railway_start.install_degraded_rpc_startup_policy(module)

        asyncio.run(module.verify_public_rpc_network())

        self.assertTrue(state.degraded)
        self.assertIn("HTTP 503", state.reason)

    def test_wrong_network_still_aborts_startup(self):
        module, _calls, _worker_calls, _send_calls = _fake_app_module(
            verification_error=RuntimeError(
                "Public deployment RPC network validation failed: Configured Nimiq RPC "
                "serves TestAlbatross, expected MainAlbatross"
            )
        )
        state = railway_start.install_degraded_rpc_startup_policy(module)

        with self.assertRaisesRegex(RuntimeError, "expected MainAlbatross"):
            asyncio.run(module.verify_public_rpc_network())

        self.assertFalse(state.degraded)

    def test_first_rpc_after_degraded_boot_reverifies_network(self):
        module, calls, _worker_calls, _send_calls = _fake_app_module(
            verification_error=RuntimeError(
                "Public deployment RPC network validation failed: getLatestBlock returned HTTP 503"
            )
        )
        state = railway_start.install_degraded_rpc_startup_policy(module)
        asyncio.run(module.verify_public_rpc_network())
        calls.clear()

        height = module.trans_updater._json_rpc_post_sync(
            rpc_url="https://rpc.nimiqwatch.com",
            method="getBlockNumber",
            params=[],
            timeout_seconds=12,
        )

        self.assertEqual(height, 123)
        self.assertEqual(calls, ["getLatestBlock", "getBlockNumber"])
        self.assertFalse(state.degraded)

    def test_recovered_wrong_network_blocks_chain_processing(self):
        module, calls, _worker_calls, _send_calls = _fake_app_module(
            verification_error=RuntimeError(
                "Public deployment RPC network validation failed: getLatestBlock returned HTTP 503"
            ),
            recovered_network="TestAlbatross",
        )
        state = railway_start.install_degraded_rpc_startup_policy(module)
        asyncio.run(module.verify_public_rpc_network())
        calls.clear()

        with self.assertRaisesRegex(RuntimeError, "Recovered Nimiq RPC serves TestAlbatross"):
            module.trans_updater._json_rpc_post_sync(
                rpc_url="https://rpc.nimiqwatch.com",
                method="getBlockNumber",
                params=[],
                timeout_seconds=12,
            )

        self.assertEqual(calls, ["getLatestBlock"])
        self.assertTrue(state.degraded)

    def test_degraded_workers_do_not_make_initial_failure_fatal(self):
        module, _calls, worker_calls, _send_calls = _fake_app_module(
            verification_error=RuntimeError(
                "Public deployment RPC network validation failed: getLatestBlock returned HTTP 503"
            )
        )
        railway_start.install_degraded_rpc_startup_policy(module)
        asyncio.run(module.verify_public_rpc_network())

        asyncio.run(
            module.settlement_updater.start_settlement_refresher(
                run_immediately=True,
                fail_on_initial_error=True,
            )
        )
        asyncio.run(
            module.trans_updater.start_transaction_refresher(
                run_immediately=True,
                fail_on_initial_error=True,
            )
        )

        self.assertEqual(worker_calls[0][1]["fail_on_initial_error"], False)
        self.assertEqual(worker_calls[1][1]["fail_on_initial_error"], False)

    def test_recorded_server_send_is_gated_before_intent_creation(self):
        module, _calls, _worker_calls, send_calls = _fake_app_module(
            verification_error=RuntimeError(
                "Public deployment RPC network validation failed: getLatestBlock returned HTTP 503"
            )
        )

        def unavailable_rpc(**_kwargs):
            raise urllib.error.HTTPError(
                "https://rpc.nimiqwatch.com",
                503,
                "Service Unavailable",
                {},
                None,
            )

        module.trans_updater._json_rpc_post_sync = unavailable_rpc
        # Install after replacing the underlying transport so the policy captures it.
        railway_start.install_degraded_rpc_startup_policy(module)
        asyncio.run(module.verify_public_rpc_network())

        with self.assertRaises(urllib.error.HTTPError):
            asyncio.run(module.trans_updater._submit_recorded_chain_send())

        self.assertEqual(send_calls, [])

    def test_main_runs_the_imported_app_in_process(self):
        module, _calls, _worker_calls, _send_calls = _fake_app_module()
        uvicorn = SimpleNamespace(run=mock.Mock())

        with mock.patch.object(
            railway_start.sys,
            "argv",
            [
                "railway_start.py",
                "--host",
                "0.0.0.0",
                "--port",
                "1234",
                "--workers",
                "1",
                "--proxy-headers",
                "--forwarded-allow-ips",
                railway_start.RAILWAY_HTTP_PROXY_CIDR,
            ],
        ):
            railway_start.main(app_module=module, uvicorn_module=uvicorn)

        uvicorn.run.assert_called_once_with(
            module.app,
            host="0.0.0.0",
            port=1234,
            workers=1,
            proxy_headers=True,
            forwarded_allow_ips=railway_start.RAILWAY_HTTP_PROXY_CIDR,
        )


if __name__ == "__main__":
    unittest.main()
