from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MY_SPOTS_OVERVIEW_CACHE_VERSION = "my-spots-overview-v3-20260802"
MY_SPOTS_BOOTSTRAP_CACHE_VERSION = "my-spots-world-wrap-v1-20260803"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_spots_loads_the_wide_overview_under_fresh_cache_keys():
    my_spots = source("static/my_spots.js")
    bootstrap = source("static/my_spots_bootstrap.js")
    template = source("templates/my_spots.html")

    assert (
        f"./spot_map.js?v={MY_SPOTS_OVERVIEW_CACHE_VERSION}"
        in my_spots
    ), "The changed shared map module needs its existing fresh import URL."
    assert (
        f"/static/my_spots_bootstrap.js?v={MY_SPOTS_BOOTSTRAP_CACHE_VERSION}-"
        in template
    ), "Returning webviews must load the new My Spots bootstrap URL."
    assert (
        f"./my_spots.js?v={MY_SPOTS_BOOTSTRAP_CACHE_VERSION}"
        in bootstrap
    ), "The bootstrap must load the My Spots entry module under a fresh URL."
    assert "minZoom: 0" in my_spots, "My Spots must be able to fit geographically distant Spots."
    assert "./spot_map.js?v=map-view-cache-v2-20260802" not in my_spots
