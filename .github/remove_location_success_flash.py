from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

find_spots_path = ROOT / "static" / "find_spots.js"
find_spots = find_spots_path.read_text(encoding="utf-8")
old_success = "        setLocationControlState('success', { hideAfterMs: 1000 });"
new_success = "        if (els.locationStatus) els.locationStatus.hidden = true;"
if old_success not in find_spots:
    raise RuntimeError("Could not find the transient location success state")
find_spots = find_spots.replace(old_success, new_success, 1)
find_spots_path.write_text(find_spots, encoding="utf-8")

template_path = ROOT / "templates" / "find_spots.html"
template = template_path.read_text(encoding="utf-8")
old_script_version = "-mobile-location-v1-20260728\"></script>"
new_script_version = "-mobile-location-v2-20260728\"></script>"
if old_script_version not in template:
    raise RuntimeError("Could not find the Find Spots script cache key")
template = template.replace(old_script_version, new_script_version, 1)
template_path.write_text(template, encoding="utf-8")

test_path = ROOT / "tests" / "test_location_resilience.py"
test_text = test_path.read_text(encoding="utf-8")
needle = '    assert "Location Blocked. Retry?" in interface_text\n'
addition = (
    needle
    + "    assert \"setLocationControlState('success'\" not in find_spots\n"
    + '    assert "els.locationStatus.hidden = true" in find_spots\n'
)
if needle not in test_text:
    raise RuntimeError("Could not find the location presentation assertions")
test_text = test_text.replace(needle, addition, 1)
test_path.write_text(test_text, encoding="utf-8")
