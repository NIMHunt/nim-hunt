import asyncio

import pytest

import main


def _configure_public_testnet(monkeypatch) -> None:
    monkeypatch.setattr(main.const, "PUBLIC_DEPLOYMENT", True)
    monkeypatch.setattr(main.const, "NIMIQ_NETWORK", "TestAlbatross")
    monkeypatch.setattr(main.const, "NIMIQ_NETWORK_ID", 5)
    monkeypatch.setattr(main.const, "NIMIQ_RPC_URL", "https://rpc.testnet.example/")
    monkeypatch.setattr(main.const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12)


def test_startup_regression_does_not_probe_get_network_id(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)
    calls = []

    async def get_network_id(**_kwargs):
        calls.append("getNetworkId")
        raise AssertionError("the unsupported preliminary probe must stay removed")

    def rpc_call(**kwargs):
        calls.append(kwargs["method"])
        return {"network": "TestAlbatross"}

    monkeypatch.setattr(
        main.trans_updater,
        "verify_configured_rpc_network",
        get_network_id,
    )
    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", rpc_call)

    asyncio.run(main.verify_public_rpc_network())

    assert calls == ["getLatestBlock"]


def test_latest_block_network_proof_still_fails_closed(monkeypatch) -> None:
    _configure_public_testnet(monkeypatch)

    def latest_block(**_kwargs):
        return {"number": 123456}

    monkeypatch.setattr(main.trans_updater, "_json_rpc_post_sync", latest_block)

    with pytest.raises(RuntimeError, match="did not expose a network"):
        asyncio.run(main.verify_public_rpc_network())
