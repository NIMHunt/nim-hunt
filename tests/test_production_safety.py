import os
import subprocess
import sys
import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock

import constants as const
import main
import public_html
import spoof
import wallet
from starlette.requests import Request

SAFE_PRODUCTION_SETTINGS = {
    "PRODUCTION_MODE": True,
    "TEST_FEATURES_ENABLED": False,
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
    dev_seed_env = getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED")
    default_mnemonic_env = getattr(
        const,
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV",
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
    )
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.dict(
                os.environ,
                {dev_seed_env: "", default_mnemonic_env: ""},
                clear=False,
            )
        )
        for name, value in settings.items():
            stack.enter_context(mock.patch.object(const, name, value))
        yield


class ProductionSafetyValidationTest(unittest.TestCase):
    def test_local_development_settings_are_allowed_when_not_in_production(self):
        with patched_settings(
            PRODUCTION_MODE=False,
            TEST_FEATURES_ENABLED=True,
            DEFAULT_TO_TEST_USER=True,
            ALLOW_DEV_WALLET_PLACEHOLDERS=True,
            ALLOW_DEV_WALLET_SENDS=True,
            NIMIQ_NETWORK="TestAlbatross",
            NIMIQ_HUB_URL="https://hub.nimiq-testnet.com",
            SPOT_CANCELLATION_FEE_ADDRESS="NQ00 NIMHUNT DEV CANCELLATION FEE POOL",
        ):
            main.validate_production_safety()


    def test_production_refuses_test_features_flag(self):
        with patched_settings(TEST_FEATURES_ENABLED=True):
            with self.assertRaisesRegex(RuntimeError, "TEST_FEATURES_ENABLED"):
                main.validate_production_safety()

    def test_production_refuses_unencrypted_dev_seed(self):
        dev_seed_env = getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED")
        with patched_settings(), mock.patch.dict(os.environ, {dev_seed_env: "unsafe seed"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, dev_seed_env):
                main.validate_production_safety()

    def test_production_refuses_public_default_test_mnemonic(self):
        env_name = getattr(
            const,
            "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV",
            "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
        )
        with patched_settings(), mock.patch.dict(os.environ, {env_name: "1"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, env_name):
                main.validate_production_safety()

    def test_production_environment_disables_test_helpers_at_import(self):
        command = (
            "import constants; "
            "print(int(constants.PRODUCTION_MODE), int(constants.TEST_FEATURES_ENABLED), "
            "int(constants.DEFAULT_TO_TEST_USER), int(constants.ALLOW_DEV_WALLET_PLACEHOLDERS))"
        )
        environment = os.environ.copy()
        environment["NIMHUNT_PRODUCTION"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.stdout.strip(), "1 0 0 0")

    def test_development_environment_keeps_test_helpers_at_import(self):
        command = (
            "import constants; "
            "print(int(constants.PRODUCTION_MODE), int(constants.TEST_FEATURES_ENABLED), "
            "int(constants.DEFAULT_TO_TEST_USER), int(constants.ALLOW_DEV_WALLET_PLACEHOLDERS))"
        )
        environment = os.environ.copy()
        environment.pop("NIMHUNT_PRODUCTION", None)
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.stdout.strip(), "0 1 1 1")

    def test_production_ignores_unencrypted_development_seed(self):
        dev_seed_env = getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED")
        with (
            mock.patch.object(const, "TEST_FEATURES_ENABLED", False),
            mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False),
            mock.patch.dict(os.environ, {dev_seed_env: "unsafe seed"}, clear=True),
        ):
            self.assertIsNone(wallet.get_master_seed(required=False))

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


class DesktopUserGatingTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_wallet_uses_desktop_user_only_in_development(self):
        payload = public_html.HomeSessionRequest(wallet_available=False)
        test_user = {
            "id": int(const.TEST_USER_ID),
            "display_name": "Desktop Test User",
            "u_status": const.USER_STATUS_ACTIVE,
        }
        db = object()
        with (
            mock.patch.object(const, "DEFAULT_TO_TEST_USER", True),
            mock.patch.object(
                public_html.db_access,
                "get_user_by_id",
                mock.AsyncMock(return_value=test_user),
            ),
            mock.patch.object(
                public_html.db_access,
                "touch_user_last_seen",
                mock.AsyncMock(),
            ),
        ):
            user, meta, http_status = await public_html._identify_private_page_user(db, payload)

        self.assertEqual(user, test_user)
        self.assertEqual(meta["code"], "test_user")
        self.assertEqual(http_status, 200)

    async def test_missing_wallet_never_uses_desktop_user_in_production(self):
        payload = public_html.HomeSessionRequest(wallet_available=False)
        get_test_user = mock.AsyncMock()
        with (
            mock.patch.object(const, "DEFAULT_TO_TEST_USER", False),
            mock.patch.object(public_html.db_access, "get_user_by_id", get_test_user),
        ):
            user, meta, http_status = await public_html._identify_private_page_user(object(), payload)

        self.assertIsNone(user)
        self.assertEqual(meta["code"], "wallet_unavailable")
        self.assertEqual(http_status, 200)
        get_test_user.assert_not_awaited()


class DevelopmentScriptGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_mock_seed_refuses_production_before_deleting_database(self):
        with (
            mock.patch.object(const, "PRODUCTION_MODE", True),
            mock.patch.object(spoof, "_remove_existing_database_files") as remove_database,
        ):
            with self.assertRaisesRegex(RuntimeError, "production mode"):
                await spoof.seed_mock_data()
        remove_database.assert_not_called()

    def test_shell_development_helpers_refuse_production(self):
        environment = os.environ.copy()
        environment["NIMHUNT_PRODUCTION"] = "1"
        for script_name in ("nimhunt_start_dev.sh", "nimhunt_reset_mock_data.sh"):
            with self.subTest(script=script_name):
                result = subprocess.run(
                    ["bash", script_name],
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Refusing to", result.stdout)


class TestFeatureRenderingTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request() -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/spots",
                "raw_path": b"/spots",
                "query_string": b"",
                "headers": [],
                "client": ("test", 123),
                "server": ("testserver", 80),
                "root_path": "",
            }
        )

    async def test_find_spots_shows_test_location_in_development(self):
        with mock.patch.object(const, "TEST_FEATURES_ENABLED", True):
            response = await public_html.find_spots_page(self._request())
        self.assertIn('id="filter-test-location"', response.body.decode("utf-8"))

    async def test_find_spots_hides_test_location_in_production(self):
        with mock.patch.object(const, "TEST_FEATURES_ENABLED", False):
            response = await public_html.find_spots_page(self._request())
        self.assertNotIn('id="filter-test-location"', response.body.decode("utf-8"))


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
