from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"Expected exactly one match in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "static" / "interface_text.js",
    "permissionDenied: 'Location blocked — Retry',",
    "permissionDenied: 'Location Blocked. Retry?',",
)

replace_once(
    ROOT / "static" / "find_spots.js",
    "permission_denied: text.permissionDenied || 'Location blocked — Retry',",
    "permission_denied: text.permissionDenied || 'Location Blocked. Retry?',",
)

replace_once(
    ROOT / "templates" / "find_spots.html",
    'class="nq-button map-location-status"',
    'class="filter-toggle map-location-status"',
)

css_path = ROOT / "static" / "home.css"
css = css_path.read_text(encoding="utf-8")
old_css = """.map-location-status.nq-button {
    position: absolute !important;
    z-index: 700;
    left: 50%;
    bottom: 14px;
    width: auto !important;
    min-width: 0 !important;
    min-height: 2.7rem !important;
    height: auto !important;
    margin: 0 !important;
    padding: 0.62rem 1rem !important;
    border: 1px solid rgba(31, 35, 72, 0.12) !important;
    border-radius: 999px !important;
    background: rgba(255, 255, 255, 0.96) !important;
    color: var(--nh-text) !important;
    box-shadow: 0 8px 22px rgba(31, 35, 72, 0.18) !important;
    font-size: 1rem !important;
    font-weight: 900 !important;
    line-height: 1 !important;
    text-transform: none !important;
    white-space: nowrap;
    transform: translateX(-50%) !important;
}

.map-location-status.nq-button[hidden] {
    display: none !important;
}

.map-location-status.nq-button:disabled {
    opacity: 0.88;
    cursor: default;
}

.map-location-status.nq-button.is-retry {
    cursor: pointer;
}

.map-location-status.nq-button::before,
.map-location-status.nq-button::after {
    display: none !important;
    content: none !important;
}

.map-location-status.nq-button:hover,
.map-location-status.nq-button:focus,
.map-location-status.nq-button:active {
    transform: translateX(-50%) !important;
}

.map-location-status.nq-button:focus-visible {
    outline: 3px solid rgba(33, 188, 165, 0.42);
    outline-offset: 3px;
}
"""
new_css = """/* Reuse the existing filter-toggle pill styling; these rules only position
   and compact the control for use over the map. */
.map-location-status.filter-toggle {
    position: absolute !important;
    z-index: 700;
    left: 50%;
    bottom: 14px;
    width: auto !important;
    min-width: 0 !important;
    height: auto;
    min-height: 2.7rem;
    margin: 0 !important;
    padding: 0.62rem 1rem;
    border: 1px solid rgba(31, 35, 72, 0.12);
    background: rgba(255, 255, 255, 0.96);
    color: var(--nh-text);
    box-shadow: 0 8px 22px rgba(31, 35, 72, 0.18);
    font-size: 1rem;
    white-space: nowrap;
    transform: translateX(-50%);
}

.map-location-status.filter-toggle[hidden] {
    display: none !important;
}

.map-location-status.filter-toggle:disabled {
    opacity: 0.88;
    cursor: default;
}

.map-location-status.filter-toggle.is-retry {
    cursor: pointer;
}

.map-location-status.filter-toggle[data-location-state="permission_denied"] {
    background: var(--nh-danger);
    color: #ffffff;
    box-shadow: 0 0 32px rgba(217, 68, 68, 0.22);
}

.map-location-status.filter-toggle:hover,
.map-location-status.filter-toggle:focus,
.map-location-status.filter-toggle:active {
    transform: translateX(-50%);
}

.map-location-status.filter-toggle:focus-visible {
    outline: 3px solid rgba(33, 188, 165, 0.42);
    outline-offset: 3px;
}
"""
if css.count(old_css) != 1:
    raise RuntimeError("Expected the existing map location control CSS block exactly once")
css_path.write_text(css.replace(old_css, new_css, 1), encoding="utf-8")

test_path = ROOT / "tests" / "test_location_resilience.py"
test_text = test_path.read_text(encoding="utf-8")
old_test = """    assert \"find-location-status\" in template
    assert \"map-location-status\" in css
    assert \"/api/claim/\" not in (ROOT / \"static\" / \"location_utils.js\").read_text(
"""
new_test = """    interface_text = (ROOT / \"static\" / \"interface_text.js\").read_text(encoding=\"utf-8\")

    assert \"find-location-status\" in template
    assert 'class=\"filter-toggle map-location-status\"' in template
    assert 'class=\"nq-button map-location-status\"' not in template
    assert \"Location Blocked. Retry?\" in interface_text
    assert '.map-location-status.filter-toggle[data-location-state=\"permission_denied\"]' in css
    assert \"background: var(--nh-danger);\" in css
    assert \"/api/claim/\" not in (ROOT / \"static\" / \"location_utils.js\").read_text(
"""
if test_text.count(old_test) != 1:
    raise RuntimeError("Expected the focused location test assertion block exactly once")
test_path.write_text(test_text.replace(old_test, new_test, 1), encoding="utf-8")
