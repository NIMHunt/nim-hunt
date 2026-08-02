from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP_VIEW_CACHE_VERSION = "map-view-cache-v2-20260802"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_spots_loads_the_fixed_map_under_fresh_cache_keys():
    my_spots = source("static/my_spots.js")
    template = source("templates/my_spots.html")

    assert (
        f"./spot_map.js?v={MAP_VIEW_CACHE_VERSION}"
        in my_spots
    ), "The changed shared map module needs a new import URL."
    assert (
        f"/static/my_spots.js?v={MAP_VIEW_CACHE_VERSION}-"
        in template
    ), "The My Spots entry module also needs a new URL for returning webviews."
    assert "./spot_map.js?v=marker-white-outline-v1-20260723" not in my_spots
