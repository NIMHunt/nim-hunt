from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


# Shared reusable map: expose layer highlighting, centre-hover callbacks and
# direct centre-click callbacks while retaining old popup behaviour by default.
spot_map_path = "static/spot_map.js"
spot_map = read(spot_map_path)
start = spot_map.index("export function createReusableSpotMap")
spot_map_tail = r'''function spotLayerKey(spotOrId) {
    const value = typeof spotOrId === 'object' ? spotOrId?.id : spotOrId;
    return String(value ?? '');
}

function setLayerEntryHighlighted(entry, highlighted, highlightColour) {
    if (!entry) return false;
    const colour = highlighted ? highlightColour : entry.colour;
    entry.circle?.setStyle({
        color: colour,
        fillColor: colour,
    });
    entry.marker?.setStyle({
        color: highlighted ? colour : '#ffffff',
        fillColor: colour,
    });
    if (highlighted) entry.marker?.bringToFront?.();
    return true;
}

export function createReusableSpotMap({
    mapEl,
    tileUrl,
    tileAttribution,
    spots = [],
    colourForSpot,
    popupBuilder = null,
    onSpotClick = null,
    onSpotHover = null,
    onSpotCentreClick = null,
    radiusInteractive = true,
    highlightColour = '#1f2348',
}) {
    if (!mapEl || !window.L) return null;

    const map = window.L.map(mapEl, {
        zoomControl: true,
        attributionControl: true,
    });

    window.L.tileLayer(tileUrl, {
        attribution: tileAttribution,
        maxZoom: 19,
    }).addTo(map);

    const spotLayer = window.L.layerGroup().addTo(map);
    const layerEntries = new Map();

    const api = {
        map,
        spotLayer,
        setSpots(nextSpots = []) {
            renderSpotsOnMap({
                map,
                spotLayer,
                layerEntries,
                spots: nextSpots,
                colourForSpot,
                popupBuilder,
                onSpotClick,
                onSpotHover,
                onSpotCentreClick,
                radiusInteractive,
                highlightColour,
            });
        },
        setSpotHighlighted(spotId, highlighted) {
            return setLayerEntryHighlighted(
                layerEntries.get(spotLayerKey(spotId)),
                Boolean(highlighted),
                highlightColour,
            );
        },
    };

    api.setSpots(spots);
    return api;
}

export function renderSpotsOnMap({
    map,
    spotLayer,
    layerEntries = new Map(),
    spots = [],
    colourForSpot,
    popupBuilder = null,
    onSpotClick = null,
    onSpotHover = null,
    onSpotCentreClick = null,
    radiusInteractive = true,
    highlightColour = '#1f2348',
}) {
    if (!map || !spotLayer || !window.L) return;

    clearLayer(spotLayer);
    layerEntries.clear();
    const visibleSpots = spots.filter(validSpotCoordinate);

    if (visibleSpots.length === 0) {
        map.setView(DEFAULT_MAP_CENTRE, DEFAULT_MAP_ZOOM);
        return;
    }

    const bounds = [];

    for (const spot of visibleSpots) {
        const latLng = spotLatLng(spot);
        const colour = typeof colourForSpot === 'function' ? colourForSpot(spot) : '#21bca5';
        bounds.push(latLng);

        const radius = Math.max(1, Number(spot.radius || 25));
        const circle = window.L.circle(latLng, {
            radius,
            color: colour,
            fillColor: colour,
            fillOpacity: 0.14,
            opacity: 0.82,
            weight: 2,
            interactive: Boolean(radiusInteractive),
            bubblingMouseEvents: false,
        });

        const marker = window.L.circleMarker(latLng, {
            radius: 7,
            color: '#ffffff',
            fillColor: colour,
            fillOpacity: 1,
            opacity: 1,
            weight: 2,
            bubblingMouseEvents: false,
        });

        if (typeof onSpotCentreClick === 'function') {
            marker.on('click', () => onSpotCentreClick(spot));
        } else {
            bindSpotPopup({ map, layer: marker, spot, popupBuilder, onSpotClick });
        }

        if (Boolean(radiusInteractive)) {
            bindSpotPopup({ map, layer: circle, spot, popupBuilder, onSpotClick });
        }

        if (typeof onSpotHover === 'function') {
            marker.on('mouseover', () => onSpotHover(spot, true));
            marker.on('mouseout', () => onSpotHover(spot, false));
        }

        layerEntries.set(spotLayerKey(spot), {
            spot,
            circle,
            marker,
            colour,
            highlightColour,
        });

        circle.addTo(spotLayer);
        marker.addTo(spotLayer);
    }

    if (bounds.length === 1) {
        map.setView(bounds[0], 13);
        return;
    }

    map.fitBounds(window.L.latLngBounds(bounds), {
        padding: [28, 28],
        maxZoom: 13,
    });
}
'''
write(spot_map_path, spot_map[:start] + spot_map_tail)

# Find Spots: keep direct references to map layers and synchronise hover both ways.
replace_once(
    "static/find_spots.js",
    "    listEntriesBySpotId: new Map(),\n",
    "    listEntriesBySpotId: new Map(),\n    mapLayersBySpotId: new Map(),\n",
)
replace_once(
    "static/find_spots.js",
    "    muted: '#8c90a8',\n};",
    "    muted: '#8c90a8',\n    highlight: '#1f2348',\n};",
)
replace_once(
    "static/find_spots.js",
    "function renderList(spots) {",
    r'''function setSpotListMapHighlighted(spotId, highlighted) {
    const entry = state.listEntriesBySpotId.get(Number(spotId));
    entry?.item.classList.toggle('is-map-highlighted', Boolean(highlighted));
}

function setSpotMapHighlighted(spotId, highlighted) {
    const entry = state.mapLayersBySpotId.get(Number(spotId));
    if (!entry) return false;
    const colour = highlighted ? MAP_COLOURS.highlight : entry.colour;
    entry.radiusCircle?.setStyle({ color: colour, fillColor: colour });
    entry.dot?.setStyle({
        color: highlighted ? colour : '#ffffff',
        fillColor: colour,
    });
    if (highlighted) entry.dot?.bringToFront?.();
    return true;
}

function renderList(spots) {''',
)
replace_once(
    "static/find_spots.js",
    "        state.listEntriesBySpotId.set(spotId, { item, summary, detail });\n\n        const toggleExpanded = () => {",
    "        state.listEntriesBySpotId.set(spotId, { item, summary, detail });\n        item.addEventListener('mouseenter', () => setSpotMapHighlighted(spotId, true));\n        item.addEventListener('mouseleave', () => setSpotMapHighlighted(spotId, false));\n\n        const toggleExpanded = () => {",
)
replace_once(
    "static/find_spots.js",
    "    state.spotLayer.clearLayers();\n\n    for (const spot of spots) {",
    "    state.spotLayer.clearLayers();\n    state.mapLayersBySpotId.clear();\n    for (const entry of state.listEntriesBySpotId.values()) {\n        entry.item.classList.remove('is-map-highlighted');\n    }\n\n    for (const spot of spots) {",
)
replace_once(
    "static/find_spots.js",
    "        let showTooltip = null;\n        let hideTooltip = null;\n\n        if (matchesFilters) {\n            const radiusCircle = L.circle(latLng, {",
    "        let showTooltip = null;\n        let hideTooltip = null;\n        let radiusCircle = null;\n\n        if (matchesFilters) {\n            radiusCircle = L.circle(latLng, {",
)
replace_once(
    "static/find_spots.js",
    "        if (matchesFilters) {\n            dot.on('click', () => focusSpotInList(spot.id));\n            dot.on('mouseover', showTooltip);\n            dot.on('mouseout', hideTooltip);\n        }\n        dots.push(dot);",
    r'''        state.mapLayersBySpotId.set(Number(spot.id), {
            radiusCircle,
            dot,
            colour,
            matchesFilters,
        });

        if (matchesFilters) {
            dot.on('click', () => focusSpotInList(spot.id));
            dot.on('mouseover', () => {
                showTooltip?.();
                setSpotListMapHighlighted(spot.id, true);
            });
            dot.on('mouseout', () => {
                hideTooltip?.();
                setSpotListMapHighlighted(spot.id, false);
            });
        }
        dots.push(dot);''',
)

# My Spots: list hover highlights the map layers; centre hover outlines the row.
replace_once(
    "static/my_spots.js",
    "import { createReusableSpotMap } from './spot_map.js';",
    "import { createReusableSpotMap } from './spot_map.js?v=map-list-hover-sync-v1-20260722';",
)
replace_once(
    "static/my_spots.js",
    "function buildMySpotListItem(spot) {",
    r'''function setMySpotListHighlighted(spotId, highlighted) {
    const item = els.sections.querySelector(`[data-spot-id="${Number(spotId)}"]`);
    item?.classList.toggle('is-map-highlighted', Boolean(highlighted));
}

function setMySpotMapHighlighted(spotId, highlighted) {
    return state.spotMap?.setSpotHighlighted(Number(spotId), Boolean(highlighted)) || false;
}

function buildMySpotListItem(spot) {''',
)
replace_once(
    "static/my_spots.js",
    "    item.dataset.spotId = String(Number(spot.id));\n    item.dataset.renderSignature = mySpotRenderSignature(spot);\n    return item;",
    "    item.dataset.spotId = String(Number(spot.id));\n    item.dataset.renderSignature = mySpotRenderSignature(spot);\n    item.addEventListener('mouseenter', () => setMySpotMapHighlighted(spot.id, true));\n    item.addEventListener('mouseleave', () => setMySpotMapHighlighted(spot.id, false));\n    return item;",
)
replace_once(
    "static/my_spots.js",
    "                popupBuilder: spotPopupContent,\n                onSpotClick: openSpotPage,\n",
    "                popupBuilder: spotPopupContent,\n                onSpotClick: openSpotPage,\n                onSpotHover: (spot, highlighted) => setMySpotListHighlighted(spot.id, highlighted),\n",
)

# My Claims: two-way hover plus direct centre click to expand and quickly scroll.
replace_once(
    "static/my_claims.js",
    "import { createReusableSpotMap } from './spot_map.js';",
    "import { createReusableSpotMap } from './spot_map.js?v=map-list-hover-sync-v1-20260722';",
)
replace_once(
    "static/my_claims.js",
    "const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';\n",
    "const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';\nconst MAP_LIST_SCROLL_DURATION_MS = 420;\n",
)
replace_once(
    "static/my_claims.js",
    "function buildClaimListItem(claim) {",
    r'''function setClaimListMapHighlighted(claimId, highlighted) {
    const item = els.list.querySelector(`[data-claim-id="${Number(claimId)}"]`);
    item?.classList.toggle('is-map-highlighted', Boolean(highlighted));
}

function setClaimMapHighlighted(claimId, highlighted) {
    return state.claimMap?.setSpotHighlighted(Number(claimId), Boolean(highlighted)) || false;
}

function fastSmoothScrollToClaim(element, durationMs = MAP_LIST_SCROLL_DURATION_MS) {
    if (!element) return false;
    const startY = window.scrollY;
    const targetY = Math.max(0, startY + element.getBoundingClientRect().top - 12);
    const distance = targetY - startY;
    if (Math.abs(distance) < 2) return true;

    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        window.scrollTo(0, targetY);
        return true;
    }

    const startedAt = performance.now();
    const duration = Math.min(1800, Math.max(120, Number(durationMs) || MAP_LIST_SCROLL_DURATION_MS));
    const step = (now) => {
        const progress = Math.min(1, (now - startedAt) / duration);
        const eased = 1 - ((1 - progress) ** 3);
        window.scrollTo(0, startY + distance * eased);
        if (progress < 1) window.requestAnimationFrame(step);
    };
    window.requestAnimationFrame(step);
    return true;
}

function focusClaimInList(claimId) {
    const item = els.list.querySelector(`[data-claim-id="${Number(claimId)}"]`);
    if (!item) return false;
    const summary = item.querySelector('.spot-list-toggle');
    const detail = item.querySelector('.claim-list-detail');
    if (!summary || !detail) return false;
    setClaimExpanded(item, summary, detail, Number(claimId), true);
    window.requestAnimationFrame(() => fastSmoothScrollToClaim(item));
    return true;
}

function buildClaimListItem(claim) {''',
)
replace_once(
    "static/my_claims.js",
    "    item.className = 'spot-list-item my-claim-list-item';\n    item.dataset.claimId = String(claimId);\n",
    "    item.className = 'spot-list-item my-claim-list-item';\n    item.dataset.claimId = String(claimId);\n    item.addEventListener('mouseenter', () => setClaimMapHighlighted(claimId, true));\n    item.addEventListener('mouseleave', () => setClaimMapHighlighted(claimId, false));\n",
)
replace_once(
    "static/my_claims.js",
    "                colourForSpot: (item) => claimMapColour(item.claim || {}),\n                popupBuilder: claimPopupContent,\n                onSpotClick: (item) => {\n                    window.location.href = item.href;\n                },\n",
    "                colourForSpot: (item) => claimMapColour(item.claim || {}),\n                onSpotCentreClick: (item) => focusClaimInList(item.id),\n                onSpotHover: (item, highlighted) => setClaimListMapHighlighted(item.id, highlighted),\n                radiusInteractive: false,\n",
)

# Intentional shared visual state and stable description spacing.
replace_once(
    "static/home.css",
    "    overflow: hidden;\n}\n\n.spot-list-link {",
    "    overflow: hidden;\n}\n\n.spot-list-item.is-map-highlighted {\n    outline: 2px solid var(--nimiq-blue, #1f2348);\n    outline-offset: 1px;\n}\n\n.spot-list-link {",
)
replace_once(
    "static/home.css",
    ".spot-detail-description {\n    margin: 0 0 0.75em;",
    "/* Intentionally compact on every Spot and Claim detail card. */\n.spot-detail-description {\n    margin: -0.35em 0 0.75em;",
)
replace_once(
    "static/home.css",
    "\n.find-shell .spot-list-item.is-expanded .spot-list-detail .spot-detail-description {\n    margin-top: -0.35em;\n}\n",
    "\n",
)

# Cache bust all affected page assets.
replace_once(
    "public_html.py",
    '_ASSET_VERSION = "single-open-details-v1-20260722"',
    '_ASSET_VERSION = "map-list-hover-sync-v1-20260722"',
)

# Focused regression coverage.
test_content = r'''from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_find_spots_hover_is_synchronised_both_ways():
    js = source("static/find_spots.js")
    assert "mapLayersBySpotId: new Map()" in js
    assert "item.addEventListener('mouseenter', () => setSpotMapHighlighted(spotId, true))" in js
    assert "item.addEventListener('mouseleave', () => setSpotMapHighlighted(spotId, false))" in js
    assert "setSpotListMapHighlighted(spot.id, true)" in js
    assert "setSpotListMapHighlighted(spot.id, false)" in js
    assert "MAP_COLOURS.highlight" in js


def test_shared_map_supports_layer_highlighting_and_centre_callbacks():
    js = source("static/spot_map.js")
    assert "setSpotHighlighted(spotId, highlighted)" in js
    assert "onSpotHover = null" in js
    assert "onSpotCentreClick = null" in js
    assert "radiusInteractive = true" in js
    assert "marker.on('mouseover', () => onSpotHover(spot, true))" in js
    assert "marker.on('click', () => onSpotCentreClick(spot))" in js
    assert "entry.circle?.setStyle" in js
    assert "entry.marker?.setStyle" in js


def test_my_spots_hover_links_map_and_rows_without_changing_click_navigation():
    js = source("static/my_spots.js")
    assert "setMySpotMapHighlighted(spot.id, true)" in js
    assert "setMySpotMapHighlighted(spot.id, false)" in js
    assert "onSpotHover: (spot, highlighted) => setMySpotListHighlighted(spot.id, highlighted)" in js
    assert "onSpotClick: openSpotPage" in js


def test_my_claims_centres_expand_scroll_and_hover_link_the_claim_row():
    js = source("static/my_claims.js")
    assert "onSpotCentreClick: (item) => focusClaimInList(item.id)" in js
    assert "radiusInteractive: false" in js
    assert "setClaimExpanded(item, summary, detail, Number(claimId), true)" in js
    assert "MAP_LIST_SCROLL_DURATION_MS = 420" in js
    assert "setClaimMapHighlighted(claimId, true)" in js
    assert "setClaimListMapHighlighted(item.id, highlighted)" in js
    assert "window.location.href = item.href" not in js


def test_blue_outline_and_description_spacing_are_intentional_shared_rules():
    css = source("static/home.css")
    assert ".spot-list-item.is-map-highlighted" in css
    assert "outline: 2px solid var(--nimiq-blue, #1f2348);" in css
    assert "/* Intentionally compact on every Spot and Claim detail card. */" in css
    assert "margin: -0.35em 0 0.75em;" in css
    assert ".find-shell .spot-list-item.is-expanded .spot-list-detail .spot-detail-description" not in css


def test_asset_version_is_bumped():
    public_html = source("public_html.py")
    assert '_ASSET_VERSION = "map-list-hover-sync-v1-20260722"' in public_html
'''
write("tests/test_map_list_hover_sync.py", test_content)
