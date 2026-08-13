from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT_CACHE_VERSION = "wrapped-search-v1-20260803"
BOOTSTRAP_CACHE_VERSION = "wide-map-refresh-v1-20260813"
PAGE_CACHE_VERSION = "wide-map-refresh-v1-20260813"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_find_spots_loads_the_search_transport_before_the_page_module():
    template = source("templates/find_spots.html")
    bootstrap = source("static/find_spots_bootstrap.js")
    installer = source("static/find_spots_search_transport_install.js")

    assert (
        f"/static/find_spots_bootstrap.js?v={BOOTSTRAP_CACHE_VERSION}"
        in template
    )
    assert 'src="/static/find_spots.js?' not in template
    assert (
        f"./find_spots_search_transport_install.js?v={TRANSPORT_CACHE_VERSION}"
        in bootstrap
    )
    assert f"./find_spots.js?v={PAGE_CACHE_VERSION}" in bootstrap
    assert bootstrap.index("find_spots_search_transport_install.js") < bootstrap.index("find_spots.js")
    assert (
        f"./find_spots_search_transport.js?v={TRANSPORT_CACHE_VERSION}"
        in installer
    )
    assert "installFindSpotsSearchTransport();" in installer


def test_search_transport_is_scoped_to_visible_spot_searches():
    transport = source("static/find_spots_search_transport.js")

    assert "const SPOT_SEARCH_PATH = '/api/spots/search';" in transport
    assert "if (!url || url.pathname !== SPOT_SEARCH_PATH) return [];" in transport
    assert "if (urls.length === 0) return fetchImpl(input, options);" in transport
    assert "failureAlreadyReported" in transport
    assert "lastSuccessfulPayload" in transport
