"""Regression tests for the page-level X share controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"


def test_x_share_assets_load_only_on_spot_and_claim_pages() -> None:
    templates_with_script = set()
    templates_with_styles = set()

    for path in TEMPLATES.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        if "/static/x_share.js?v=x-share-v1-20260725" in source:
            templates_with_script.add(path.name)
        if "/static/x_share.css?v=x-share-v1-20260725" in source:
            templates_with_styles.add(path.name)

    assert templates_with_script == {"spot.html", "claim.html"}
    assert templates_with_styles == {"spot.html", "claim.html"}


def test_x_share_uses_official_web_intent_and_canonical_page_url() -> None:
    source = (STATIC / "x_share.js").read_text(encoding="utf-8")

    assert "https://x.com/intent/tweet" in source
    assert "intentUrl.searchParams.set('url', shareUrl)" in source
    assert "link[rel=\"canonical\"]" in source
    assert "pageUrl.hash = ''" in source
    assert "searchParams.set('text'" not in source


def test_x_share_link_is_inserted_after_copy_control_and_is_accessible() -> None:
    source = (STATIC / "x_share.js").read_text(encoding="utf-8")

    assert "copyButton.after(createXShareLink())" in source
    assert "spot-copy-button spot-x-share-link" in source
    assert "link.target = '_blank'" in source
    assert "link.rel = 'noopener noreferrer'" in source
    assert "Share this claim on X" in source
    assert "Share this Spot on X" in source
    assert "new MutationObserver" in source


def test_x_share_icon_matches_the_existing_inline_action_size() -> None:
    source = (STATIC / "x_share.css").read_text(encoding="utf-8")

    assert ".spot-x-share-link" in source
    assert ".spot-x-share-icon" in source
    assert "width: 0.95em" in source
    assert "height: 0.95em" in source
    assert "fill: currentColor" in source
