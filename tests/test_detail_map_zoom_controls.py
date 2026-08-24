from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_spot_detail_map_only_allows_explicit_zoom_controls():
    page = source("static/spot_detail.js")
    assert "zoomControl: false" in page
    assert "window.L.control.zoom({ position: 'topright' }).addTo(map);" in page
    assert "dragging: false" in page
    assert "touchZoom: false" in page
    assert "scrollWheelZoom: false" in page
    assert "doubleClickZoom: false" in page
    assert "boxZoom: false" in page
    assert "keyboard: false" in page
    assert "map.on('moveend zoomend', keepCentred);" not in page


def test_detail_maps_share_a_global_one_kilometre_zoom_floor():
    spot = source("static/spot_detail.js")
    claim = source("static/claim_detail.js")

    for page in (spot, claim):
        assert "const DETAIL_MAP_MIN_ZOOM = 12;" in page
        assert "minZoom: DETAIL_MAP_MIN_ZOOM," in page
        assert "map.getBoundsZoom(" not in page
        assert "map.setMinZoom(" not in page


def test_claim_detail_keeps_gestures_locked_and_spot_centred():
    page = source("static/claim_detail.js")
    assert "zoomControl: false" in page
    assert "window.L.control.zoom({ position: 'topright' }).addTo(map);" in page
    assert "dragging: false" in page
    assert "touchZoom: false" in page
    assert "scrollWheelZoom: false" in page
    assert "doubleClickZoom: false" in page
    assert "bounds.extend(userLatLng);" not in page
    assert "map.fitBounds(radiusBounds, { animate: false, maxZoom: 16 });" in page
    assert "map.setView(centre, DETAIL_MAP_MIN_ZOOM, { animate: false });" in page


def test_detail_map_cache_keys_are_bumped():
    spot = source("templates/spot.html")
    claim = source("templates/claim.html")
    assert "/static/spot_detail.js?v=locked-global-zoom-right-v3-20260814-" in spot
    assert "/static/claim_detail.js?v=locked-global-zoom-right-v3-20260814-" in claim
