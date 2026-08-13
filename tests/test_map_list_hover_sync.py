from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_find_spots_hover_is_synchronised_both_ways():
    js = source("static/find_spots.js")
    assert "mapLayersBySpotId: new Map()" in js
    assert "item.addEventListener('mouseenter', () => setSpotMapHighlighted(spotId, true))" in js
    assert "item.addEventListener('mouseleave', () => setSpotMapHighlighted(spotId, false))" in js
    assert "setSpotListMapHighlighted(spot.id, true)" in js
    assert "setSpotListMapHighlighted(spot.id, false)" in js
    assert "MAP_COLOURS.highlight" in js
    assert "highlight: '#0582ca'" in js
    assert "highlight: '#1f2348'" not in js


def test_shared_map_supports_layer_highlighting_and_centre_callbacks():
    js = source("static/spot_map.js")
    assert "setSpotHighlighted(spotId, highlighted)" in js
    assert "onSpotHover = null" in js
    assert "onSpotCentreClick = null" in js
    assert "radiusInteractive = true" in js
    assert "marker.on('mouseover', () => onSpotHover(spot, true))" in js
    assert "marker.on('click', () => onSpotCentreClick(spot))" in js
    assert "entry.circle?.setStyle" in js
    assert "entry.marker?.setStyle" in js
    assert "highlightColour = '#0582ca'" in js


def test_my_spots_hover_links_map_and_rows_without_changing_click_navigation():
    js = source("static/my_spots.js")
    assert "setMySpotMapHighlighted(spot.id, true)" in js
    assert "setMySpotMapHighlighted(spot.id, false)" in js
    assert "onSpotHover: (spot, highlighted) => setMySpotListHighlighted(spot.id, highlighted)" in js
    assert "onSpotClick: openSpotPage" in js


def test_my_claims_centres_expand_scroll_and_hover_link_the_claim_row():
    js = source("static/my_claims.js")
    assert "onSpotCentreClick: (item) => focusClaimInList(item.id)" in js
    assert "radiusInteractive: false" in js
    assert "setClaimExpanded(item, summary, detail, Number(claimId), true)" in js
    assert "MAP_LIST_SCROLL_DURATION_MS = 420" in js
    assert "setClaimMapHighlighted(claimId, true)" in js
    assert "setClaimListMapHighlighted(item.id, highlighted)" in js
    assert "window.location.href = item.href" not in js


def test_blue_outline_and_description_spacing_are_intentional_shared_rules():
    css = source("static/home.css")
    assert ".spot-list-item.is-map-highlighted" in css
    assert "--nh-highlight-blue: #0582ca;" in css
    assert "outline: 2px solid var(--nh-highlight-blue);" in css
    assert ".nq-style .spot-list-detail > .spot-detail-description" in css
    assert "overrides Nimiq Style's generic paragraph margins" in css
    assert "margin: -0.35em 0 0.75em;" in css
    assert ".find-shell .spot-list-item.is-expanded .spot-list-detail .spot-detail-description" not in css


def test_asset_version_is_bumped():
    public_html = source("public_html.py")
    assert '_ASSET_VERSION = "stable-chevron-v1-20260813"' in public_html


def test_collapsible_chevron_uses_stable_geometry():
    css = source("static/home.css")
    assert "Stable geometric chevron for collapsible Spot/Claim/FAQ rows." in css
    assert ".spot-list-chevron::before" in css
    assert "text-indent: -9999px;" in css
    assert "transform-origin: 50% 50%;" in css
    assert "transform: rotate(0deg);" in css
    assert "transform: rotate(180deg);" in css
