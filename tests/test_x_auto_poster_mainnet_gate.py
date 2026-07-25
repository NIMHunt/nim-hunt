"""Automatic X posting must never run outside production MainAlbatross."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import constants as const
import x_auto_poster


def run(coroutine):
    return asyncio.run(coroutine)


def configure(
    monkeypatch,
    *,
    enabled: bool,
    production: bool,
    mode: str,
    network: str,
    network_id: int,
) -> None:
    monkeypatch.setattr(const, "X_AUTO_POST_ENABLED", enabled)
    monkeypatch.setattr(const, "PRODUCTION_MODE", production)
    monkeypatch.setattr(const, "DEPLOYMENT_MODE", mode)
    monkeypatch.setattr(const, "NIMIQ_NETWORK", network)
    monkeypatch.setattr(const, "NIMIQ_NETWORK_ID", network_id)


def test_flag_alone_is_not_enough_on_testnet(monkeypatch) -> None:
    configure(
        monkeypatch,
        enabled=True,
        production=False,
        mode="public-testnet",
        network="TestAlbatross",
        network_id=5,
    )
    assert x_auto_poster.x_posting_allowed() is False
    assert x_auto_poster.x_posting_block_reason() == (
        "requires_production_mainalbatross"
    )


def test_flag_alone_is_not_enough_on_devnet(monkeypatch) -> None:
    configure(
        monkeypatch,
        enabled=True,
        production=False,
        mode="development",
        network="DevAlbatross",
        network_id=6,
    )
    assert x_auto_poster.x_posting_allowed() is False


def test_production_mode_with_wrong_network_is_still_blocked(monkeypatch) -> None:
    configure(
        monkeypatch,
        enabled=True,
        production=True,
        mode="production",
        network="TestAlbatross",
        network_id=5,
    )
    assert x_auto_poster.x_posting_allowed() is False


def test_only_production_mainalbatross_can_be_allowed(monkeypatch) -> None:
    configure(
        monkeypatch,
        enabled=True,
        production=True,
        mode="production",
        network="MainAlbatross",
        network_id=24,
    )
    assert x_auto_poster.x_posting_block_reason() is None
    assert x_auto_poster.x_posting_allowed() is True


def test_testnet_does_not_require_credentials_even_if_flag_is_true(
    monkeypatch,
) -> None:
    configure(
        monkeypatch,
        enabled=True,
        production=False,
        mode="public-testnet",
        network="TestAlbatross",
        network_id=5,
    )
    for name in (
        const.NIMHUNT_X_API_KEY_ENV,
        const.NIMHUNT_X_API_SECRET_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    x_auto_poster.validate_configuration()


def test_mainnet_requires_credentials_when_flag_is_true(monkeypatch) -> None:
    configure(
        monkeypatch,
        enabled=True,
        production=True,
        mode="production",
        network="MainAlbatross",
        network_id=24,
    )
    monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")
    for name in (
        const.NIMHUNT_X_API_KEY_ENV,
        const.NIMHUNT_X_API_SECRET_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(x_auto_poster.XConfigurationError):
        x_auto_poster.validate_configuration()


def test_testnet_startup_never_contacts_x(monkeypatch) -> None:
    async def scenario() -> None:
        configure(
            monkeypatch,
            enabled=True,
            production=False,
            mode="public-testnet",
            network="TestAlbatross",
            network_id=5,
        )
        monkeypatch.setattr(x_auto_poster, "_X_POST_TASK", None)
        prepare_disabled = AsyncMock(
            return_value=x_auto_poster.ActivationCursor(123, 0)
        )
        request_json = AsyncMock(
            side_effect=AssertionError("testnet must not contact X")
        )
        monkeypatch.setattr(
            x_auto_poster,
            "prepare_disabled_mode",
            prepare_disabled,
        )
        monkeypatch.setattr(x_auto_poster, "request_json", request_json)

        await x_auto_poster.start_x_auto_poster(run_immediately=True)

        prepare_disabled.assert_awaited_once_with()
        request_json.assert_not_awaited()
        assert x_auto_poster._X_POST_TASK is None
        status = x_auto_poster.x_auto_poster_status()
        assert status["requested_enabled"] is True
        assert status["enabled"] is False
        assert status["blocked_reason"] == (
            "requires_production_mainalbatross"
        )
        assert status["production_mainnet_only"] is True
        assert status["deployment_mode"] == "public-testnet"
        assert status["network"] == "TestAlbatross"
        assert status["network_id"] == 5

    run(scenario())
