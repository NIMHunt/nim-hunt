import asyncio

import pytest

import main


def _configure_public_testnet(monkeypatch) -> None:
    monkeypatch.setattr(main.const, "PUBLIC_DEPLOYMENT", True)
    monkeypatch.setattr(main.const, "NIMIQ_NETWORK", "TestAlbatross")
    monkeypatch.setattr(main.const, "NIMIQ_NETWORK_ID", 5)
    monkeypatch.setattr(main.const, "NIMIQ_RPC_URL", "https://rpc.testnet.example/")
    monkeypatch.setattr(main.const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12)


def test_public_rpc_network_uses_get_network_id_when_available(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    async def verify_configured_rpc_network(**kwargs):
        assert kwargs["expected_network_id"] == 5
        return 5

    def unexpected_fallback(**_kwargs):
        raise AssertionError("getLatestBlock fallback should not run")

    monkeypatch.setattr(
        main.trans_updater,
        "verify_configured_rpc_network",
        verify_configured_rpc_network,
    )
    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", unexpected_fallback)

    asyncio.run(main.verify_public_rpc_network())


def test_public_rpc_network_falls_back_to_latest_block(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)
    calls = {}

    async def unsupported_get_network_id(**_kwargs):
        raise RuntimeError(
            "Nimiq RPC error: {'code': -32601, 'message': 'Method not found'}"
        )

    def latest_block(**kwargs):
        calls.update(kwargs)
        return {"network": "TestAlbatross"}

    monkeypatch.setattr(
        main.trans_updater,
        "verify_configured_rpc_network",
        unsupported_get_network_id,
    )
    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    asyncio.run(main.verify_public_rpc_network())

    assert calls == {
        "rpc_url": "https://rpc.testnet.example/",
        "method": "getLatestBlock",
        "params": [False],
        "timeout_seconds": 12,
    }


def test_public_rpc_network_fallback_rejects_wrong_network(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    async def unsupported_get_network_id(**_kwargs):
        raise RuntimeError("Nimiq RPC error: -32601 Method not found")

    def latest_block(**_kwargs):
        return {"network": "MainAlbatross"}

    monkeypatch.setattr(
        main.trans_updater,
        "verify_configured_rpc_network",
        unsupported_get_network_id,
    )
    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    with pytest.raises(RuntimeError, match="RPC network validation failed"):
        asyncio.run(main.verify_public_rpc_network())
