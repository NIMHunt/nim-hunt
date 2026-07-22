from __future__ import annotations

import unittest
from pathlib import Path


class FindSpotsMapInteractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "static" / "find_spots.js").read_text(encoding="utf-8")
        cls.css = (cls.root / "static" / "home.css").read_text(encoding="utf-8")
        cls.text = (cls.root / "static" / "interface_text.js").read_text(encoding="utf-8")
        cls.template = (cls.root / "templates" / "find_spots.html").read_text(encoding="utf-8")

    def test_count_heading_names_the_users_area(self):
        self.assertIn("listTitle: 'Spots in Your Area'", self.text)
        self.assertIn("`${n} ${n === 1 ? 'Spot' : 'Spots'} in Your Area`", self.text)
        self.assertNotIn("Visible Spots (${n})", self.text)
        self.assertIn('>Spots in Your Area</h2>', self.template)

    def test_only_filtered_in_centres_are_clickable(self):
        map_start = self.source.index("function renderMapSpots(spots)")
        map_end = self.source.index("async function fetchInitialSpots", map_start)
        block = self.source[map_start:map_end]
        self.assertNotIn("window.location.href = spot.href", block)
        self.assertNotIn("radiusCircle.on('click'", block)
        self.assertIn("interactive: matchesFilters", block)
        self.assertIn("dot.on('click', () => focusSpotInList(spot.id))", block)

    def test_map_click_expands_and_fast_scrolls_to_list_card(self):
        self.assertIn("state.listEntriesBySpotId.set(spotId, { item, summary, detail });", self.source)
        self.assertIn("setListItemExpanded(entry.item, entry.summary, entry.detail", self.source)
        self.assertIn("MAP_LIST_SCROLL_DURATION_MS = 420", self.source)
        self.assertRegex(self.source, r"Math\.min\(1800, Math\.max\(120,")

    def test_radius_hover_uses_standard_light_tooltip_with_lock(self):
        self.assertIn("radiusCircle.on('mouseover', showTooltip)", self.source)
        self.assertIn("spotCentreWithinBounds(spot, state.map.getBounds())", self.source)
        self.assertIn("createNimiqInlineIcon('nq-lock-locked')", self.source)
        self.assertIn(".leaflet-tooltip.map-spot-title-tooltip", self.css)
        self.assertIn("background: rgba(255, 255, 255, 0.96);", self.css)
        self.assertIn("color: var(--nh-muted);", self.css)
        self.assertIn("font-size: 1.6rem;", self.css)
        self.assertIn("font-weight: 750;", self.css)
        self.assertIn("line-height: 1.25;", self.css)

    def test_expanded_find_spot_descriptions_sit_closer_to_meta(self):
        self.assertIn(
            '.find-shell .spot-list-item.is-expanded .spot-list-detail .spot-detail-description',
            self.css,
        )
        self.assertIn('margin-top: -0.35em;', self.css)

    def test_centre_markers_use_subtle_button_like_depth(self):
        self.assertIn('.spot-centre-marker {', self.css)
        self.assertIn('filter: drop-shadow(0 3px 4px rgba(31, 35, 72, 0.18));', self.css)

    def test_centre_diameter_is_doubled(self):
        self.assertRegex(self.source, r"L\.circleMarker\(latLng, \{\s*radius: 12,")

    def test_search_bounds_include_overlapping_offscreen_radii(self):
        self.assertIn("expandedMapSearchBounds(visibleBounds)", self.source)
        self.assertIn("MAX_SPOT_RADIUS_METRES", self.source)
        self.assertIn("spotCentreWithinBounds(spot, visibleBounds)", self.source)
        self.assertIn('data-max-spot-radius-metres=', self.template)


if __name__ == "__main__":
    unittest.main()
