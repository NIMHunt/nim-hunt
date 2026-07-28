from pathlib import Path

# This helper exists only to apply and validate the requested PR correction.
ROOT = Path(__file__).resolve().parents[1]

css_path = ROOT / "static" / "home.css"
css = css_path.read_text(encoding="utf-8")
old = '''/* Reuse the existing filter-toggle pill styling; these rules only position
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
'''
new = '''/* Reuse the filter-toggle dimensions and typography exactly. These rules
   only position the pill over the map, normalise the button element, and set
   its state colours. */
.map-location-status.filter-toggle {
    position: absolute !important;
    z-index: 700;
    left: 50%;
    bottom: 14px;
    margin: 0 !important;
    border: 0;
    background: #ffffff;
    color: var(--nh-text);
    box-shadow: 0 0 32px rgba(31, 35, 72, 0.18);
    font-family: inherit;
    font-size: inherit;
    opacity: 1;
    transform: translateX(-50%);
}

.map-location-status.filter-toggle[hidden] {
    display: none !important;
}

.map-location-status.filter-toggle:disabled {
    opacity: 1;
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
'''
if css.count(old) != 1:
    raise RuntimeError("Expected the compact map-location CSS block exactly once")
css_path.write_text(css.replace(old, new, 1), encoding="utf-8")

test_path = ROOT / "tests" / "test_location_resilience.py"
test = test_path.read_text(encoding="utf-8")
needle = '''    assert "background: var(--nh-danger);" in css
    assert "/api/claim/" not in (ROOT / "static" / "location_utils.js").read_text(
'''
replacement = '''    assert "background: var(--nh-danger);" in css
    assert "min-width: 0 !important;" not in css
    assert "min-height: 2.7rem;" not in css
    assert "padding: 0.62rem 1rem;" not in css
    assert "font-size: 1rem;" not in css
    assert "opacity: 1;" in css
    assert "/api/claim/" not in (ROOT / "static" / "location_utils.js").read_text(
'''
if test.count(needle) != 1:
    raise RuntimeError("Expected the location style assertion block exactly once")
test_path.write_text(test.replace(needle, replacement, 1), encoding="utf-8")
