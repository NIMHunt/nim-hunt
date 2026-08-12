from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORLD_WRAP_CACHE_VERSION = "my-spots-world-wrap-v1-20260803"
PAGE_CACHE_VERSION = "rapid-deposit-v1-20260805"
BOOTSTRAP_CACHE_VERSION = "nimiq-2-compat-v1-20260812"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_spots_loads_world_wrap_before_the_page_module():
    template = source("templates/my_spots.html")
    bootstrap = source("static/my_spots_bootstrap.js")
    installer = source("static/my_spots_world_wrap_install.js")

    assert f"/static/my_spots_bootstrap.js?v={BOOTSTRAP_CACHE_VERSION}" in template
    assert 'src="/static/my_spots.js?' not in template
    assert f"./my_spots_world_wrap_install.js?v={WORLD_WRAP_CACHE_VERSION}" in bootstrap
    assert f"const MY_SPOTS_MODULE_URL = './my_spots.js?v={PAGE_CACHE_VERSION}'" in bootstrap
    assert "await import(MY_SPOTS_MODULE_URL);" in bootstrap
    assert f"./my_spots_world_wrap.js?v={WORLD_WRAP_CACHE_VERSION}" in installer
    assert "installMySpotsWorldWrap();" in installer


def test_my_spots_world_wrap_is_page_scoped_and_does_not_change_searches():
    world_wrap = source("static/my_spots_world_wrap.js")

    assert "leaflet.map =" in world_wrap
    assert "leaflet.circle =" in world_wrap
    assert "leaflet.circleMarker =" in world_wrap
    assert "leaflet.latLngBounds =" in world_wrap
    assert "fetch(" not in world_wrap
    assert "/api/" not in world_wrap


def test_my_spots_map_policy_only_filters_the_map():
    page = source("static/my_spots.js")
    policy = source("static/my_spots_map_policy.js")

    assert f"./my_spots_map_policy.js?v={PAGE_CACHE_VERSION}" in page
    assert "renderMap(spotsVisibleOnMySpotsMap(spots));" in page
    assert "renderSpots(spots);" in page
    assert "endsAt <= now" in policy
    assert "status === 'completed'" in policy
    assert "status === 'cancelled'" in policy
    assert "#0582ca" in policy
    assert "#d94432" in policy
    assert "fetch(" not in policy
    assert "/api/" not in policy
