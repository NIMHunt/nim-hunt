import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_admin_pages_share_nimhunt_theme_head():
    for path in (
        "templates/admin_login.html",
        "templates/admin_dashboard.html",
    ):
        assert '{% include "_theme_head.html" %}' in source(path), path


def test_shared_theme_ui_places_toggle_on_admin_pages():
    javascript = source("static/theme.js")
    stylesheet = source("static/admin_theme.css")

    assert "documentObj.querySelector('.admin-header')" in javascript
    assert 'form[action="/admin/logout"]' in javascript
    assert "actions.className = 'admin-header-actions'" in javascript
    assert "documentObj.querySelector('.admin-login-card')" in javascript
    assert "row.className = 'admin-login-theme-row'" in javascript
    assert "toggle.classList.add('admin-theme-toggle')" in javascript
    assert ".admin-theme-toggle" in stylesheet


def test_admin_toggle_reuses_shared_theme_preference_and_symbols():
    javascript = source("static/theme.js")

    assert "const STORAGE_KEY = 'nimhunt-theme'" in javascript
    assert "symbol: '◐'" in javascript
    assert "symbol: '☀'" in javascript
    assert "return explicitStoredTheme() || systemTheme();" in javascript
    assert "persist: true" in javascript
    assert "typeof documentObj.startViewTransition === 'function'" in javascript


def test_admin_theme_assets_are_cache_busted():
    theme_head = source("templates/_theme_head.html")

    assert "/static/admin_theme.css?v=admin-theme-v1-20260901" in theme_head
    assert "admin-toggle-v1-20260901" in theme_head
