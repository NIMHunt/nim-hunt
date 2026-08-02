from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MY_SPOTS_OVERVIEW_CACHE_VERSION = "my-spots-overview-v3-20260802"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_spots_loads_the_wide_overview_under_fresh_cache_keys():
    my_spots = source("static/my_spots.js")
    template = source("templates/my_spots.html")

    assert (
        f"./spot_map.js?v={MY_SPOTS_OVERVIEW_CACHE_VERSION}"
        in my_spots
    ), "The changed shared map module needs a new import URL."
    assert (
        f"/static/my_spots.js?v={MY_SPOTS_OVERVIEW_CACHE_VERSION}-"
        in template
    ), "The My Spots entry module also needs a new URL for returning webviews."
    assert "minZoom: 0" in my_spots, "My Spots must be able to fit geographically distant Spots."
    assert "./spot_map.js?v=map-view-cache-v2-20260802" not in my_spots
