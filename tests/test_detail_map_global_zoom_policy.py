from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_detail_maps_use_the_same_fixed_zoom_floor():
    for relative_path in ("static/spot_detail.js", "static/claim_detail.js"):
        page = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "const DETAIL_MAP_MIN_ZOOM = 12;" in page
        assert "minZoom: DETAIL_MAP_MIN_ZOOM," in page
        assert "map.getBoundsZoom(" not in page
        assert "map.setMinZoom(" not in page
