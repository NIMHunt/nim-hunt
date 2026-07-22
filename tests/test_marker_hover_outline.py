from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_find_spots_hover_changes_marker_fill_only():
    js = source("static/find_spots.js")
    assert "entry.dot?.setStyle({ fillColor: colour });" in js
    assert "color: highlighted ? colour : '#ffffff'" not in js
    assert "color: '#ffffff'" in js
    assert "className: `spot-centre-marker" in js


def test_shared_map_hover_changes_marker_fill_only():
    js = source("static/spot_map.js")
    assert "entry.marker?.setStyle({ fillColor: colour });" in js
    assert "color: highlighted ? colour : '#ffffff'" not in js
    assert "color: '#ffffff'" in js


def test_marker_shadow_and_cache_versions_are_preserved():
    css = source("static/home.css")
    public_html = source("public_html.py")
    my_spots = source("static/my_spots.js")
    my_claims = source("static/my_claims.js")
    assert ".spot-centre-marker {" in css
    assert "filter: drop-shadow(0 3px 4px rgba(31, 35, 72, 0.18));" in css
    assert '_ASSET_VERSION = "marker-white-outline-v1-20260723"' in public_html
    assert "spot_map.js?v=marker-white-outline-v1-20260723" in my_spots
    assert "spot_map.js?v=marker-white-outline-v1-20260723" in my_claims
