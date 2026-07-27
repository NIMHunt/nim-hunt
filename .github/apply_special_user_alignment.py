from pathlib import Path


HOME_CSS = Path("static/home.css")
TEST_FILE = Path("tests/test_special_users.py")

old_css = """.special-user-display-name {
    display: inline-flex;
    align-items: center;
    gap: 0.24em;
    vertical-align: middle;
}
"""
new_css = """.special-user-display-name {
    display: inline-flex;
    align-items: center;
    gap: 0.24em;
    vertical-align: baseline;
}
"""

css = HOME_CSS.read_text(encoding="utf-8")
if new_css not in css:
    if css.count(old_css) != 1:
        raise RuntimeError("Expected exactly one special-user display-name CSS block")
    HOME_CSS.write_text(css.replace(old_css, new_css, 1), encoding="utf-8")

old_test_read = """    interface_text = (root / "static" / "interface_text.js").read_text(encoding="utf-8")
    icon_sprite = (root / "static" / "nimiq-style.icons.svg").read_text(encoding="utf-8")
"""
new_test_read = """    interface_text = (root / "static" / "interface_text.js").read_text(encoding="utf-8")
    home_css = (root / "static" / "home.css").read_text(encoding="utf-8")
    icon_sprite = (root / "static" / "nimiq-style.icons.svg").read_text(encoding="utf-8")
"""
old_test_assert = """    assert ".special-user-badge" in find_spots
    assert 'id="nq-hexagon"' in icon_sprite
"""
new_test_assert = """    assert ".special-user-badge" in find_spots
    assert "vertical-align: baseline;" in home_css
    assert 'id="nq-hexagon"' in icon_sprite
"""

tests = TEST_FILE.read_text(encoding="utf-8")
if "home_css = (root / \"static\" / \"home.css\")" not in tests:
    if tests.count(old_test_read) != 1 or tests.count(old_test_assert) != 1:
        raise RuntimeError("Expected special-user frontend test structure was not found")
    tests = tests.replace(old_test_read, new_test_read, 1)
    tests = tests.replace(old_test_assert, new_test_assert, 1)
    TEST_FILE.write_text(tests, encoding="utf-8")
