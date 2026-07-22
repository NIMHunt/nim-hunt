from __future__ import annotations

import re
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_replace_once(path: str, pattern: str, replacement: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{path}: expected one regex match, found {count}: {pattern[:120]!r}")
    file.write_text(updated, encoding="utf-8")


replace_once(
    "public_html.py",
    '_ASSET_VERSION = "lock-prefix-unavailable-v1-20260722"',
    '_ASSET_VERSION = "map-list-interactions-v1-20260722"',
)
replace_once(
    "public_html.py",
    '        "max_draft_spots_per_user": int(getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3)),\n',
    '        "max_draft_spots_per_user": int(getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3)),\n'
    '        "max_spot_radius_metres": int(getattr(const, "MAX_SPOT_RADIUS_METRES", 1000)),\n',
)

replace_once(
    "templates/find_spots.html",
    '    data-max-map-zoom-out="{{ max_map_zoom_out }}"\n',
    '    data-max-map-zoom-out="{{ max_map_zoom_out }}"\n'
    '    data-max-spot-radius-metres="{{ max_spot_radius_metres | default(1000) }}"\n',
)
replace_once(
    "templates/find_spots.html",
    '<section class="spot-list-card" aria-label="Visible spots" data-i18n-aria-label="findSpots.listAria">',
    '<section class="spot-list-card" aria-label="Spots" data-i18n-aria-label="findSpots.listAria">',
)
replace_once(
    "templates/find_spots.html",
    '<h2 id="visible-spots-title" data-i18n="findSpots.visibleSpots">Visible Spots</h2>',
    '<h2 id="visible-spots-title" data-i18n="findSpots.visibleSpots">Spots</h2>',
)

replace_once(
    "static/interface_text.js",
    "            listTitle: 'Visible Spots',\n            listTitleWithCount: (n) => `Visible Spots (${n})`,",
    "            listTitle: 'Spots',\n            listTitleWithCount: (n) => `${n} ${n === 1 ? 'Spot' : 'Spots'}` ,",
)

replace_once(
    "static/find_spots.js",
    "import { getReportReasonOptions, makeFindSpotsText, makeSpotDetailText } from './interface_text.js?v=transaction-integrity-v1-20260721';",
    "import { getReportReasonOptions, makeFindSpotsText, makeSpotDetailText } from './interface_text.js?v=map-list-interactions-v1-20260722';",
)
replace_once(
    "static/find_spots.js",
    "    createOwnerClaimCodesControl,\n    durationText,",
    "    createOwnerClaimCodesControl,\n    createNimiqInlineIcon,\n    durationText,",
)
replace_once(
    "static/find_spots.js",
    "    expandedSpotIds: new Set(),\n    expandedClaimCodeSpotIds: new Set(),",
    "    expandedSpotIds: new Set(),\n    listEntriesBySpotId: new Map(),\n    expandedClaimCodeSpotIds: new Set(),",
)
replace_once(
    "static/find_spots.js",
    "const MAX_MAP_ZOOM_OUT = Number.parseInt(document.body.dataset.maxMapZoomOut || '11', 10);\n",
    "const MAX_MAP_ZOOM_OUT = Number.parseInt(document.body.dataset.maxMapZoomOut || '11', 10);\n"
    "const MAX_SPOT_RADIUS_METRES = Number.parseFloat(document.body.dataset.maxSpotRadiusMetres || '1000');\n"
    "const MAP_LIST_SCROLL_DURATION_MS = 420;\n",
)

replace_once(
    "static/find_spots.js",
    "function spotMatchesFilters(spot, filters = getFilterParams()) {\n"
    "    if (spot.is_prizedraw && !filters.includePrizedraws) return false;\n"
    "    if (spot.status_label === 'upcoming') return filters.includeUpcoming;\n"
    "    return filters.includeActive;\n"
    "}\n",
    "function spotMatchesFilters(spot, filters = getFilterParams()) {\n"
    "    if (spot.is_prizedraw && !filters.includePrizedraws) return false;\n"
    "    if (spot.status_label === 'upcoming') return filters.includeUpcoming;\n"
    "    return filters.includeActive;\n"
    "}\n\n"
    "function spotCentreWithinBounds(spot, bounds) {\n"
    "    const lat = Number(spot?.lat);\n"
    "    const long = Number(spot?.long);\n"
    "    if (!Number.isFinite(lat) || !Number.isFinite(long) || !bounds?.contains) return false;\n"
    "    return Boolean(bounds.contains([lat, long]));\n"
    "}\n\n"
    "function expandedMapSearchBounds(bounds, radiusMetres = MAX_SPOT_RADIUS_METRES) {\n"
    "    const south = Number(bounds.getSouth());\n"
    "    const north = Number(bounds.getNorth());\n"
    "    const west = Number(bounds.getWest());\n"
    "    const east = Number(bounds.getEast());\n"
    "    const radius = Math.max(0, Number(radiusMetres) || 0);\n"
    "    const centreLatitude = (south + north) / 2;\n"
    "    const latitudePadding = radius / 111320;\n"
    "    const longitudeScale = Math.max(0.2, Math.cos(centreLatitude * Math.PI / 180));\n"
    "    const longitudePadding = radius / (111320 * longitudeScale);\n\n"
    "    return {\n"
    "        south: Math.max(-90, south - latitudePadding),\n"
    "        north: Math.min(90, north + latitudePadding),\n"
    "        west: west - longitudePadding,\n"
    "        east: east + longitudePadding,\n"
    "    };\n"
    "}\n",
)

replace_once(
    "static/find_spots.js",
    "function setListItemExpanded(item, summary, detail, spotId, expanded) {\n"
    "    item.classList.toggle('is-expanded', expanded);\n"
    "    summary.setAttribute('aria-expanded', expanded ? 'true' : 'false');\n"
    "    detail.hidden = !expanded;\n\n"
    "    if (expanded) {\n"
    "        state.expandedSpotIds.add(spotId);\n"
    "    } else {\n"
    "        state.expandedSpotIds.delete(spotId);\n"
    "    }\n"
    "}\n",
    "function setListItemExpanded(item, summary, detail, spotId, expanded) {\n"
    "    item.classList.toggle('is-expanded', expanded);\n"
    "    summary.setAttribute('aria-expanded', expanded ? 'true' : 'false');\n"
    "    detail.hidden = !expanded;\n\n"
    "    if (expanded) {\n"
    "        state.expandedSpotIds.add(spotId);\n"
    "    } else {\n"
    "        state.expandedSpotIds.delete(spotId);\n"
    "    }\n"
    "}\n\n"
    "function fastSmoothScrollToElement(element, durationMs = MAP_LIST_SCROLL_DURATION_MS) {\n"
    "    if (!element) return false;\n"
    "    const startY = window.scrollY;\n"
    "    const targetY = Math.max(0, startY + element.getBoundingClientRect().top - 12);\n"
    "    const distance = targetY - startY;\n"
    "    if (Math.abs(distance) < 2) return true;\n\n"
    "    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {\n"
    "        window.scrollTo(0, targetY);\n"
    "        return true;\n"
    "    }\n\n"
    "    const startedAt = performance.now();\n"
    "    const duration = Math.min(1800, Math.max(120, Number(durationMs) || MAP_LIST_SCROLL_DURATION_MS));\n"
    "    const step = (now) => {\n"
    "        const progress = Math.min(1, (now - startedAt) / duration);\n"
    "        const eased = 1 - ((1 - progress) ** 3);\n"
    "        window.scrollTo(0, startY + distance * eased);\n"
    "        if (progress < 1) window.requestAnimationFrame(step);\n"
    "    };\n"
    "    window.requestAnimationFrame(step);\n"
    "    return true;\n"
    "}\n\n"
    "function focusSpotInList(spotId) {\n"
    "    const entry = state.listEntriesBySpotId.get(Number(spotId));\n"
    "    if (!entry) return false;\n"
    "    setListItemExpanded(entry.item, entry.summary, entry.detail, Number(spotId), true);\n"
    "    window.requestAnimationFrame(() => fastSmoothScrollToElement(entry.item));\n"
    "    return true;\n"
    "}\n",
)

replace_once(
    "static/find_spots.js",
    "    els.list.replaceChildren();\n    els.list.hidden = !hasSpots;",
    "    els.list.replaceChildren();\n    state.listEntriesBySpotId = new Map();\n    els.list.hidden = !hasSpots;",
)
replace_once(
    "static/find_spots.js",
    "        const item = document.createElement('li');\n        item.className = 'spot-list-item';",
    "        const item = document.createElement('li');\n        item.className = 'spot-list-item';\n        item.dataset.spotId = String(spotId);",
)
replace_once(
    "static/find_spots.js",
    "        const initiallyExpanded = state.expandedSpotIds.has(spotId);\n        setListItemExpanded(item, summary, detail, spotId, initiallyExpanded);",
    "        const initiallyExpanded = state.expandedSpotIds.has(spotId);\n        setListItemExpanded(item, summary, detail, spotId, initiallyExpanded);\n        state.listEntriesBySpotId.set(spotId, { item, summary, detail });",
)

new_map_block = r'''function createMapSpotTooltipContent(spot) {
    const content = document.createElement('span');
    content.className = 'map-spot-title-tooltip-content';

    if (spot.use_password) {
        const lock = document.createElement('span');
        lock.className = 'map-spot-title-tooltip-lock';
        lock.setAttribute('aria-hidden', 'true');
        lock.append(createNimiqInlineIcon('nq-lock-locked'));
        content.append(lock);
    }

    const title = document.createElement('span');
    title.className = 'map-spot-title-tooltip-text';
    title.textContent = String(spot.title || 'NimHunt Spot');
    content.append(title);
    return content;
}

function renderMapSpots(spots) {
    const filters = getFilterParams();
    const radiusCircles = [];
    const dots = [];

    state.spotLayer.clearLayers();

    for (const spot of spots) {
        const matchesFilters = spotMatchesFilters(spot, filters);
        const colour = matchesFilters ? markerColour(spot) : MAP_COLOURS.muted;
        const latLng = [Number(spot.lat), Number(spot.long)];
        let showTooltip = null;
        let hideTooltip = null;

        if (matchesFilters) {
            const radiusCircle = L.circle(latLng, {
                radius: spot.radius,
                color: colour,
                opacity: 0.95,
                fillColor: colour,
                fillOpacity: 0.22,
                weight: 2.5,
                interactive: true,
                bubblingMouseEvents: false,
                className: 'spot-radius-circle',
            });

            const tooltip = L.tooltip({
                className: 'map-spot-title-tooltip',
                direction: 'top',
                offset: [0, -16],
                opacity: 1,
                interactive: false,
            })
                .setLatLng(latLng)
                .setContent(createMapSpotTooltipContent(spot));

            showTooltip = () => {
                if (!spotCentreWithinBounds(spot, state.map.getBounds())) return;
                if (!state.spotLayer.hasLayer(tooltip)) state.spotLayer.addLayer(tooltip);
            };
            hideTooltip = () => {
                if (state.spotLayer.hasLayer(tooltip)) state.spotLayer.removeLayer(tooltip);
            };
            radiusCircle.on('mouseover', showTooltip);
            radiusCircle.on('mouseout', hideTooltip);
            radiusCircles.push(radiusCircle);
        }

        const dot = L.circleMarker(latLng, {
            radius: 12,
            color: '#ffffff',
            fillColor: colour,
            fillOpacity: matchesFilters ? 1 : 0.68,
            weight: 2,
            interactive: matchesFilters,
            bubblingMouseEvents: false,
            className: `spot-centre-marker ${matchesFilters ? 'is-interactive' : 'is-muted'}`,
        });

        if (matchesFilters) {
            dot.on('click', () => focusSpotInList(spot.id));
            dot.on('mouseover', showTooltip);
            dot.on('mouseout', hideTooltip);
        }
        dots.push(dot);
    }

    // Draw all radii first and all dots second. That keeps the translucent
    // radius overlays visible without letting them wash over the spot dots.
    for (const radiusCircle of radiusCircles) {
        radiusCircle.addTo(state.spotLayer);
    }
    for (const dot of dots) {
        dot.addTo(state.spotLayer);
    }
}

async function fetchInitialSpots'''
regex_replace_once(
    "static/find_spots.js",
    r"function renderMapSpots\(spots\) \{.*?\n\}\n\nasync function fetchInitialSpots",
    new_map_block,
)

replace_once(
    "static/find_spots.js",
    "    const bounds = state.map.getBounds();\n    const origin = getDistanceOrigin();\n"
    "    params.set('min_lat', String(bounds.getSouth()));\n"
    "    params.set('max_lat', String(bounds.getNorth()));\n"
    "    params.set('min_long', String(bounds.getWest()));\n"
    "    params.set('max_long', String(bounds.getEast()));",
    "    const visibleBounds = state.map.getBounds();\n"
    "    const searchBounds = expandedMapSearchBounds(visibleBounds);\n"
    "    const origin = getDistanceOrigin();\n"
    "    params.set('min_lat', String(searchBounds.south));\n"
    "    params.set('max_lat', String(searchBounds.north));\n"
    "    params.set('min_long', String(searchBounds.west));\n"
    "    params.set('max_long', String(searchBounds.east));",
)
replace_once(
    "static/find_spots.js",
    "        const listSpots = spots.filter((spot) => spotMatchesFilters(spot));",
    "        const listSpots = spots.filter((spot) => (\n"
    "            spotMatchesFilters(spot) && spotCentreWithinBounds(spot, visibleBounds)\n"
    "        ));",
)

replace_once(
    "static/home.css",
    ".spot-map .leaflet-control-attribution {\n"
    "    font-family: Muli, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;\n"
    "    font-size: 0.72rem;\n"
    "}\n",
    ".spot-map .leaflet-control-attribution {\n"
    "    font-family: Muli, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;\n"
    "    font-size: 0.72rem;\n"
    "}\n\n"
    ".spot-radius-circle.leaflet-interactive {\n"
    "    cursor: default;\n"
    "}\n\n"
    ".spot-centre-marker.is-interactive.leaflet-interactive {\n"
    "    cursor: pointer;\n"
    "}\n\n"
    ".leaflet-tooltip.map-spot-title-tooltip {\n"
    "    padding: 8px 10px;\n"
    "    border: 1px solid var(--nh-border);\n"
    "    border-radius: 14px;\n"
    "    background: rgba(255, 255, 255, 0.96);\n"
    "    color: var(--nh-muted);\n"
    "    box-shadow: 0 8px 22px rgba(31, 35, 72, 0.10);\n"
    "    font-family: Muli, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;\n"
    "    font-size: 1rem;\n"
    "    font-weight: 900;\n"
    "    line-height: 1.15;\n"
    "    white-space: nowrap;\n"
    "}\n\n"
    ".leaflet-tooltip-top.map-spot-title-tooltip::before {\n"
    "    border-top-color: rgba(255, 255, 255, 0.96);\n"
    "}\n\n"
    ".map-spot-title-tooltip-content {\n"
    "    display: inline-flex;\n"
    "    align-items: center;\n"
    "    min-width: 0;\n"
    "}\n\n"
    ".map-spot-title-tooltip-lock {\n"
    "    display: inline-flex;\n"
    "    align-items: center;\n"
    "    justify-content: center;\n"
    "    width: 1em;\n"
    "    height: 1em;\n"
    "    margin-right: 0.25em;\n"
    "    color: currentColor;\n"
    "}\n\n"
    ".map-spot-title-tooltip-lock .nq-icon {\n"
    "    display: block;\n"
    "    width: 1em;\n"
    "    height: 1em;\n"
    "}\n",
)

Path("tests/test_find_spots_map_interactions.py").write_text(
    '''from __future__ import annotations

import re
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

    def test_count_heading_is_plain_spot_count(self):
        self.assertIn("listTitle: 'Spots'", self.text)
        self.assertIn("`${n} ${n === 1 ? 'Spot' : 'Spots'}`", self.text)
        self.assertNotIn("Visible Spots (${n})", self.text)
        self.assertIn('>Spots</h2>', self.template)

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
        self.assertIn("font-size: 1rem;", self.css)

    def test_centre_diameter_is_doubled(self):
        self.assertRegex(self.source, r"L\.circleMarker\(latLng, \{\s*radius: 12,")

    def test_search_bounds_include_overlapping_offscreen_radii(self):
        self.assertIn("expandedMapSearchBounds(visibleBounds)", self.source)
        self.assertIn("MAX_SPOT_RADIUS_METRES", self.source)
        self.assertIn("spotCentreWithinBounds(spot, visibleBounds)", self.source)
        self.assertIn('data-max-spot-radius-metres=', self.template)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
