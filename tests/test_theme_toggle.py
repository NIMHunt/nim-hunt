import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_theme_head_is_loaded_by_every_page_shell():
    theme_head = source("templates/_theme_head.html")
    assert "/static/theme.css?v=dark-mode-v1-20260816" in theme_head
    assert "/static/theme.js?v=dark-mode-v1-20260816" in theme_head

    for path in (
        "templates/_home_shell.html",
        "templates/find_spots.html",
        "templates/claim.html",
        "templates/create_spot.html",
        "templates/my_claims.html",
        "templates/my_spots.html",
        "templates/spot.html",
    ):
        assert '{% include "_theme_head.html" %}' in source(path), path


def test_footer_theme_toggle_is_inserted_between_how_to_and_faq_slots():
    javascript = source("static/theme.js")
    stylesheet = source("static/theme.css")

    assert "documentObj.querySelectorAll('.home-information-links > a')" in javascript
    assert "links[1].after(toggle);" in javascript
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in stylesheet


def test_theme_toggle_uses_requested_symbols_and_tooltips():
    javascript = source("static/theme.js")

    assert "symbol: '◐'" in javascript
    assert "symbol: '☀'" in javascript
    assert "label: 'Switch to dark mode'" in javascript
    assert "label: 'Switch to light mode'" in javascript
    assert "toggle.dataset.tooltip = presentation.label" in javascript
    assert "toggle.setAttribute('title', presentation.label)" in javascript
    assert "toggle.setAttribute('aria-label', presentation.label)" in javascript


def test_theme_defaults_to_light_and_persists_an_explicit_choice():
    javascript = source("static/theme.js")

    assert "const STORAGE_KEY = 'nimhunt-theme'" in javascript
    assert "window.localStorage.getItem(STORAGE_KEY) === DARK_THEME ? DARK_THEME : LIGHT_THEME" in javascript
    assert "window.localStorage.setItem(STORAGE_KEY, theme)" in javascript
    assert "persist: true" in javascript
    assert "window.addEventListener('storage'" in javascript


def test_dark_mode_changes_neutrals_without_redefining_action_colours():
    stylesheet = source("static/theme.css")

    assert '--nh-bg-1: #1f2348;' in stylesheet
    assert '--nh-bg-2: #151833;' in stylesheet
    assert '--nh-text: #fafafa;' in stylesheet
    assert '--nh-card: rgba(31, 35, 72, 0.94);' in stylesheet

    for accent_variable in (
        "--nh-danger:",
        "--nh-warning:",
        "--nh-success:",
        "--nh-highlight-blue:",
    ):
        assert accent_variable not in stylesheet


def test_dark_mode_adapts_leaflet_chrome_but_not_map_tiles():
    stylesheet = source("static/theme.css")

    assert 'html[data-theme="dark"] .leaflet-popup-content-wrapper' in stylesheet
    assert 'html[data-theme="dark"] .leaflet-control-zoom a' in stylesheet
    assert 'html[data-theme="dark"] .leaflet-control-attribution' in stylesheet
    assert "filter: invert" not in stylesheet


def test_bundled_nimiq_icons_follow_foreground_colour():
    icons = source("static/nimiq-style.icons.svg")
    assert "currentColor" in icons
