import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import constants as const
import database
import main
import trans_updater
import wallet

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_FEE_ADDRESS = "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "NIMHUNT_DEPLOYMENT_MODE",
        "NIMHUNT_PRODUCTION",
        "NIMHUNT_NIMIQ_NETWORK",
        "NIMHUNT_NIMIQ_NETWORK_ID",
        "NIMHUNT_NIMIQ_RPC_URL",
        "NIMHUNT_NIMIQ_HUB_URL",
        "NIMHUNT_NIMIQ_TRANSACTION_FEE",
        "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND",
        "NIMHUNT_NIMIQ_SEND_COMMAND",
        "NIMHUNT_NIMIQ_MNEMONIC",
        "NIMHUNT_NIMIQ_EXTERNAL_SIGNER",
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
        "NIMHUNT_DEV_MASTER_SEED",
        "NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM",
        "NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM",
        "NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS",
        "NIMHUNT_DB_PATH",
    ):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    return environment


def valid_public_environment(mode: str) -> dict[str, str]:
    environment = clean_environment()
    if mode == "public-testnet":
        network = "TestAlbatross"
        network_id = "5"
        rpc = "https://rpc.testnet.nimiqwatch.com/"
        hub = "https://hub.nimiq-testnet.com"
    else:
        network = "MainAlbatross"
        network_id = "24"
        rpc = "https://rpc.nimiqwatch.com"
        hub = "https://hub.nimiq.com"
    environment.update(
        {
            "NIMHUNT_DEPLOYMENT_MODE": mode,
            "NIMHUNT_NIMIQ_NETWORK": network,
            "NIMHUNT_NIMIQ_NETWORK_ID": network_id,
            "NIMHUNT_NIMIQ_RPC_URL": rpc,
            "NIMHUNT_NIMIQ_HUB_URL": hub,
            "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND": "node /app/helpers/nimiq_helper.mjs",
            "NIMHUNT_NIMIQ_SEND_COMMAND": "node /app/helpers/nimiq_helper.mjs",
            "NIMHUNT_NIMIQ_MNEMONIC": "private operator mnemonic words",
            "NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS": VALID_FEE_ADDRESS,
            "NIMHUNT_DB_PATH": "/srv/nimhunt/records.db",
        }
    )
    return environment


def run_python(code: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


class DeploymentModeParsingTest(unittest.TestCase):
    def test_default_remains_local_development(self):
        result = run_python(
            "import constants as c; print(c.DEPLOYMENT_MODE, int(c.PUBLIC_DEPLOYMENT), "
            "int(c.PRODUCTION_MODE), int(c.TEST_FEATURES_ENABLED))",
            clean_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "development 0 0 1")

    def test_legacy_production_maps_to_production(self):
        environment = clean_environment()
        environment["NIMHUNT_PRODUCTION"] = "1"
        result = run_python(
            "import constants as c; print(c.DEPLOYMENT_MODE, int(c.PUBLIC_DEPLOYMENT), "
            "int(c.PRODUCTION_MODE), int(c.TEST_FEATURES_ENABLED))",
            environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "production 1 1 0")

    def test_public_testnet_disables_test_behaviour(self):
        environment = valid_public_environment("public-testnet")
        result = run_python(
            "import constants as c; print(c.DEPLOYMENT_MODE, int(c.PUBLIC_DEPLOYMENT), "
            "int(c.PRODUCTION_MODE), int(c.TEST_FEATURES_ENABLED), "
            "int(c.DEFAULT_TO_TEST_USER), int(c.ALLOW_DEV_WALLET_PLACEHOLDERS))",
            environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "public-testnet 1 0 0 0 0")

    def test_unknown_mode_fails_during_configuration_import(self):
        environment = clean_environment()
        environment["NIMHUNT_DEPLOYMENT_MODE"] = "staging-ish"
        result = run_python("import constants", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be development, public-testnet, or production", result.stderr)

    def test_default_helper_accepts_documented_true_alias(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "NIMHUNT_NIMIQ_MNEMONIC": "",
                    "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC": "yes",
                },
                clear=False,
            ),
            mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
        ):
            self.assertTrue(trans_updater._helper_seed_configured())

    def test_creation_fees_are_independent_and_zero_is_supported(self):
        environment = clean_environment()
        environment.update(
            {
                "NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM": "0",
                "NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM": "2.5",
            }
        )
        result = run_python(
            "import constants as c; print(c.STANDARD_SPOT_CREATION_FEE, "
            "c.PRIZEDRAW_SPOT_CREATION_FEE)",
            environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0 250000")

    def test_conflicting_legacy_and_preferred_settings_fail(self):
        environment = clean_environment()
        environment.update(
            {
                "NIMHUNT_DEPLOYMENT_MODE": "public-testnet",
                "NIMHUNT_PRODUCTION": "1",
            }
        )
        result = run_python("import constants", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with NIMHUNT_PRODUCTION", result.stderr)


class PublicDeploymentValidationTest(unittest.TestCase):
    def test_public_testnet_accepts_safe_testalbatross_configuration(self):
        result = run_python(
            "import main; main.validate_deployment_safety(); print('ok')",
            valid_public_environment("public-testnet"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_public_testnet_rejects_mainalbatross(self):
        environment = valid_public_environment("public-testnet")
        environment.update(
            {
                "NIMHUNT_NIMIQ_NETWORK": "MainAlbatross",
                "NIMHUNT_NIMIQ_NETWORK_ID": "24",
                "NIMHUNT_NIMIQ_RPC_URL": "https://rpc.nimiqwatch.com",
                "NIMHUNT_NIMIQ_HUB_URL": "https://hub.nimiq.com",
            }
        )
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public-testnet requires TestAlbatross", result.stderr)

    def test_public_testnet_rejects_public_default_mnemonic_flag(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC"] = "1"
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be enabled", result.stderr)

    def test_public_testnet_rejects_public_default_mnemonic_when_set_directly(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_NIMIQ_MNEMONIC"] = (
            "abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about"
        )
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public default test mnemonic", result.stderr)

    def test_public_modes_reject_nonzero_network_fee_without_a_fee_reserve(self):
        with (
            mock.patch.object(const, "DEPLOYMENT_MODE", "public-testnet"),
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
            mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
            mock.patch.object(const, "NIMIQ_RPC_URL", "https://rpc.testnet.nimiqwatch.com/"),
            mock.patch.object(const, "NIMIQ_HUB_URL", "https://hub.nimiq-testnet.com"),
            mock.patch.object(const, "NIMIQ_TRANSACTION_FEE", 1),
            mock.patch.object(const, "TEST_FEATURES_ENABLED", False),
            mock.patch.object(const, "DEFAULT_TO_TEST_USER", False),
            mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False),
            mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", False),
            mock.patch.object(const, "SPOT_CANCELLATION_FEE_ADDRESS", VALID_FEE_ADDRESS),
            mock.patch.object(main.database, "DB_PATH", "/srv/nimhunt/records.db"),
            mock.patch.dict(
                os.environ,
                {
                    "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND": "node /app/helpers/nimiq_helper.mjs",
                    "NIMHUNT_NIMIQ_SEND_COMMAND": "node /app/helpers/nimiq_helper.mjs",
                    "NIMHUNT_NIMIQ_MNEMONIC": "private operator mnemonic words",
                    "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC": "",
                    "NIMHUNT_DEV_MASTER_SEED": "",
                },
                clear=False,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "NIMIQ_TRANSACTION_FEE must remain 0"):
                main.validate_deployment_safety()

    def test_public_testnet_rejects_public_development_fee_address(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS"] = const.DEV_PLATFORM_FEE_ADDRESS
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public development fee address", result.stderr)

    def test_public_testnet_rejects_development_seed(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_DEV_MASTER_SEED"] = "development only"
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NIMHUNT_DEV_MASTER_SEED", result.stderr)

    def test_public_testnet_requires_private_signing_material(self):
        environment = valid_public_environment("public-testnet")
        environment.pop("NIMHUNT_NIMIQ_MNEMONIC")
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be configured for the bundled Nimiq helper", result.stderr)

    def test_key_managed_external_signer_can_replace_mnemonic(self):
        environment = valid_public_environment("public-testnet")
        environment.pop("NIMHUNT_NIMIQ_MNEMONIC")
        environment.update(
            {
                "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND": "/opt/signer derive",
                "NIMHUNT_NIMIQ_SEND_COMMAND": "/opt/signer send",
                "NIMHUNT_NIMIQ_EXTERNAL_SIGNER": "1",
            }
        )
        result = run_python(
            "import main; main.validate_deployment_safety(); print('ok')", environment
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_testnet_rejects_development_wallet_shortcuts(self):
        with (
            mock.patch.object(const, "DEPLOYMENT_MODE", "public-testnet"),
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
            mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
            mock.patch.object(const, "NIMIQ_RPC_URL", "https://rpc.testnet.nimiqwatch.com/"),
            mock.patch.object(const, "NIMIQ_HUB_URL", "https://hub.nimiq-testnet.com"),
            mock.patch.object(const, "TEST_FEATURES_ENABLED", False),
            mock.patch.object(const, "DEFAULT_TO_TEST_USER", False),
            mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", True),
            mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
            mock.patch.object(const, "SPOT_CANCELLATION_FEE_ADDRESS", VALID_FEE_ADDRESS),
            mock.patch.object(main.database, "DB_PATH", "/srv/nimhunt/records.db"),
            mock.patch.dict(
                os.environ,
                {
                    "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND": "node /app/helpers/nimiq_helper.mjs",
                    "NIMHUNT_NIMIQ_SEND_COMMAND": "node /app/helpers/nimiq_helper.mjs",
                    "NIMHUNT_NIMIQ_MNEMONIC": "private operator mnemonic words",
                    "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC": "",
                    "NIMHUNT_DEV_MASTER_SEED": "",
                },
                clear=False,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "ALLOW_DEV_WALLET_PLACEHOLDERS"):
                main.validate_deployment_safety()

    def test_public_testnet_rejects_detectable_mainnet_endpoints(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_NIMIQ_RPC_URL"] = "https://rpc.nimiqwatch.com"
        environment["NIMHUNT_NIMIQ_HUB_URL"] = "https://hub.nimiq.com"
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mainnet RPC", result.stderr)
        self.assertIn("TestAlbatross/testnet Hub", result.stderr)

    def test_public_testnet_allows_a_custom_https_rpc_hostname(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_NIMIQ_RPC_URL"] = "https://rpc.example.invalid"
        result = run_python(
            "import main; main.validate_deployment_safety(); print('ok')", environment
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_modes_require_an_absolute_database_path(self):
        environment = valid_public_environment("public-testnet")
        environment["NIMHUNT_DB_PATH"] = "records.db"
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute persistent path", result.stderr)

    def test_production_accepts_safe_mainalbatross_configuration(self):
        result = run_python(
            "import main; main.validate_deployment_safety(); print('ok')",
            valid_public_environment("production"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_public_signer_probe_checks_both_helper_commands(self):
        validated = mock.Mock(address=VALID_FEE_ADDRESS)
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                main.wallet, "validate_public_signer_configuration", return_value=validated
            ) as validate_signer,
        ):
            main.verify_public_signing_access()
        validate_signer.assert_called_once_with()

    def test_public_signer_probe_fails_startup_cleanly(self):
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                main.wallet,
                "validate_public_signer_configuration",
                side_effect=RuntimeError("secret-specific helper detail"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "signer validation failed") as raised:
                main.verify_public_signing_access()
        self.assertNotIn("secret-specific", str(raised.exception))

    def test_production_rejects_testnet_endpoints_even_with_mainalbatross(self):
        environment = valid_public_environment("production")
        environment["NIMHUNT_NIMIQ_RPC_URL"] = "https://rpc.testnet.nimiqwatch.com/"
        environment["NIMHUNT_NIMIQ_HUB_URL"] = "https://hub.nimiq-testnet.com"
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mainnet RPC", result.stderr)
        self.assertIn("mainnet Hub", result.stderr)

    def test_production_rejects_testalbatross_and_testnet_endpoints(self):
        environment = valid_public_environment("production")
        environment.update(
            {
                "NIMHUNT_NIMIQ_NETWORK": "TestAlbatross",
                "NIMHUNT_NIMIQ_NETWORK_ID": "5",
                "NIMHUNT_NIMIQ_RPC_URL": "https://rpc.testnet.nimiqwatch.com/",
                "NIMHUNT_NIMIQ_HUB_URL": "https://hub.nimiq-testnet.com",
            }
        )
        result = run_python("import main; main.validate_deployment_safety()", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production requires MainAlbatross", result.stderr)


class PublicRuntimeProbeTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_rpc_probe_accepts_matching_network_id(self):
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
            mock.patch.object(const, "NIMIQ_RPC_URL", "https://rpc.testnet.example"),
            mock.patch.object(const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12),
            mock.patch.object(
                main.trans_updater,
                "verify_configured_rpc_network",
                mock.AsyncMock(return_value=5),
            ) as verify_rpc,
        ):
            await main.verify_public_rpc_network()

        verify_rpc.assert_awaited_once_with(
            expected_network_id=5,
            rpc_url="https://rpc.testnet.example",
            timeout_seconds=12,
        )

    async def test_public_rpc_probe_hides_low_level_endpoint_details(self):
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                main.trans_updater,
                "verify_configured_rpc_network",
                mock.AsyncMock(side_effect=RuntimeError("low-level response detail")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "RPC network validation failed") as raised:
                await main.verify_public_rpc_network()
        self.assertNotIn("low-level response", str(raised.exception))

    async def test_rpc_network_verifier_rejects_wrong_live_network(self):
        with mock.patch.object(
            trans_updater,
            "get_configured_rpc_network_id",
            mock.AsyncMock(return_value=24),
        ):
            with self.assertRaisesRegex(RuntimeError, "network ID 24, expected 5"):
                await trans_updater.verify_configured_rpc_network(
                    expected_network_id=5,
                    rpc_url="https://rpc.example",
                )

    def test_rpc_network_id_parser_accepts_pos_response_shape(self):
        self.assertEqual(
            trans_updater._extract_rpc_network_id(
                {"data": {"networkId": 5}, "metadata": {"source": "test"}}
            ),
            5,
        )


class SignerCommandSafetyTest(unittest.TestCase):
    def test_public_signer_validation_checks_derive_and_send_commands(self):
        response = {
            "ok": True,
            "network": "TestAlbatross",
            "address": VALID_FEE_ADDRESS,
            "key_index": 0,
            "key_path": "m/44'/242'/0'/0'",
            "key_version": 1,
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND": "derive-helper",
                    "NIMHUNT_NIMIQ_SEND_COMMAND": "send-helper",
                },
                clear=False,
            ),
            mock.patch.object(wallet, "_run_json_command", return_value=response) as run_helper,
        ):
            result = wallet.validate_public_signer_configuration()

        self.assertEqual(result.address, VALID_FEE_ADDRESS)
        self.assertEqual(run_helper.call_count, 2)
        self.assertEqual(
            {call.args[0][0] for call in run_helper.call_args_list},
            {"derive-helper", "send-helper"},
        )
        for call in run_helper.call_args_list:
            self.assertEqual(call.args[1]["action"], "validate_signer_configuration")

    def test_public_signer_validation_rejects_inconsistent_commands(self):
        responses = [
            {"ok": True, "network": "TestAlbatross", "address": VALID_FEE_ADDRESS},
            {
                "ok": True,
                "network": "TestAlbatross",
                "address": "NQ30 AMQ0 TJEX 5922 0KK3 2F55 SYHB HBT2 7BNX",
            },
        ]
        with (
            mock.patch.dict(
                os.environ,
                {
                    "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND": "derive-helper",
                    "NIMHUNT_NIMIQ_SEND_COMMAND": "send-helper",
                },
                clear=False,
            ),
            mock.patch.object(wallet, "_run_json_command", side_effect=responses),
        ):
            with self.assertRaisesRegex(wallet.WalletConfigError, "same signer key"):
                wallet.validate_public_signer_configuration()

    def test_helper_errors_redact_configured_secrets(self):
        secret = "alpha beta gamma private words"
        completed = subprocess.CompletedProcess(
            args=["helper"],
            returncode=1,
            stdout="",
            stderr=f"failed while loading {secret}",
        )
        with (
            mock.patch.dict(os.environ, {"NIMHUNT_NIMIQ_MNEMONIC": secret}, clear=False),
            mock.patch.object(wallet.subprocess, "run", return_value=completed),
        ):
            with self.assertRaises(wallet.WalletConfigError) as raised:
                wallet._run_json_command(["helper"], {"action": "test"})
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))


class DatabaseNetworkIdentityTest(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_database_records_network_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.db"
            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "public-testnet"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            ):
                await database.init_db()

            connection = sqlite3.connect(path)
            try:
                rows = dict(connection.execute("SELECT key, value FROM app_metadata"))
            finally:
                connection.close()
            self.assertEqual(rows["nimiq_network"], "TestAlbatross")
            self.assertEqual(rows["nimiq_network_id"], "5")
            self.assertEqual(rows["deployment_mode"], "public-testnet")

    async def test_testnet_database_cannot_be_opened_as_mainnet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.db"
            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "development"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", False),
            ):
                await database.init_db()

            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "MainAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 24),
                mock.patch.object(const, "DEPLOYMENT_MODE", "production"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            ):
                with self.assertRaisesRegex(RuntimeError, "database network mismatch"):
                    await database.init_db()

    async def test_development_database_cannot_be_exposed_as_public_testnet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.db"
            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "development"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", False),
            ):
                await database.init_db()

            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "public-testnet"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            ):
                with self.assertRaisesRegex(RuntimeError, "deployment-mode mismatch"):
                    await database.init_db()

    async def test_existing_development_database_is_bound_without_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.db"
            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "development"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", False),
            ):
                await database.init_db()

            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TABLE app_metadata")
                connection.commit()
            finally:
                connection.close()

            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "development"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", False),
            ):
                await database.init_db()

            connection = sqlite3.connect(path)
            try:
                rows = dict(connection.execute("SELECT key, value FROM app_metadata"))
            finally:
                connection.close()
            self.assertEqual(rows["deployment_mode"], "development")

    async def test_existing_unbound_database_is_rejected_in_public_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE user (id INTEGER PRIMARY KEY)")
                connection.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION}")
                connection.commit()
            finally:
                connection.close()

            with (
                mock.patch.object(database, "DB_PATH", str(path)),
                mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"),
                mock.patch.object(const, "NIMIQ_NETWORK_ID", 5),
                mock.patch.object(const, "DEPLOYMENT_MODE", "public-testnet"),
                mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            ):
                with self.assertRaisesRegex(RuntimeError, "no recorded Nimiq network identity"):
                    await database.init_db()


class DeploymentHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_response_contains_only_safe_operational_fields(self):
        response = await main.healthz()
        payload = json.loads(response.body)
        self.assertEqual(set(payload), {"ok", "deployment_mode", "network"})
        self.assertTrue(payload["ok"])


class RailwayDeploymentConfigTest(unittest.TestCase):
    def test_railway_uses_one_worker_port_healthcheck_and_graceful_drain(self):
        config = json.loads((PROJECT_ROOT / "railway.json").read_text(encoding="utf-8"))
        deploy = config["deploy"]
        self.assertIn("--port $PORT", deploy["startCommand"])
        self.assertIn("--workers 1", deploy["startCommand"])
        self.assertEqual(deploy["healthcheckPath"], "/healthz")
        self.assertEqual(str(deploy["drainingSeconds"]), "30")

    def test_railway_runtime_versions_are_pinned(self):
        mise = (PROJECT_ROOT / "mise.toml").read_text(encoding="utf-8")
        self.assertIn('python = "3.11"', mise)
        self.assertIn('node = "20"', mise)
