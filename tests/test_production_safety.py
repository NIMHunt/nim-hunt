import unittest
from unittest import mock

import constants as const
import main


class ProductionSafetyValidationTest(unittest.TestCase):
    def test_local_development_settings_are_allowed_when_not_in_production(self):
        with mock.patch.object(const, "PRODUCTION_MODE", False), \
             mock.patch.object(const, "DEFAULT_TO_TEST_USER", True), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", True), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True), \
             mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"), \
             mock.patch.object(const, "NIMIQ_HUB_URL", "https://hub.nimiq-testnet.com"), \
             mock.patch.object(const, "SPOT_CANCELLATION_FEE_ADDRESS", "NQ00 NIMHUNT DEV CANCELLATION FEE POOL"):
            main.validate_production_safety()

    def test_production_refuses_test_user_fallback(self):
        with mock.patch.object(const, "PRODUCTION_MODE", True), \
             mock.patch.object(const, "DEFAULT_TO_TEST_USER", True), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", False), \
             mock.patch.object(const, "NIMIQ_NETWORK", "MainAlbatross"), \
             mock.patch.object(const, "NIMIQ_HUB_URL", "https://hub.nimiq.com"), \
             mock.patch.object(const, "SPOT_CANCELLATION_FEE_ADDRESS", "NQ12 PRODUCTION CANCELLATION FEE ADDRESS"):
            with self.assertRaisesRegex(RuntimeError, "DEFAULT_TO_TEST_USER"):
                main.validate_production_safety()

    def test_production_refuses_dev_wallet_settings_and_testnet(self):
        with mock.patch.object(const, "PRODUCTION_MODE", True), \
             mock.patch.object(const, "DEFAULT_TO_TEST_USER", False), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", True), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True), \
             mock.patch.object(const, "NIMIQ_NETWORK", "TestAlbatross"), \
             mock.patch.object(const, "NIMIQ_HUB_URL", "https://hub.nimiq-testnet.com"), \
             mock.patch.object(const, "SPOT_CANCELLATION_FEE_ADDRESS", "NQ00 NIMHUNT DEV CANCELLATION FEE POOL"):
            with self.assertRaisesRegex(RuntimeError, "ALLOW_DEV_WALLET_PLACEHOLDERS"):
                main.validate_production_safety()


    def test_production_refuses_testnet_hub_and_dev_fee_address(self):
        with mock.patch.object(const, "PRODUCTION_MODE", True), \
             mock.patch.object(const, "DEFAULT_TO_TEST_USER", False), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False), \
             mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", False), \
             mock.patch.object(const, "NIMIQ_NETWORK", "MainAlbatross"), \
             mock.patch.object(const, "NIMIQ_HUB_URL", "https://hub.nimiq-testnet.com"), \
             mock.patch.object(const, "SPOT_CANCELLATION_FEE_ADDRESS", "NQ00 NIMHUNT DEV CANCELLATION FEE POOL"):
            with self.assertRaisesRegex(RuntimeError, "NIMIQ_HUB_URL"):
                main.validate_production_safety()
