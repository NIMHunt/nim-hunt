from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Use the standard Nimiq button class so the location control is not mistaken
# for one of the checkbox-backed Find Spots filters.
template_path = ROOT / "templates" / "find_spots.html"
template = template_path.read_text(encoding="utf-8")
old_template = 'class="filter-toggle map-location-status"'
new_template = 'class="nq-button map-location-status"'
if old_template in template:
    template = template.replace(old_template, new_template, 1)
elif new_template not in template:
    raise RuntimeError("Could not find the location control class")
template_path.write_text(template, encoding="utf-8")

css_path = ROOT / "static" / "home.css"
css = css_path.read_text(encoding="utf-8")
old_css = '''/* Reuse the filter-toggle dimensions and typography exactly. These rules
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
new_css = '''/* Use Nimiq button styling directly. These rules only place the control over
   the map, let it expand to its message, and colour the blocked state. */
.map-location-status.nq-button {
    position: absolute !important;
    z-index: 700;
    left: 0;
    right: 0;
    bottom: 14px;
    width: max-content !important;
    max-width: calc(100% - 28px);
    margin: 0 auto !important;
    white-space: nowrap;
}

.map-location-status.nq-button[hidden] {
    display: none !important;
}

.map-location-status.nq-button:disabled {
    opacity: 1;
    cursor: default;
}

.map-location-status.nq-button.is-retry {
    cursor: pointer;
}

.map-location-status.nq-button[data-location-state="permission_denied"] {
    background: var(--nh-danger) !important;
    color: #ffffff !important;
    box-shadow: 0 0 32px rgba(217, 68, 68, 0.22) !important;
}
'''
if old_css in css:
    css = css.replace(old_css, new_css, 1)
elif new_css not in css:
    raise RuntimeError("Could not find the current location-control CSS block")
css_path.write_text(css, encoding="utf-8")

# Keep the focused regression test aligned with the requested presentation.
test_path = ROOT / "tests" / "test_location_resilience.py"
test = test_path.read_text(encoding="utf-8")
test = test.replace(
    'assert \'class="filter-toggle map-location-status"\' in template\n'
    '    assert \'class="nq-button map-location-status"\' not in template',
    'assert \'class="nq-button map-location-status"\' in template\n'
    '    assert \'class="filter-toggle map-location-status"\' not in template',
)
old_assertions = '''    assert '.map-location-status.filter-toggle[data-location-state="permission_denied"]' in css
    location_css = css[
        css.index("/* Reuse the filter-toggle dimensions and typography exactly.") :
    ]

    assert "background: var(--nh-danger);" in location_css
    assert "min-width: 0 !important;" not in location_css
    assert "min-height: 2.7rem;" not in location_css
    assert "padding: 0.62rem 1rem;" not in location_css
    assert "font-size: 1rem;" not in location_css
    assert "opacity: 1;" in location_css
'''
new_assertions = '''    assert '.map-location-status.nq-button[data-location-state="permission_denied"]' in css
    location_css = css[
        css.index("/* Use Nimiq button styling directly.") :
    ]

    assert "background: var(--nh-danger) !important;" in location_css
    assert "width: max-content !important;" in location_css
    assert "white-space: nowrap;" in location_css
    assert "opacity: 1;" in location_css
    assert "padding:" not in location_css
    assert "font-size:" not in location_css
    assert "font-weight:" not in location_css
'''
if old_assertions in test:
    test = test.replace(old_assertions, new_assertions, 1)
elif new_assertions not in test:
    raise RuntimeError("Could not find the location style assertions")
test_path.write_text(test, encoding="utf-8")
