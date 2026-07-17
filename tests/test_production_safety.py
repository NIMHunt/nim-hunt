import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock

import constants as const
import main

SAFE_PRODUCTION_SETTINGS = {
    "PRODUCTION_MODE": True,
    "DEFAULT_TO_TEST_USER": False,
    "ALLOW_DEV_WALLET_PLACEHOLDERS": False,
    "ALLOW_DEV_WALLET_SENDS": False,
    "NIMIQ_NETWORK": "MainAlbatross",
    "NIMIQ_HUB_URL": "https://hub.nimiq.com",
    "SPOT_CANCELLATION_FEE_ADDRESS": "NQ12 PRODUCTION CANCELLATION FEE ADDRESS",
}


@contextmanager
def patched_settings(**overrides):
    settings = {**SAFE_PRODUCTION_SETTINGS, **overrides}
    with ExitStack() as stack:
        for name, value in settings.items():
            stack.enter_context(mock.patch.object(const, name, value))
        yield


class ProductionSafetyValidationTest(unittest.TestCase):
    def test_local_development_settings_are_allowed_when_not_in_production(self):
        with patched_settings(
            PRODUCTION_MODE=False,
            DEFAULT_TO_TEST_USER=True,
            ALLOW_DEV_WALLET_PLACEHOLDERS=True,
            ALLOW_DEV_WALLET_SENDS=True,
            NIMIQ_NETWORK="TestAlbatross",
            NIMIQ_HUB_URL="https://hub.nimiq-testnet.com",
            SPOT_CANCELLATION_FEE_ADDRESS="NQ00 NIMHUNT DEV CANCELLATION FEE POOL",
        ):
            main.validate_production_safety()

    def test_production_refuses_test_user_fallback(self):
        with patched_settings(DEFAULT_TO_TEST_USER=True):
            with self.assertRaisesRegex(RuntimeError, "DEFAULT_TO_TEST_USER"):
                main.validate_production_safety()

    def test_production_refuses_dev_wallet_settings_and_testnet(self):
        with patched_settings(
            ALLOW_DEV_WALLET_PLACEHOLDERS=True,
            ALLOW_DEV_WALLET_SENDS=True,
            NIMIQ_NETWORK="TestAlbatross",
            NIMIQ_HUB_URL="https://hub.nimiq-testnet.com",
            SPOT_CANCELLATION_FEE_ADDRESS="NQ00 NIMHUNT DEV CANCELLATION FEE POOL",
        ):
            with self.assertRaisesRegex(RuntimeError, "ALLOW_DEV_WALLET_PLACEHOLDERS"):
                main.validate_production_safety()

    def test_production_refuses_testnet_hub_and_dev_fee_address(self):
        with patched_settings(
            NIMIQ_HUB_URL="https://hub.nimiq-testnet.com",
            SPOT_CANCELLATION_FEE_ADDRESS="NQ00 NIMHUNT DEV CANCELLATION FEE POOL",
        ):
            with self.assertRaisesRegex(RuntimeError, "NIMIQ_HUB_URL"):
                main.validate_production_safety()


class ApplicationLifespanTest(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_runs_startup_then_shutdown(self):
        calls = []

        async def record_startup():
            calls.append("startup")

        async def record_shutdown():
            calls.append("shutdown")

        with (
            mock.patch.object(main, "startup", side_effect=record_startup),
            mock.patch.object(main, "shutdown", side_effect=record_shutdown),
        ):
            async with main.lifespan(main.app):
                calls.append("running")

        self.assertEqual(calls, ["startup", "running", "shutdown"])

    async def test_lifespan_still_shuts_down_after_application_error(self):
        shutdown = mock.AsyncMock()

        with (
            mock.patch.object(main, "startup", mock.AsyncMock()),
            mock.patch.object(main, "shutdown", shutdown),
        ):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                async with main.lifespan(main.app):
                    raise RuntimeError("test failure")

        shutdown.assert_awaited_once_with()
