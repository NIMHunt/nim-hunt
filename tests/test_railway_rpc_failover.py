import os
import unittest
from unittest import mock

import railway_start


class RailwayRpcFailoverTest(unittest.TestCase):
    def test_uses_primary_when_primary_is_healthy(self):
        calls = []

        def probe(url, **kwargs):
            calls.append((url, kwargs))

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NIMHUNT_NIMIQ_RPC_FALLBACK_URLS", None)
            selected = railway_start.select_rpc_url(
                "https://rpc.nimiqwatch.com",
                expected_network="MainAlbatross",
                timeout_seconds=12,
                probe=probe,
            )

        self.assertEqual(selected, "https://rpc.nimiqwatch.com")
        self.assertEqual(len(calls), 1)

    def test_falls_back_to_nimiqscan_when_primary_is_unavailable(self):
        calls = []

        def probe(url, **_kwargs):
            calls.append(url)
            if url == "https://rpc.nimiqwatch.com":
                raise railway_start.RpcUnavailableError("HTTP 503")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NIMHUNT_NIMIQ_RPC_FALLBACK_URLS", None)
            selected = railway_start.select_rpc_url(
                "https://rpc.nimiqwatch.com",
                expected_network="MainAlbatross",
                timeout_seconds=12,
                probe=probe,
            )

        self.assertEqual(selected, "https://rpc-mainnet.nimiqscan.com")
        self.assertEqual(
            calls,
            ["https://rpc.nimiqwatch.com", "https://rpc-mainnet.nimiqscan.com"],
        )

    def test_wrong_network_is_fatal_instead_of_being_hidden_by_failover(self):
        calls = []

        def probe(url, **_kwargs):
            calls.append(url)
            raise railway_start.RpcNetworkMismatchError(
                "RPC serves TestAlbatross, expected MainAlbatross"
            )

        with self.assertRaisesRegex(
            railway_start.RpcNetworkMismatchError,
            "expected MainAlbatross",
        ):
            railway_start.select_rpc_url(
                "https://rpc.nimiqwatch.com",
                expected_network="MainAlbatross",
                timeout_seconds=12,
                probe=probe,
            )

        self.assertEqual(calls, ["https://rpc.nimiqwatch.com"])

    def test_custom_fallback_list_is_supported_and_deduplicated(self):
        with mock.patch.dict(
            os.environ,
            {
                "NIMHUNT_NIMIQ_RPC_FALLBACK_URLS": (
                    "https://rpc.nimiqwatch.com, https://rpc.example.com"
                )
            },
            clear=False,
        ):
            candidates = railway_start._candidate_urls(
                "https://rpc.nimiqwatch.com",
                network="MainAlbatross",
            )

        self.assertEqual(
            candidates,
            ("https://rpc.nimiqwatch.com", "https://rpc.example.com"),
        )

    def test_all_unavailable_endpoints_fail_closed(self):
        def probe(url, **_kwargs):
            raise railway_start.RpcUnavailableError("HTTP 503")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NIMHUNT_NIMIQ_RPC_FALLBACK_URLS", None)
            with self.assertRaisesRegex(RuntimeError, "No verified Nimiq RPC endpoint"):
                railway_start.select_rpc_url(
                    "https://rpc.nimiqwatch.com",
                    expected_network="MainAlbatross",
                    timeout_seconds=12,
                    probe=probe,
                )

    def test_main_sets_selected_rpc_before_execing_uvicorn(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "NIMHUNT_DEPLOYMENT_MODE": "production",
                    "NIMHUNT_NIMIQ_NETWORK": "MainAlbatross",
                    "NIMHUNT_NIMIQ_RPC_URL": "https://rpc.nimiqwatch.com",
                    "NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS": "12",
                },
                clear=False,
            ),
            mock.patch.object(
                railway_start,
                "select_rpc_url",
                return_value="https://rpc-mainnet.nimiqscan.com",
            ) as select_rpc,
            mock.patch.object(railway_start.os, "execvp") as execvp,
            mock.patch.object(
                railway_start.sys,
                "argv",
                ["railway_start.py", "--host", "0.0.0.0", "--port", "1234"],
            ),
        ):
            railway_start.main()
            self.assertEqual(
                os.environ["NIMHUNT_NIMIQ_RPC_URL"],
                "https://rpc-mainnet.nimiqscan.com",
            )

        select_rpc.assert_called_once_with(
            "https://rpc.nimiqwatch.com",
            expected_network="MainAlbatross",
            timeout_seconds=12,
        )
        execvp.assert_called_once_with(
            "uvicorn",
            ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1234"],
        )


if __name__ == "__main__":
    unittest.main()
