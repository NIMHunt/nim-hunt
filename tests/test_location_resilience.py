import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str) -> None:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_location_helper_falls_back_after_precise_timeout():
    run_node(
        r"""
        import fs from 'node:fs';
        const source = fs.readFileSync('static/location_utils.js', 'utf8');
        const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
        const { requestResilientLocation } = await import(url);
        const calls = [];
        const warnings = [];
        const geolocation = {
            getCurrentPosition(success, failure, options) {
                calls.push(options);
                if (calls.length === 1) {
                    failure({ code: 3, message: 'precise timeout' });
                    return;
                }
                success({
                    coords: { latitude: 55.86, longitude: -4.25, accuracy: 42 },
                });
            },
        };
        const result = await requestResilientLocation({
            geolocation,
            logger: { warn: (...args) => warnings.push(args) },
        });
        if (!result.ok || result.attempt !== 'fallback') process.exit(1);
        if (calls.length !== 2) process.exit(2);
        if (calls[0].enableHighAccuracy !== true) process.exit(3);
        if (calls[0].timeout !== 20000) process.exit(4);
        if (calls[1].enableHighAccuracy !== false) process.exit(5);
        if (warnings.length !== 1) process.exit(6);
        """
    )


def test_location_helper_does_not_repeat_a_denied_permission_request():
    run_node(
        r"""
        import fs from 'node:fs';
        const source = fs.readFileSync('static/location_utils.js', 'utf8');
        const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
        const { requestResilientLocation } = await import(url);
        let calls = 0;
        const geolocation = {
            getCurrentPosition(success, failure) {
                calls += 1;
                failure({ code: 1, message: 'denied' });
            },
        };
        const result = await requestResilientLocation({
            geolocation,
            logger: { warn: () => {} },
        });
        if (result.ok || result.kind !== 'permission_denied') process.exit(1);
        if (calls !== 1) process.exit(2);
        """
    )


def test_find_spots_exposes_retry_status_without_backend_changes():
    find_spots = (ROOT / "static" / "find_spots.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "find_spots.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "home.css").read_text(encoding="utf-8")
    interface_text = (ROOT / "static" / "interface_text.js").read_text(encoding="utf-8")

    assert "requestResilientLocation" in find_spots
    assert "maybeRetryLocationOnResume" in find_spots
    assert "find-location-status" in template
    assert 'class="filter-toggle map-location-status"' in template
    assert 'class="nq-button map-location-status"' not in template
    assert "Location Blocked. Retry?" in interface_text
    assert '.map-location-status.filter-toggle[data-location-state="permission_denied"]' in css
    assert "background: var(--nh-danger);" in css
    assert "/api/claim/" not in (ROOT / "static" / "location_utils.js").read_text(
        encoding="utf-8"
    )
