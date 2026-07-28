from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    file_path = ROOT / path
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Expected one match in {path}, found {count}: {old[:100]!r}"
        )
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    file_path = ROOT / path
    text = file_path.read_text(encoding="utf-8")
    if marker in text:
        raise RuntimeError(f"Marker already present in {path}: {marker!r}")
    file_path.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


location_utils = r"""const GEOLOCATION_ERROR_KINDS = Object.freeze({
    1: 'permission_denied',
    2: 'position_unavailable',
    3: 'timeout',
});

const DEFAULT_ATTEMPTS = Object.freeze([
    Object.freeze({
        name: 'precise',
        status: 'requesting',
        options: Object.freeze({
            enableHighAccuracy: true,
            timeout: 20000,
            maximumAge: 60000,
        }),
    }),
    Object.freeze({
        name: 'fallback',
        status: 'fallback',
        options: Object.freeze({
            enableHighAccuracy: false,
            timeout: 12000,
            maximumAge: 120000,
        }),
    }),
]);

function errorKind(error) {
    return GEOLOCATION_ERROR_KINDS[Number(error?.code)] || 'position_unavailable';
}

function locationFromPosition(position) {
    const lat = Number(position?.coords?.latitude);
    const long = Number(position?.coords?.longitude);
    const accuracy = Number(position?.coords?.accuracy);
    if (!Number.isFinite(lat) || !Number.isFinite(long)) {
        const error = new Error('The device returned invalid coordinates.');
        error.code = 2;
        throw error;
    }
    return {
        lat,
        long,
        accuracy: Number.isFinite(accuracy) ? accuracy : null,
    };
}

function getCurrentPosition(geolocation, options) {
    return new Promise((resolve, reject) => {
        geolocation.getCurrentPosition(resolve, reject, options);
    });
}

function logFailure(logger, attempt, error, kind) {
    const warn = logger?.warn;
    if (typeof warn !== 'function') return;
    warn.call(logger, '[NimHunt] Geolocation request failed.', {
        attempt,
        kind,
        code: Number(error?.code || 0),
        message: String(error?.message || 'Unknown geolocation error'),
    });
}

export async function requestResilientLocation({
    geolocation = globalThis.navigator?.geolocation,
    onStatus = () => {},
    logger = globalThis.console,
    attempts = DEFAULT_ATTEMPTS,
} = {}) {
    if (!geolocation || typeof geolocation.getCurrentPosition !== 'function') {
        const error = new Error('Geolocation is not available in this browser.');
        logFailure(logger, 'unsupported', error, 'unsupported');
        return { ok: false, kind: 'unsupported', error };
    }

    let lastError = null;
    let lastKind = 'position_unavailable';

    for (const attempt of attempts) {
        onStatus(attempt.status);
        try {
            const position = await getCurrentPosition(geolocation, attempt.options);
            return {
                ok: true,
                location: locationFromPosition(position),
                attempt: attempt.name,
            };
        } catch (error) {
            lastError = error;
            lastKind = errorKind(error);
            logFailure(logger, attempt.name, error, lastKind);
            if (lastKind === 'permission_denied') break;
        }
    }

    return { ok: false, kind: lastKind, error: lastError };
}
"""
(ROOT / "static/location_utils.js").write_text(location_utils, encoding="utf-8")

replace_once(
    "static/find_spots.js",
    "import { init, requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';\n",
    "import { init, requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';\n"
    "import { requestResilientLocation } from './location_utils.js?v=mobile-location-v1-20260728';\n",
)
replace_once(
    "static/find_spots.js",
    "import { getReportReasonOptions, makeFindSpotsText, makeSpotDetailText } from './interface_text.js?v=special-user-badge-v1-20260727';",
    "import { getReportReasonOptions, makeFindSpotsText, makeSpotDetailText } from './interface_text.js?v=mobile-location-v1-20260728';",
)
replace_once(
    "static/find_spots.js",
    "    cancelSpot: null,\n    cancelInProgress: false,\n};",
    "    cancelSpot: null,\n"
    "    cancelInProgress: false,\n"
    "    locationRequestInFlight: false,\n"
    "    lastLocationRequestAt: 0,\n"
    "    locationStatusTimerId: null,\n"
    "};",
)
replace_once(
    "static/find_spots.js",
    "const MAP_LIST_SCROLL_DURATION_MS = 420;\nconst CREATE_SPOT_URL = document.body.dataset.createSpotUrl || '/create';",
    "const MAP_LIST_SCROLL_DURATION_MS = 420;\n"
    "const LOCATION_RESUME_RETRY_COOLDOWN_MS = 10000;\n"
    "const CREATE_SPOT_URL = document.body.dataset.createSpotUrl || '/create';",
)
replace_once(
    "static/find_spots.js",
    "    map: document.getElementById('spot-map'),\n    filterActive: document.getElementById('filter-active'),",
    "    map: document.getElementById('spot-map'),\n"
    "    locationStatus: document.getElementById('find-location-status'),\n"
    "    filterActive: document.getElementById('filter-active'),",
)

old_location_function = r"""function requestLocation() {
    return new Promise((resolve) => {
        if (!navigator.geolocation) {
            resolve(null);
            return;
        }

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                resolve({
                    lat: pos.coords.latitude,
                    long: pos.coords.longitude,
                    accuracy: pos.coords.accuracy,
                });
            },
            () => resolve(null),
            {
                enableHighAccuracy: true,
                timeout: 8000,
                maximumAge: 60000,
            }
        );
    });
}
"""
new_location_functions = r"""function locationControlText(kind) {
    const text = UI_COPY.locationStatus || {};
    const values = {
        requesting: text.requesting || 'Finding location…',
        fallback: text.fallback || 'Still finding location…',
        success: text.success || 'Location found',
        permission_denied: text.permissionDenied || 'Location blocked — Retry',
        unsupported: text.unsupported || 'Location unavailable',
        position_unavailable: text.retry || 'Retry Location',
        timeout: text.retry || 'Retry Location',
    };
    return values[kind] || text.retry || 'Retry Location';
}

function setLocationControlState(kind, { hideAfterMs = 0 } = {}) {
    const control = els.locationStatus;
    if (!control) return;

    if (state.locationStatusTimerId) {
        window.clearTimeout(state.locationStatusTimerId);
        state.locationStatusTimerId = null;
    }

    const retryable = ['permission_denied', 'position_unavailable', 'timeout'].includes(kind);
    control.textContent = locationControlText(kind);
    control.disabled = !retryable;
    control.hidden = false;
    control.dataset.locationState = kind;
    control.classList.toggle('is-retry', retryable);

    if (hideAfterMs > 0) {
        state.locationStatusTimerId = window.setTimeout(() => {
            control.hidden = true;
            state.locationStatusTimerId = null;
        }, hideAfterMs);
    }
}

async function refreshUserLocation({ showFailureNotice = false, recenter = false } = {}) {
    if (state.locationRequestInFlight) return null;

    state.locationRequestInFlight = true;
    state.lastLocationRequestAt = Date.now();
    try {
        const result = await requestResilientLocation({
            onStatus: (kind) => setLocationControlState(kind),
        });

        if (!result.ok) {
            setLocationControlState(result.kind || 'position_unavailable');
            if (showFailureNotice) showNotice(UI_COPY.notices.locationUnavailable);
            return null;
        }

        const location = result.location;
        const hadLocation = state.hasUserLocation;
        setRecordedLocation({
            lat: location.lat,
            long: location.long,
            accuracy: location.accuracy,
            isReal: true,
        });
        setLocationControlState('success', { hideAfterMs: 1000 });

        if (state.map) {
            setMapInteractionEnabled(false);
            if (recenter || !hadLocation) {
                state.map.setView(
                    [location.lat, location.long],
                    Math.max(14, state.map.getZoom()),
                    { animate: false },
                );
            } else {
                await refreshVisibleSpots();
            }
        }
        return location;
    } finally {
        state.locationRequestInFlight = false;
    }
}

function maybeRetryLocationOnResume() {
    if (state.testLocationMode || state.hasUserLocation || state.locationRequestInFlight) return;
    if (Date.now() - state.lastLocationRequestAt < LOCATION_RESUME_RETRY_COOLDOWN_MS) return;
    void refreshUserLocation({ recenter: true });
}
"""
replace_once("static/find_spots.js", old_location_function, new_location_functions)
replace_once(
    "static/find_spots.js",
    "    const location = await requestLocation();\n"
    "    if (location) {\n"
    "        setRecordedLocation({ lat: location.lat, long: location.long, accuracy: location.accuracy, isReal: true });\n"
    "    } else {\n"
    "        showNotice(UI_COPY.notices.locationUnavailable);\n"
    "    }",
    "    await refreshUserLocation({ showFailureNotice: true });",
)
replace_once(
    "static/find_spots.js",
    "els.noticeOk.addEventListener('click', () => {\n",
    "els.locationStatus?.addEventListener('click', () => {\n"
    "    if (els.locationStatus.disabled) return;\n"
    "    void refreshUserLocation({ recenter: true });\n"
    "});\n\n"
    "els.noticeOk.addEventListener('click', () => {\n",
)
replace_once(
    "static/find_spots.js",
    "document.addEventListener('visibilitychange', () => {\n"
    "    if (document.visibilityState === 'visible') {\n"
    "        void runLiveRefresh();\n"
    "    } else {\n"
    "        stopLiveRefresh();\n"
    "    }\n"
    "});\n"
    "window.addEventListener('pageshow', () => {\n"
    "    if (state.map) void runLiveRefresh();\n"
    "});",
    "document.addEventListener('visibilitychange', () => {\n"
    "    if (document.visibilityState === 'visible') {\n"
    "        void runLiveRefresh();\n"
    "        maybeRetryLocationOnResume();\n"
    "    } else {\n"
    "        stopLiveRefresh();\n"
    "    }\n"
    "});\n"
    "window.addEventListener('pageshow', () => {\n"
    "    if (state.map) void runLiveRefresh();\n"
    "    maybeRetryLocationOnResume();\n"
    "});",
)

replace_once(
    "templates/find_spots.html",
    '<link rel="stylesheet" href="/static/home.css?v={{ asset_version | default(\'claim-ui-polish-v1-20260704\') }}">',
    '<link rel="stylesheet" href="/static/home.css?v={{ asset_version | default(\'claim-ui-polish-v1-20260704\') }}-mobile-location-v1-20260728">',
)
replace_once(
    "templates/find_spots.html",
    "        <section class=\"map-card\" aria-label=\"Spot map\" data-i18n-aria-label=\"findSpots.mapAria\">\n"
    "            <div id=\"spot-map\" class=\"spot-map\"></div>\n"
    "        </section>",
    "        <section class=\"map-card\" aria-label=\"Spot map\" data-i18n-aria-label=\"findSpots.mapAria\">\n"
    "            <div class=\"map-location-shell\">\n"
    "                <div id=\"spot-map\" class=\"spot-map\"></div>\n"
    "                <button id=\"find-location-status\" class=\"nq-button map-location-status\" type=\"button\" aria-live=\"polite\" hidden>Finding location…</button>\n"
    "            </div>\n"
    "        </section>",
)
replace_once(
    "templates/find_spots.html",
    '<script type="module" src="/static/find_spots.js?v={{ asset_version | default(\'claim-ui-polish-v1-20260704\') }}-claim-live-v2-20260720"></script>',
    '<script type="module" src="/static/find_spots.js?v={{ asset_version | default(\'claim-ui-polish-v1-20260704\') }}-mobile-location-v1-20260728"></script>',
)

replace_once(
    "static/interface_text.js",
    "        notices: {\n"
    "            locationUnavailable: {\n"
    "                title: 'Location unavailable',\n"
    "                body: `${appName} could not read your location. You can still move the map manually. Distances are hidden until location is available.`,\n"
    "            },",
    "        locationStatus: {\n"
    "            requesting: 'Finding location…',\n"
    "            fallback: 'Still finding location…',\n"
    "            success: 'Location found',\n"
    "            retry: 'Retry Location',\n"
    "            permissionDenied: 'Location blocked — Retry',\n"
    "            unsupported: 'Location unavailable',\n"
    "        },\n"
    "        notices: {\n"
    "            locationUnavailable: {\n"
    "                title: 'Location unavailable',\n"
    "                body: `${appName} could not read your location. You can still move the map manually. Distances are hidden until location is available.`,\n"
    "            },",
)

location_css = r"""/* -----------------------------
   Resilient mobile location control
   ----------------------------- */

.map-location-shell {
    position: relative;
}

.map-location-status.nq-button {
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
append_once("static/home.css", "Resilient mobile location control", location_css)

test_file = r'''import subprocess
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

    assert "requestResilientLocation" in find_spots
    assert "maybeRetryLocationOnResume" in find_spots
    assert "find-location-status" in template
    assert "map-location-status" in css
    assert "/api/claim/" not in (ROOT / "static" / "location_utils.js").read_text(
        encoding="utf-8"
    )
'''
(ROOT / "tests/test_location_resilience.py").write_text(test_file, encoding="utf-8")
