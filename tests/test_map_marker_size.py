"""Regression test for consistent Find Spots map marker sizing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_user_and_spot_markers_share_one_radius() -> None:
    source = (ROOT / "static/find_spots.js").read_text(encoding="utf-8")

    assert "const MAP_MARKER_RADIUS = 12;" in source
    assert (
        "const dot = L.circleMarker(latLng, {\n"
        "            radius: MAP_MARKER_RADIUS,"
    ) in source
    assert (
        "state.userMarker = L.circleMarker(position, {\n"
        "            radius: MAP_MARKER_RADIUS,"
    ) in source
