from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_exact(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}: found {count}")
    path.write_text(source.replace(old, new), encoding="utf-8")


replace_exact(
    ROOT / "constants.py",
    """# Spot title validation. A draft SPOT can be created with only a title;\n# the remaining fields are filled on the full Create Spot form.\nSPOT_TITLE_MIN_CHARS = 3\nSPOT_TITLE_MAX_CHARS = DISPLAY_NAME_MAX_CHARS\n""",
    """# Spot title validation. A draft SPOT can be created with only a title;\n# the remaining fields are filled on the full Create Spot form. Keep this\n# independent from display names because useful location/event titles are longer.\nSPOT_TITLE_MIN_CHARS = 3\nSPOT_TITLE_MAX_CHARS = 27\n""",
)

replace_exact(
    ROOT / "public_html.py",
    '_ASSET_VERSION = "history-disclaimer-v1-20260722"',
    '_ASSET_VERSION = "presentation-spots-v1-20260722"',
)

spot_ui = ROOT / "static" / "spot_ui.js"
replace_exact(
    spot_ui,
    """export function appendSpotTitleWithLock(titleEl, spot) {\n    if (!titleEl) return;\n    titleEl.textContent = spot.title || SPOT_TEXT.fallbackTitle;\n    if (spot.use_password) {\n        titleEl.append(document.createTextNode(' '), createPasswordRequiredIcon());\n    }\n}\n""",
    """export function appendSpotTitleWithLock(\n    titleEl,\n    spot,\n    { truncate = false } = {},\n) {\n    if (!titleEl) return;\n\n    const fullTitle = String(spot.title || SPOT_TEXT.fallbackTitle);\n    const titleText = document.createElement('span');\n    titleText.className = 'spot-title-text';\n    titleText.textContent = fullTitle;\n\n    titleEl.replaceChildren();\n    titleEl.removeAttribute('title');\n    titleEl.removeAttribute('aria-label');\n    titleEl.classList.toggle('is-truncated-title', Boolean(truncate));\n\n    if (truncate) {\n        titleEl.title = fullTitle;\n        titleEl.setAttribute(\n            'aria-label',\n            `${fullTitle}${spot.use_password ? '. Requires a password.' : ''}`,\n        );\n    }\n\n    titleEl.append(titleText);\n    if (spot.use_password) {\n        const lockIcon = createPasswordRequiredIcon();\n        titleEl.append(document.createTextNode(' '), lockIcon);\n    }\n}\n""",
)

replace_exact(
    spot_ui,
    "    appendSpotTitleWithLock(title, spot);",
    "    appendSpotTitleWithLock(title, spot, { truncate: true });",
)

replace_exact(
    ROOT / "static" / "find_spots.js",
    "        appendSpotTitleWithLock(title, spot);",
    "        appendSpotTitleWithLock(title, spot, { truncate: true });",
)

home_css = ROOT / "static" / "home.css"
css_source = home_css.read_text(encoding="utf-8")
css_block = """

/* List rows keep longer Spot names to one line; detail pages remain uncut. */
.spot-list-title.is-truncated-title {
    display: flex;
    align-items: center;
    flex: 1 1 auto;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
}

.spot-list-title.is-truncated-title .spot-title-text {
    display: block;
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.spot-list-title.is-truncated-title .spot-title-lock-icon-wrap {
    flex: 0 0 auto;
}
"""
if ".spot-list-title.is-truncated-title" in css_source:
    raise RuntimeError("List-title truncation CSS already exists")
home_css.write_text(css_source.rstrip() + css_block + "\n", encoding="utf-8")

import_pattern = re.compile(r"spot_ui\\.js\\?v=[^'\"]+")
changed_imports = 0
for path in sorted((ROOT / "static").glob("*.js")):
    source = path.read_text(encoding="utf-8")
    revised, count = import_pattern.subn(
        "spot_ui.js?v=long-titles-v1-20260722",
        source,
    )
    if count:
        path.write_text(revised, encoding="utf-8")
        changed_imports += count
if changed_imports < 3:
    raise RuntimeError(f"Expected several spot_ui imports, changed only {changed_imports}")

(ROOT / "tests" / "test_presentation_spots.py").write_text(
    '''from __future__ import annotations

import unittest
from pathlib import Path

import constants as const
from spoof import PRESENTATION_SPOTS


class PresentationSpotDataTest(unittest.TestCase):
    def test_spot_title_limit_is_fifty_percent_larger(self):
        self.assertEqual(const.DISPLAY_NAME_MAX_CHARS, 18)
        self.assertEqual(const.SPOT_TITLE_MAX_CHARS, 27)

    def test_presentation_dataset_has_expected_variety(self):
        self.assertEqual(len(PRESENTATION_SPOTS), 25)
        self.assertGreaterEqual(
            sum(spot.city == "London" for spot in PRESENTATION_SPOTS),
            20,
        )
        self.assertEqual(
            len({spot.link for spot in PRESENTATION_SPOTS}),
            len(PRESENTATION_SPOTS),
        )
        self.assertTrue(any(len(spot.title) > 18 for spot in PRESENTATION_SPOTS))
        self.assertTrue(
            any(
                spot.starts_offset_seconds and spot.starts_offset_seconds > 0
                for spot in PRESENTATION_SPOTS
            )
        )
        self.assertTrue(
            any(
                spot.starts_offset_seconds and spot.starts_offset_seconds < 0
                for spot in PRESENTATION_SPOTS
            )
        )
        self.assertTrue(any(spot.claim_duration > 0 for spot in PRESENTATION_SPOTS))
        self.assertTrue(any(spot.use_password for spot in PRESENTATION_SPOTS))
        self.assertTrue(any(spot.is_prizedraw for spot in PRESENTATION_SPOTS))
        self.assertGreaterEqual(len({spot.radius for spot in PRESENTATION_SPOTS}), 10)

    def test_every_presentation_spot_obeys_current_value_rules(self):
        for spot in PRESENTATION_SPOTS:
            with self.subTest(spot=spot.title):
                self.assertLessEqual(len(spot.title), const.SPOT_TITLE_MAX_CHARS)
                self.assertGreaterEqual(spot.radius, const.MIN_SPOT_RADIUS_METRES)
                self.assertLessEqual(spot.radius, const.MAX_SPOT_RADIUS_METRES)
                self.assertGreaterEqual(
                    spot.active_for_seconds,
                    const.MIN_SPOT_ENDS_AFTER_SECONDS,
                )
                self.assertLessEqual(
                    spot.active_for_seconds,
                    const.MAX_SPOT_ENDS_AFTER_SECONDS,
                )
                if spot.is_prizedraw:
                    self.assertFalse(spot.use_password)
                    self.assertGreater(spot.max_total_claims, spot.prize_count)
                    self.assertGreaterEqual(
                        spot.total_value,
                        spot.prize_count * const.MIN_PRIZEDRAW_PRIZE_PAYOUT,
                    )
                else:
                    self.assertGreater(spot.max_total_claims, 0)
                    self.assertGreaterEqual(
                        spot.total_value,
                        spot.max_total_claims * const.MIN_STANDARD_CLAIM_PAYOUT,
                    )

    def test_list_titles_use_single_line_ellipsis_only_when_requested(self):
        source = (
            Path(__file__).resolve().parents[1] / "static" / "spot_ui.js"
        ).read_text(encoding="utf-8")
        css = (
            Path(__file__).resolve().parents[1] / "static" / "home.css"
        ).read_text(encoding="utf-8")
        find_spots = (
            Path(__file__).resolve().parents[1] / "static" / "find_spots.js"
        ).read_text(encoding="utf-8")

        self.assertIn("{ truncate = false } = {}", source)
        self.assertIn("{ truncate: true }", source)
        self.assertIn("{ truncate: true }", find_spots)
        self.assertIn("titleEl.title = fullTitle;", source)
        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("white-space: nowrap", css)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
