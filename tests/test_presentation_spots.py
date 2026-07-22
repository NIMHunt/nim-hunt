from __future__ import annotations

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
