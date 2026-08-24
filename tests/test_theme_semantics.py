import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_semantic_theme_layer_loads_after_neutral_theme():
    head = source("templates/_theme_head.html")

    theme_href = "/static/theme.css?v=dark-mode-v8-20260816"
    semantics_href = "/static/theme_semantics.css?v=semantic-accents-v1-20260816"
    assert theme_href in head
    assert semantics_href in head
    assert head.index(theme_href) < head.index(semantics_href)


def test_dark_claim_cards_keep_their_light_mode_semantic_colours():
    stylesheet = source("static/theme_semantics.css")

    standard = stylesheet.split(
        'html[data-theme="dark"] body.nq-style .spot-list-item.is-claim-standard {',
        1,
    )[1].split("}", 1)[0]
    conditional = stylesheet.split(
        'html[data-theme="dark"] body.nq-style .spot-list-item.is-claim-conditional {',
        1,
    )[1].split("}", 1)[0]
    prizedraw = stylesheet.split(
        'html[data-theme="dark"] body.nq-style .spot-list-item.is-claim-prizedraw {',
        1,
    )[1].split("}", 1)[0]

    assert "background: var(--nh-success);" in standard
    assert "color: #ffffff;" in standard
    assert "background: #0582ca;" in conditional
    assert "color: #ffffff;" in conditional
    assert "background: #ffc435;" in prizedraw
    assert "color: #1f2348;" in prizedraw

    assert ".spot-list-item.is-claim-standard .spot-detail-description" in stylesheet
    assert ".spot-list-item.is-claim-conditional .spot-detail-description" in stylesheet
    assert ".spot-list-item.is-claim-prizedraw .spot-detail-description" in stylesheet


def test_dark_claim_pill_keeps_normal_non_neutral_appearance():
    stylesheet = source("static/theme_semantics.css")

    claim_selector = (
        'html[data-theme="dark"] body.nq-style '
        '.spot-claim-button.nq-button-pill:not(.is-unavailable):not(.how-to-claim-button)'
    )
    assert claim_selector in stylesheet
    assert "background: #ffffff !important;" in stylesheet
    assert "0 10px 24px rgba(31, 35, 72, 0.20)" in stylesheet
    assert ":not(.is-unavailable)" in stylesheet


def test_dark_disabled_coloured_controls_keep_their_colour_family():
    stylesheet = source("static/theme_semantics.css")

    assert ".nq-button.green:is(" in stylesheet
    assert "background: var(--nh-success) !important;" in stylesheet
    assert ".nq-button.gold:is(" in stylesheet
    assert "background: #ffc435 !important;" in stylesheet
    assert ".nq-button.light-blue:not(.nh-grey-cancel-button):not(.create-spot-cancel):is(" in stylesheet
    assert "background: #0582ca !important;" in stylesheet
    assert ".nq-button.red:is(" in stylesheet
    assert "background: var(--nh-danger) !important;" in stylesheet
    assert "filter: saturate(0.58) brightness(0.92) !important;" in stylesheet
    assert "opacity: 0.78 !important;" in stylesheet


def test_dark_inactive_filters_remain_muted_versions_of_their_semantic_colours():
    stylesheet = source("static/theme_semantics.css")

    assert ".filter-toggle.is-off:not(.is-prizedraw):not(.is-test-location)" in stylesheet
    assert ".filter-toggle.is-off.is-prizedraw" in stylesheet
    assert ".filter-toggle.is-off.is-test-location" in stylesheet
    assert "filter: grayscale(0.55) !important;" in stylesheet
    assert "opacity: 0.72 !important;" in stylesheet
