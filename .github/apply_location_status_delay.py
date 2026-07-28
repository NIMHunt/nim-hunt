from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

find_spots_path = ROOT / "static" / "find_spots.js"
find_spots = find_spots_path.read_text(encoding="utf-8")

old_state = "    locationStatusTimerId: null,\n};"
new_state = "    locationStatusTimerId: null,\n    locationRequestStatusDelayTimerId: null,\n};"
if old_state not in find_spots:
    raise RuntimeError("Could not find location timer state")
find_spots = find_spots.replace(old_state, new_state, 1)

old_constant = "const LOCATION_RESUME_RETRY_COOLDOWN_MS = 10000;"
new_constant = (
    "const LOCATION_RESUME_RETRY_COOLDOWN_MS = 10000;\n"
    "const LOCATION_REQUEST_STATUS_DELAY_MS = 150;"
)
if old_constant not in find_spots:
    raise RuntimeError("Could not find location retry constant")
find_spots = find_spots.replace(old_constant, new_constant, 1)

marker = "function locationControlText(kind) {"
helpers = '''function clearLocationRequestStatusDelay() {
    if (!state.locationRequestStatusDelayTimerId) return;
    window.clearTimeout(state.locationRequestStatusDelayTimerId);
    state.locationRequestStatusDelayTimerId = null;
}

function showLocationRequestStatus(kind) {
    if (kind === 'requesting') {
        clearLocationRequestStatusDelay();
        state.locationRequestStatusDelayTimerId = window.setTimeout(() => {
            state.locationRequestStatusDelayTimerId = null;
            setLocationControlState(kind);
        }, LOCATION_REQUEST_STATUS_DELAY_MS);
        return;
    }

    clearLocationRequestStatusDelay();
    setLocationControlState(kind);
}

'''
if marker not in find_spots:
    raise RuntimeError("Could not find location control text function")
find_spots = find_spots.replace(marker, helpers + marker, 1)

old_status_callback = "            onStatus: (kind) => setLocationControlState(kind),"
new_status_callback = "            onStatus: (kind) => showLocationRequestStatus(kind),"
if old_status_callback not in find_spots:
    raise RuntimeError("Could not find geolocation status callback")
find_spots = find_spots.replace(old_status_callback, new_status_callback, 1)

old_failure = "            setLocationControlState(result.kind || 'position_unavailable');"
new_failure = "            showLocationRequestStatus(result.kind || 'position_unavailable');"
if old_failure not in find_spots:
    raise RuntimeError("Could not find location failure state")
find_spots = find_spots.replace(old_failure, new_failure, 1)

old_success = "        if (els.locationStatus) els.locationStatus.hidden = true;"
new_success = (
    "        clearLocationRequestStatusDelay();\n"
    "        if (els.locationStatus) els.locationStatus.hidden = true;"
)
if old_success not in find_spots:
    raise RuntimeError("Could not find location success hide")
find_spots = find_spots.replace(old_success, new_success, 1)

find_spots_path.write_text(find_spots, encoding="utf-8")

template_path = ROOT / "templates" / "find_spots.html"
template = template_path.read_text(encoding="utf-8")
old_cache_key = "-mobile-location-v2-20260728\"></script>"
new_cache_key = "-mobile-location-v3-20260728\"></script>"
if old_cache_key not in template:
    raise RuntimeError("Could not find Find Spots script cache key")
template = template.replace(old_cache_key, new_cache_key, 1)
template_path.write_text(template, encoding="utf-8")

test_path = ROOT / "tests" / "test_location_resilience.py"
test = test_path.read_text(encoding="utf-8")
anchor = '    assert "maybeRetryLocationOnResume" in find_spots\n'
addition = (
    anchor
    + '    assert "LOCATION_REQUEST_STATUS_DELAY_MS = 150" in find_spots\n'
    + '    assert "showLocationRequestStatus" in find_spots\n'
    + '    assert "onStatus: (kind) => showLocationRequestStatus(kind)" in find_spots\n'
    + '    assert "clearLocationRequestStatusDelay();" in find_spots\n'
)
if anchor not in test:
    raise RuntimeError("Could not find focused test anchor")
test = test.replace(anchor, addition, 1)
test_path.write_text(test, encoding="utf-8")
