from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_find_spots_debounces_zoom_refreshes_and_uses_one_leaflet_end_event():
    page = source("static/find_spots.js")

    assert "const MAP_REFRESH_DEBOUNCE_MS = 120;" in page
    assert "mapRefreshTimerId: null" in page
    assert "function scheduleMapRefresh()" in page
    assert "state.map.on('moveend', handleMapMoved);" in page
    assert "state.map.on('moveend zoomend', handleMapMoved);" not in page
    assert "clearScheduledMapRefresh();" in page


def test_find_spots_builds_a_replacement_marker_layer_before_swapping():
    page = source("static/find_spots.js")
    render = page.split("function renderMapSpots(spots) {", 1)[1].split(
        "async function fetchInitialSpots() {", 1
    )[0]

    assert "const nextSpotLayer = L.layerGroup();" in render
    assert "const nextMapLayersBySpotId = new Map();" in render
    assert "state.spotLayer.clearLayers();" not in render
    assert "nextSpotLayer.addTo(state.map);" in render
    assert "const previousSpotLayer = state.spotLayer;" in render
    assert "state.spotLayer = nextSpotLayer;" in render
    assert "previousSpotLayer?.remove();" in render
    assert render.index("nextSpotLayer.addTo(state.map);") < render.index(
        "previousSpotLayer?.remove();"
    )


def test_find_spots_keeps_wide_world_browsing_enabled():
    initial_view = source("static/find_spots_initial_view.js")
    assert "FIND_SPOTS_MIN_ZOOM = 0" in initial_view


def test_known_location_disables_double_click_recentering():
    page = source("static/find_spots.js")

    interaction = page.split("function setMapInteractionEnabled(enabled) {", 1)[1].split(
        "function clearScheduledMapRefresh()", 1
    )[0]
    setup = page.split("function setupMap() {", 1)[1].split(
        "async function initFindSpots()", 1
    )[0]

    assert "state.map.doubleClickZoom?.[method]?.();" in interaction
    assert "doubleClickZoom: !state.hasUserLocation," in setup
