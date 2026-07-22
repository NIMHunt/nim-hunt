from __future__ import annotations

import unittest
from pathlib import Path


class SpotActionPriorityTest(unittest.TestCase):
    def test_upcoming_spots_show_unavailable_action_before_status_badge(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "static" / "find_spots.js").read_text(encoding="utf-8")
        start = source.index("function shouldShowClaimAction(spot)")
        end = source.index("function claimActionText(spot)", start)
        block = source[start:end]

        upcoming_rule = "if (statusLabel === 'upcoming') return true;"
        inactive_rule = "if (statusLabel !== 'active') return false;"
        self.assertIn(upcoming_rule, block)
        self.assertIn(inactive_rule, block)
        self.assertLess(block.index(upcoming_rule), block.index(inactive_rule))

    def test_upcoming_unavailable_tooltip_explains_not_active(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "static" / "find_spots.js").read_text(encoding="utf-8")
        self.assertIn("if (reason === 'not_active')", source)
        self.assertIn("This spot is not active right now.", source)


if __name__ == "__main__":
    unittest.main()
