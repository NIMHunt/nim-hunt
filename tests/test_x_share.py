"""Regression tests for X share controls beside public-link copy buttons."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
X_SHARE_VERSION = "x-share-v2-20260725"
EXPECTED_TEMPLATES = {
    "find_spots.html",
    "my_spots.html",
    "my_claims.html",
    "spot.html",
    "claim.html",
}


def test_x_share_assets_load_everywhere_spot_link_rows_are_rendered() -> None:
    templates_with_script = set()
    templates_with_styles = set()

    for path in TEMPLATES.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        if f"/static/x_share.js?v={X_SHARE_VERSION}" in source:
            templates_with_script.add(path.name)
        if f"/static/x_share.css?v={X_SHARE_VERSION}" in source:
            templates_with_styles.add(path.name)

    assert templates_with_script == EXPECTED_TEMPLATES
    assert templates_with_styles == EXPECTED_TEMPLATES


def test_x_share_uses_official_web_intent_and_correct_row_url() -> None:
    source = (STATIC / "x_share.js").read_text(encoding="utf-8")

    assert "https://x.com/intent/tweet" in source
    assert "intentUrl.searchParams.set('url', shareUrl)" in source
    assert "row.querySelector('.spot-link-anchor')?.href" in source
    assert "if (isIndividualClaimPage()) return canonicalPageUrl()" in source
    assert "link[rel=\"canonical\"]" in source
    assert "pageUrl.hash = ''" in source
    assert "searchParams.set('text'" not in source


def test_x_share_link_is_inserted_after_public_link_copy_control() -> None:
    source = (STATIC / "x_share.js").read_text(encoding="utf-8")

    assert "copyButton.after(createXShareLink(shareUrlForRow(row)))" in source
    assert "row.querySelector('.spot-copy-button')" in source
    assert "row.querySelector('.spot-x-share-link')" in source
    assert "spot-copy-button spot-x-share-link" in source
    assert "link.target = '_blank'" in source
    assert "link.rel = 'noopener noreferrer'" in source
    assert "Share this claim on X" in source
    assert "Share this Spot on X" in source
    assert "new MutationObserver" in source


def test_x_share_button_has_black_circle_and_inverted_hover() -> None:
    source = (STATIC / "x_share.css").read_text(encoding="utf-8")

    assert ".nq-style .spot-x-share-link" in source
    assert "border-radius: 50%" in source
    assert "background: #000000 !important" in source
    assert "color: #ffffff !important" in source
    assert ".nq-style .spot-x-share-link:hover" in source
    assert ".nq-style .spot-x-share-link:focus-visible" in source
    assert "background: transparent !important" in source
    assert "color: #000000 !important" in source
    assert "width: 0.72em" in source
    assert "height: 0.72em" in source
    assert "fill: currentColor" in source


def test_claim_code_copy_buttons_are_not_share_targets() -> None:
    source = (STATIC / "x_share.js").read_text(encoding="utf-8")

    assert "'.spot-detail-link-row'" in source
    assert "spot-password-copy-button" not in source
