import asyncio
import urllib.error

import pytest

import main


def _configure_public_testnet(monkeypatch) -> None:
    monkeypatch.setattr(main.const, "PUBLIC_DEPLOYMENT", True)
    monkeypatch.setattr(main.const, "NIMIQ_NETWORK", "TestAlbatross")
    monkeypatch.setattr(main.const, "NIMIQ_NETWORK_ID", 5)
    monkeypatch.setattr(main.const, "NIMIQ_RPC_URL", "https://rpc.testnet.example/")
    monkeypatch.setattr(main.const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12)


def test_public_rpc_network_verifies_directly_from_latest_block(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)
    calls = {}

    async def unexpected_get_network_id(**_kwargs):
        raise AssertionError("startup should not probe getNetworkId")

    def latest_block(**kwargs):
        calls.update(kwargs)
        return {"network": "TestAlbatross"}

    monkeypatch.setattr(
        main.trans_updater,
        "verify_configured_rpc_network",
        unexpected_get_network_id,
    )
    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    asyncio.run(main.verify_public_rpc_network())

    assert calls == {
        "rpc_url": "https://rpc.testnet.example/",
        "method": "getLatestBlock",
        "params": [False],
        "timeout_seconds": 12,
    }


def test_public_rpc_network_accepts_canonical_network_alias(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    def latest_block(**_kwargs):
        return {"network": "testnet"}

    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    asyncio.run(main.verify_public_rpc_network())


def test_public_rpc_network_rejects_wrong_network(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    def latest_block(**_kwargs):
        return {"network": "MainAlbatross"}

    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    with pytest.raises(
        RuntimeError,
        match="Configured Nimiq RPC serves MainAlbatross, expected TestAlbatross",
    ):
        asyncio.run(main.verify_public_rpc_network())


def test_public_rpc_network_reports_http_status(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    def latest_block(**_kwargs):
        raise urllib.error.HTTPError(
            "https://rpc.testnet.example/",
            503,
            "Service Unavailable",
            {},
            None,
        )

    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    with pytest.raises(RuntimeError, match="getLatestBlock returned HTTP 503"):
        asyncio.run(main.verify_public_rpc_network())


def test_public_rpc_network_reports_transport_failure_type(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    def latest_block(**_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    with pytest.raises(RuntimeError, match=r"getLatestBlock failed \(TimeoutError\)"):
        asyncio.run(main.verify_public_rpc_network())


def test_private_development_skips_rpc_network_verification(monkeypatch) -> None:
    monkeypatch.setattr(main.const, "PUBLIC_DEPLOYMENT", False)

    def unexpected_latest_block(**_kwargs):
        raise AssertionError("private development should not verify public RPC network")

    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", unexpected_latest_block)

    asyncio.run(main.verify_public_rpc_network())
