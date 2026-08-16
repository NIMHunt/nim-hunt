from __future__ import annotations

import unittest
from unittest import mock

import constants as const
import funding_flow


class DesktopTestUserRetirementTest(unittest.TestCase):
    def test_runtime_install_disables_implicit_desktop_identity_even_when_hooks_are_skipped(self):
        """The application must never silently log a desktop browser in as user 0."""
        with (
            mock.patch.object(const, "DEFAULT_TO_TEST_USER", True),
            mock.patch.object(funding_flow, "_INSTALLED", False),
        ):
            # Under pytest the financial/security monkey-patches intentionally
            # stay uninstalled. The identity retirement happens before that
            # guard so this test can verify it without mutating runtime hooks.
            funding_flow.install()
            self.assertFalse(const.DEFAULT_TO_TEST_USER)
            self.assertFalse(funding_flow._INSTALLED)


if __name__ == "__main__":
    unittest.main()
