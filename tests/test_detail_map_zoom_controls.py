from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_spot_detail_map_only_allows_explicit_zoom_controls():
    page = source("static/spot_detail.js")
    assert "zoomControl: true" in page
    assert "dragging: false" in page
    assert "touchZoom: false" in page
    assert "scrollWheelZoom: false" in page
    assert "doubleClickZoom: false" in page
    assert "boxZoom: false" in page
    assert "keyboard: false" in page
    assert "map.on('moveend zoomend', keepCentred);" not in page


def test_spot_detail_zoom_floor_is_derived_from_spot_radius():
    page = source("static/spot_detail.js")
    assert "const radiusBounds = metreBoundsAround(centre[0], centre[1], spot.radius).pad(0.18);" in page
    assert "map.getBoundsZoom(radiusBounds, false)" in page
    assert "map.setMinZoom(minZoom);" in page


def test_claim_detail_keeps_gestures_locked_and_limits_zoom_out_by_radius():
    page = source("static/claim_detail.js")
    assert "zoomControl: true" in page
    assert "dragging: false" in page
    assert "touchZoom: false" in page
    assert "scrollWheelZoom: false" in page
    assert "doubleClickZoom: false" in page
    assert "const radiusBounds = metreBoundsAround(centre[0], centre[1], spot.radius).pad(0.18);" in page
    assert "map.getBoundsZoom(radiusBounds, false)" in page
    assert "map.setMinZoom(minZoom);" in page


def test_detail_map_cache_keys_are_bumped():
    spot = source("templates/spot.html")
    claim = source("templates/claim.html")
    assert "/static/spot_detail.js?v=locked-radius-zoom-v1-20260814-" in spot
    assert "/static/claim_detail.js?v=locked-radius-zoom-v1-20260814-" in claim
