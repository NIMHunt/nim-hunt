from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_find_spots_initial_view_is_loaded_before_the_page_module():
    bootstrap = source("static/find_spots_bootstrap.js")
    install_import = "./find_spots_initial_view_install.js?v=initial-view-v1-20260803"
    page_import = "./find_spots.js?v=wrapped-search-v2-20260803-chevron-cache-compat-v2-20260813"

    assert install_import in bootstrap
    assert page_import in bootstrap
    assert bootstrap.index(install_import) < bootstrap.index(page_import)


def test_find_spots_template_busts_the_bootstrap_cache():
    template = source("templates/find_spots.html")
    assert "/static/find_spots_bootstrap.js?v=initial-view-v1-20260803-{{ asset_version" in template


def test_find_spots_initial_view_uses_london_and_allows_a_wide_fit():
    module = source("static/find_spots_initial_view.js")

    assert "51.5074" in module
    assert "-0.1278" in module
    assert "FIND_SPOTS_MIN_ZOOM = 0" in module
    assert "maxZoom: FIND_SPOTS_INITIAL_MAX_ZOOM" in module
    assert "suppressLegacyZoomClamp" in module
