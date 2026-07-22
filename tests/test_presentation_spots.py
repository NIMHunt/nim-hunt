from __future__ import annotations

import unittest
from pathlib import Path

import constants as const


class SpotTitlePresentationTest(unittest.TestCase):
    def test_spot_title_limit_is_fifty_percent_larger(self):
        self.assertEqual(const.DISPLAY_NAME_MAX_CHARS, 18)
        self.assertEqual(const.SPOT_TITLE_MAX_CHARS, 27)

    def test_list_titles_use_single_line_ellipsis_only_when_requested(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "static" / "spot_ui.js").read_text(encoding="utf-8")
        css = (root / "static" / "home.css").read_text(encoding="utf-8")
        find_spots = (root / "static" / "find_spots.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("{ truncate = false } = {}", source)
        self.assertIn("{ truncate: true }", source)
        self.assertIn("{ truncate: true }", find_spots)
        self.assertIn("titleEl.title = fullTitle;", source)
        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("white-space: nowrap", css)
        self.assertIn("titleEl.append(lockIcon);", source)
        self.assertNotIn("document.createTextNode(' '), lockIcon", source)
        self.assertLess(
            source.index("titleEl.append(lockIcon);"),
            source.index("titleEl.append(titleText);"),
        )
        self.assertIn("flex: 0 1 auto;", css)
        self.assertIn("margin-left: 0;", css)
        self.assertIn("margin-right: 0.25em;", css)


if __name__ == "__main__":
    unittest.main()
