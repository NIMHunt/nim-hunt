import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

import constants as const
import main
import public_html
import spoof
import wallet
from starlette.requests import Request

SAFE_PRODUCTION_SETTINGS = {
    "DEPLOYMENT_MODE": "production",
    "PUBLIC_DEPLOYMENT": True,
    "PUBLIC_TESTNET_MODE": False,
    "PRODUCTION_MODE": True,
    "TEST_FEATURES_ENABLED": False,
    "DEFAULT_TO_TEST_USER": False,
    "ALLOW_DEV_WALLET_PLACEHOLDERS": False,
    "ALLOW_DEV_WALLET_SENDS": False,
    "NIMIQ_NETWORK": "MainAlbatross",
    "NIMIQ_NETWORK_ID": 24,
    "NIMIQ_RPC_URL": "https://rpc.nimiqwatch.com",
    "NIMIQ_HUB_URL": "https://hub.nimiq.com",
    "SPOT_CANCELLATION_FEE_ADDRESS": "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF",
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
        derive_command_env = getattr(
            const,
            "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV",
            "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND",
        )
        send_command_env = getattr(
            const,
            "NIMHUNT_NIMIQ_SEND_COMMAND_ENV",
            "NIMHUNT_NIMIQ_SEND_COMMAND",
        )
        stack.enter_context(
            mock.patch.dict(
                os.environ,
                {
                    dev_seed_env: "",
                    default_mnemonic_env: "",
                    derive_command_env: "configured-derive-helper",
                    send_command_env: "configured-send-helper",
                    "NIMHUNT_NIMIQ_MNEMONIC": "private operator mnemonic words",
                    "NIMHUNT_NIMIQ_EXTERNAL_SIGNER": "",
                },
                clear=False,
            )
        )
        for name, value in settings.items():
            stack.enter_context(mock.patch.object(const, name, value))
        stack.enter_context(
            mock.patch.object(main.database, "DB_PATH", "/srv/nimhunt/records.db")
        )
        yield


class ProductionSafetyValidationTest(unittest.TestCase):
    def test_local_development_settings_are_allowed_when_not_in_production(self):
        with patched_settings(
            DEPLOYMENT_MODE="development",
            PUBLIC_DEPLOYMENT=False,
            PUBLIC_TESTNET_MODE=False,
            PRODUCTION_MODE=False,
            TEST_FEATURES_ENABLED=True,
            DEFAULT_TO_TEST_USER=True,
            ALLOW_DEV_WALLET_PLACEHOLDERS=True,
            ALLOW_DEV_WALLET_SENDS=True,
            NIMIQ_NETWORK="TestAlbatross",
            NIMIQ_NETWORK_ID=5,
            NIMIQ_RPC_URL="https://rpc.testnet.nimiqwatch.com/",
            NIMIQ_HUB_URL="https://hub.nimiq-testnet.com",
            SPOT_CANCELLATION_FEE_ADDRESS="NQ00 NIMHUNT DEV CANCELLATION FEE POOL",
        ):
            main.validate_deployment_safety()

    def test_production_refuses_test_features_flag(self):
        with patched_settings(TEST_FEATURES_ENABLED=True):
            with self.assertRaisesRegex(RuntimeError, "TEST_FEATURES_ENABLED"):
                main.validate_deployment_safety()

    def test_production_refuses_unencrypted_dev_seed(self):
        dev_seed_env = getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED")
        with patched_settings(), mock.patch.dict(os.environ, {dev_seed_env: "unsafe seed"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, dev_seed_env):
                main.validate_deployment_safety()

    def test_production_refuses_public_default_test_mnemonic(self):
        env_name = getattr(
            const,
            "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV",
            "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
        )
        with patched_settings(), mock.patch.dict(os.environ, {env_name: "1"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, env_name):
                main.validate_deployment_safety()

    def test_production_environment_disables_test_helpers_at_import(self):
        command = (
            "import constants; "
            "print(constants.DEPLOYMENT_MODE, int(constants.PUBLIC_DEPLOYMENT), "
            "int(constants.PRODUCTION_MODE), int(constants.TEST_FEATURES_ENABLED), "
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
        self.assertEqual(result.stdout.strip(), "production 1 1 0 0 0")

    def test_development_environment_keeps_test_helpers_at_import(self):
        command = (
            "import constants; "
            "print(constants.DEPLOYMENT_MODE, int(constants.PUBLIC_DEPLOYMENT), "
            "int(constants.PRODUCTION_MODE), int(constants.TEST_FEATURES_ENABLED), "
            "int(constants.DEFAULT_TO_TEST_USER), int(constants.ALLOW_DEV_WALLET_PLACEHOLDERS))"
        )
        environment = os.environ.copy()
        environment.pop("NIMHUNT_DEPLOYMENT_MODE", None)
        environment.pop("NIMHUNT_PRODUCTION", None)
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.stdout.strip(), "development 0 0 1 1 1")

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
                main.validate_deployment_safety()

    def test_production_refuses_dev_wallet_settings_and_testnet(self):
        with patched_settings(
            ALLOW_DEV_WALLET_PLACEHOLDERS=True,
            ALLOW_DEV_WALLET_SENDS=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "ALLOW_DEV_WALLET_PLACEHOLDERS"):
                main.validate_deployment_safety()

    def test_production_refuses_malformed_fee_address(self):
        with patched_settings(SPOT_CANCELLATION_FEE_ADDRESS="NQ12 NOT A REAL ADDRESS"):
            with self.assertRaisesRegex(RuntimeError, "operator-controlled address"):
                main.validate_deployment_safety()

    def test_production_refuses_testnet_hub_and_dev_fee_address(self):
        with patched_settings(
            NIMIQ_HUB_URL="https://hub.nimiq-testnet.com",
            SPOT_CANCELLATION_FEE_ADDRESS="NQ00 NIMHUNT DEV CANCELLATION FEE POOL",
        ):
            with self.assertRaisesRegex(RuntimeError, "NIMIQ_HUB_URL"):
                main.validate_deployment_safety()

    def test_network_defaults_match_testalbatross(self):
        command = (
            "import constants; "
            "print(constants.NIMIQ_NETWORK, constants.NIMIQ_NETWORK_ID, "
            "constants.NIMIQ_RPC_URL, constants.NIMIQ_HUB_URL)"
        )
        environment = os.environ.copy()
        for name in (
            "NIMHUNT_DEPLOYMENT_MODE",
            "NIMHUNT_PRODUCTION",
            "NIMHUNT_NIMIQ_NETWORK",
            "NIMHUNT_NIMIQ_RPC_URL",
            "NIMHUNT_NIMIQ_HUB_URL",
        ):
            environment.pop(name, None)
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(
            result.stdout.strip(),
            "TestAlbatross 5 https://rpc.testnet.nimiqwatch.com/ https://hub.nimiq-testnet.com",
        )

    def test_production_environment_can_configure_mainnet_without_source_edits(self):
        command = (
            "import constants; "
            "print(constants.NIMIQ_NETWORK, constants.NIMIQ_NETWORK_ID, "
            "constants.NIMIQ_RPC_URL, constants.NIMIQ_HUB_URL, "
            "constants.SPOT_CANCELLATION_FEE_ADDRESS)"
        )
        environment = os.environ.copy()
        environment.update(
            {
                "NIMHUNT_PRODUCTION": "1",
                "NIMHUNT_NIMIQ_NETWORK": "MainAlbatross",
                "NIMHUNT_NIMIQ_RPC_URL": "https://rpc.example.invalid",
                "NIMHUNT_NIMIQ_HUB_URL": "https://hub.nimiq.com",
                "NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS": "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF",
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(
            result.stdout.strip(),
            "MainAlbatross 24 https://rpc.example.invalid https://hub.nimiq.com "
            "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF",
        )

    def test_database_path_can_be_configured_for_persistent_storage(self):
        command = "import database; print(database.DB_PATH)"
        environment = os.environ.copy()
        environment["NIMHUNT_DB_PATH"] = "/srv/nimhunt/records.db"
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.stdout.strip(), "/srv/nimhunt/records.db")

    def test_application_assets_resolve_outside_project_working_directory(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        command = (
            "import asyncio, constants, main, public_html; "
            "favicon = asyncio.run(public_html.favicon()); "
            "print(int(constants.STATIC_DIR.is_dir()), "
            "int(constants.TEMPLATES_DIR.is_dir()), favicon.path)"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = project_root
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, "-c", command],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
                cwd=working_directory,
            )

        static_ok, templates_ok, favicon_path = result.stdout.strip().split(" ", 2)
        self.assertEqual((static_ok, templates_ok), ("1", "1"))
        self.assertEqual(
            os.path.realpath(favicon_path),
            os.path.realpath(os.path.join(project_root, "static", "favicon.svg")),
        )

    def test_runtime_refuses_mismatched_network_id_even_in_development(self):
        with patched_settings(
            DEPLOYMENT_MODE="development",
            PUBLIC_DEPLOYMENT=False,
            PUBLIC_TESTNET_MODE=False,
            PRODUCTION_MODE=False,
            NIMIQ_NETWORK="TestAlbatross",
            NIMIQ_NETWORK_ID=6,
            NIMIQ_RPC_URL="https://rpc.testnet.nimiqwatch.com/",
        ):
            with self.assertRaisesRegex(RuntimeError, "NIMIQ_NETWORK_ID must be 5"):
                main.validate_deployment_safety()

    def test_production_requires_signing_commands(self):
        derive_command_env = getattr(
            const,
            "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV",
            "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND",
        )
        with patched_settings(), mock.patch.dict(
            os.environ,
            {derive_command_env: ""},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, derive_command_env):
                main.validate_deployment_safety()


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
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(spoof, "_remove_existing_database_files") as remove_database,
        ):
            with self.assertRaisesRegex(RuntimeError, "public deployment mode"):
                await spoof.seed_mock_data()
        remove_database.assert_not_called()

    async def test_mock_seed_refuses_database_already_bound_to_public_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.executemany(
                    "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                    (
                        ("nimiq_network", "TestAlbatross"),
                        ("nimiq_network_id", "5"),
                        ("deployment_mode", "public-testnet"),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with (
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", False),
                mock.patch.object(spoof.schema, "DB_PATH", str(path)),
                mock.patch.object(spoof, "_remove_existing_database_files") as remove_database,
            ):
                with self.assertRaisesRegex(RuntimeError, "bound to public deployment mode"):
                    await spoof.seed_mock_data()
            remove_database.assert_not_called()

    async def test_mock_seed_refuses_incomplete_deployment_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                    ("deployment_mode", "development"),
                )
                connection.commit()
            finally:
                connection.close()

            with (
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", False),
                mock.patch.object(spoof.schema, "DB_PATH", str(path)),
                mock.patch.object(spoof, "_remove_existing_database_files") as remove_database,
            ):
                with self.assertRaisesRegex(RuntimeError, "metadata is incomplete"):
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

    def test_shell_development_helpers_refuse_public_testnet(self):
        environment = os.environ.copy()
        environment["NIMHUNT_DEPLOYMENT_MODE"] = "public-testnet"
        environment.pop("NIMHUNT_PRODUCTION", None)
        for script_name in ("nimhunt_start_dev.sh", "nimhunt_reset_mock_data.sh"):
            with self.subTest(script=script_name):
                result = subprocess.run(
                    ["bash", script_name],
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("public deployment mode", result.stdout)

    def test_shell_development_helpers_are_project_path_independent(self):
        for script_name in ("nimhunt_start_dev.sh", "nimhunt_reset_mock_data.sh"):
            with self.subTest(script=script_name):
                source = Path(script_name).read_text(encoding="utf-8")
                self.assertIn("BASH_SOURCE", source)
                self.assertIn("NIMHUNT_PROJECT_DIR", source)
                self.assertNotIn("/home/jakorah", source)


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
    async def test_production_startup_requires_successful_initial_financial_passes(self):
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(main, "validate_deployment_safety"),
            mock.patch.object(main, "verify_public_signing_access"),
            mock.patch.object(main, "verify_public_rpc_network", mock.AsyncMock()),
            mock.patch.object(main.database, "init_db", mock.AsyncMock()),
            mock.patch.object(main.cache, "start_cache_refresher", mock.AsyncMock()),
            mock.patch.object(
                main.settlement_updater,
                "start_settlement_refresher",
                mock.AsyncMock(),
            ) as start_settlement,
            mock.patch.object(
                main.trans_updater,
                "start_transaction_refresher",
                mock.AsyncMock(),
            ) as start_transactions,
        ):
            await main.startup()

        start_settlement.assert_awaited_once_with(
            run_immediately=True,
            fail_on_initial_error=True,
        )
        start_transactions.assert_awaited_once_with(
            run_immediately=True,
            fail_on_initial_error=True,
        )

    async def test_shutdown_attempts_every_service_when_one_stop_fails(self):
        with (
            mock.patch.object(
                main.trans_updater,
                "stop_transaction_refresher",
                mock.AsyncMock(side_effect=RuntimeError("stop failed")),
            ) as stop_transactions,
            mock.patch.object(
                main.settlement_updater,
                "stop_settlement_refresher",
                mock.AsyncMock(),
            ) as stop_settlement,
            mock.patch.object(
                main.cache,
                "stop_cache_refresher",
                mock.AsyncMock(),
            ) as stop_cache,
            mock.patch.object(main.logger, "exception"),
        ):
            await main.shutdown()

        stop_transactions.assert_awaited_once_with()
        stop_settlement.assert_awaited_once_with()
        stop_cache.assert_awaited_once_with()

    async def test_startup_cleans_up_services_after_partial_failure(self):
        failure = RuntimeError("settlement startup failed")
        shutdown = mock.AsyncMock()
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(main, "validate_deployment_safety"),
            mock.patch.object(main, "verify_public_signing_access"),
            mock.patch.object(main, "verify_public_rpc_network", mock.AsyncMock()),
            mock.patch.object(main.database, "init_db", mock.AsyncMock()),
            mock.patch.object(main.cache, "start_cache_refresher", mock.AsyncMock()),
            mock.patch.object(
                main.settlement_updater,
                "start_settlement_refresher",
                mock.AsyncMock(side_effect=failure),
            ),
            mock.patch.object(main, "shutdown", shutdown),
        ):
            with self.assertRaisesRegex(RuntimeError, "settlement startup failed"):
                await main.startup()

        shutdown.assert_awaited_once_with()

    async def test_strict_settlement_startup_propagates_initial_failure(self):
        error = RuntimeError("settlement unavailable")
        with (
            mock.patch.object(
                main.settlement_updater,
                "run_settlement_pass",
                mock.AsyncMock(side_effect=error),
            ),
            mock.patch.object(main.settlement_updater.logger, "exception"),
        ):
            with self.assertRaisesRegex(RuntimeError, "settlement unavailable"):
                await main.settlement_updater.start_settlement_refresher(
                    run_immediately=True,
                    fail_on_initial_error=True,
                )

        self.assertIsNone(main.settlement_updater._SETTLEMENT_TASK)
        main.settlement_updater._SETTLEMENT_STOP_EVENT = None

    async def test_strict_settlement_startup_rejects_unsuccessful_result(self):
        with (
            mock.patch.object(
                main.settlement_updater,
                "run_settlement_pass",
                mock.AsyncMock(return_value={"ok": False, "reason": "payout failed"}),
            ),
            mock.patch.object(main.settlement_updater.logger, "exception"),
        ):
            with self.assertRaisesRegex(RuntimeError, "reported failure"):
                await main.settlement_updater.start_settlement_refresher(
                    run_immediately=True,
                    fail_on_initial_error=True,
                )

        self.assertIsNone(main.settlement_updater._SETTLEMENT_TASK)
        main.settlement_updater._SETTLEMENT_STOP_EVENT = None

    async def test_strict_transaction_startup_propagates_initial_failure(self):
        error = RuntimeError("transaction RPC unavailable")
        with (
            mock.patch.object(
                main.trans_updater,
                "check_pending_transactions",
                mock.AsyncMock(side_effect=error),
            ),
            mock.patch.object(main.trans_updater.logger, "exception"),
        ):
            with self.assertRaisesRegex(RuntimeError, "transaction RPC unavailable"):
                await main.trans_updater.start_transaction_refresher(
                    run_immediately=True,
                    fail_on_initial_error=True,
                )

        self.assertIsNone(main.trans_updater._TRANS_CHECK_TASK)
        main.trans_updater._TRANS_CHECK_STOP_EVENT = None

    async def test_strict_transaction_startup_rejects_unsuccessful_result(self):
        with (
            mock.patch.object(
                main.trans_updater,
                "check_pending_transactions",
                mock.AsyncMock(return_value={"ok": False, "reason": "RPC failed"}),
            ),
            mock.patch.object(main.trans_updater.logger, "exception"),
        ):
            with self.assertRaisesRegex(RuntimeError, "reported failure"):
                await main.trans_updater.start_transaction_refresher(
                    run_immediately=True,
                    fail_on_initial_error=True,
                )

        self.assertIsNone(main.trans_updater._TRANS_CHECK_TASK)
        main.trans_updater._TRANS_CHECK_STOP_EVENT = None

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


class CancellationFeeEnvironmentTest(unittest.TestCase):
    def _read_fee(self, value: str) -> subprocess.CompletedProcess[str]:
        project_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root)
        environment["NIMHUNT_SPOT_CANCELLATION_FEE_NIM"] = value
        return subprocess.run(
            [sys.executable, "-c", "import constants; print(constants.SPOT_CANCELLATION_FEE)"],
            cwd=project_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cancellation_fee_accepts_exact_nim_amount(self):
        result = self._read_fee("1.25")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "125000")

    def test_cancellation_fee_rejects_sub_luna_precision(self):
        result = self._read_fee("0.000001")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot use more than 5 decimal places", result.stderr)
