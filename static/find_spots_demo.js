export const DEMO_SPOT_ID = -2147483001;
export const DEMO_RADIUS_METRES = 200;
export const DEMO_DISTANCE_METRES = 250;
export const DEMO_COLOUR = '#8f5bd7';
export const DEMO_STORAGE_KEY = 'nimhunt-demo-spot-v1';
export const DEMO_COMPLETED_KEY = 'nimhunt-demo-completed-v1';
const EARTH_RADIUS_METRES = 6371000;

function toRadians(value) { return value * Math.PI / 180; }
function toDegrees(value) { return value * 180 / Math.PI; }

export function distanceMetres(aLat, aLong, bLat, bLong) {
    const lat1 = toRadians(Number(aLat));
    const lat2 = toRadians(Number(bLat));
    const deltaLat = lat2 - lat1;
    const deltaLong = toRadians(Number(bLong) - Number(aLong));
    const h = Math.sin(deltaLat / 2) ** 2
        + Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLong / 2) ** 2;
    return EARTH_RADIUS_METRES * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

export function pointAtDistance(lat, long, distance = DEMO_DISTANCE_METRES, bearingRadians = Math.random() * Math.PI * 2) {
    const angularDistance = distance / EARTH_RADIUS_METRES;
    const lat1 = toRadians(Number(lat));
    const long1 = toRadians(Number(long));
    const lat2 = Math.asin(
        Math.sin(lat1) * Math.cos(angularDistance)
        + Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearingRadians),
    );
    const long2 = long1 + Math.atan2(
        Math.sin(bearingRadians) * Math.sin(angularDistance) * Math.cos(lat1),
        Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2),
    );
    const normalizedLong = ((((toDegrees(long2) + 180) % 360) + 360) % 360) - 180;
    return { lat: toDegrees(lat2), long: normalizedLong };
}

export function makeDemoSpot({ userId, lat, long, bearingRadians, now = Date.now() } = {}) {
    const centre = pointAtDistance(lat, long, DEMO_DISTANCE_METRES, bearingRadians);
    return {
        id: DEMO_SPOT_ID,
        owner_user_id: Number(userId),
        title: 'Demo Hunt!',
        description: 'This is a practice spot. Move into its highlighted area and tap "Claim" to complete it.',
        lat: centre.lat,
        long: centre.long,
        radius: DEMO_RADIUS_METRES,
        total_value: 0,
        max_total_claims: 1,
        max_claims_per_user: 1,
        claim_duration: 0,
        use_password: false,
        is_prizedraw: false,
        prize_count: 1,
        status_label: 'active',
        claim_count: 0,
        success_claim_count: 0,
        pending_claim_count: 0,
        failed_claim_count: 0,
        created_by: null,
        creator_display_name: 'NimHunt',
        city: 'Demo area',
        country: null,
        starts_at: Math.floor(now / 1000) - 60,
        demo: true,
    };
}

function finite(value) {
    if (value === null || value === undefined || (typeof value === 'string' && value.trim() === '')) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

export function spotInSearchViewport(spot, urlLike, origin = 'http://localhost') {
    const url = new URL(urlLike, origin);
    if (url.pathname !== '/api/spots/search') return false;
    const minLat = finite(url.searchParams.get('min_lat'));
    const maxLat = finite(url.searchParams.get('max_lat'));
    const minLong = finite(url.searchParams.get('min_long'));
    const maxLong = finite(url.searchParams.get('max_long'));
    if ([minLat, maxLat, minLong, maxLong].some((value) => value === null)) return false;
    return Number(spot.lat) >= minLat && Number(spot.lat) <= maxLat
        && Number(spot.long) >= minLong && Number(spot.long) <= maxLong;
}

function readJson(storage, key) {
    try {
        const raw = storage?.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (_err) {
        return null;
    }
}

function writeJson(storage, key, value) {
    try { storage?.setItem(key, JSON.stringify(value)); } catch (_err) {}
}

function requestUrl(input, origin) {
    const raw = typeof input === 'string' || input instanceof URL ? input : input?.url;
    return raw ? new URL(raw, origin) : null;
}

function requestBodyJson(options) {
    try { return options?.body ? JSON.parse(options.body) : {}; } catch (_err) { return {}; }
}

function makeJsonResponse(response, payload, windowObj) {
    return new windowObj.Response(JSON.stringify(payload), {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
    });
}

function scrollMapIntoView(documentObj) {
    documentObj.querySelector('.map-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resolvedDemoColour(runtime) {
    if (runtime.demoColour) return runtime.demoColour;
    const probe = runtime.document.createElement('span');
    probe.className = 'nq-purple-bg';
    probe.style.position = 'fixed';
    probe.style.opacity = '0';
    runtime.document.body.append(probe);
    const colour = runtime.window.getComputedStyle?.(probe)?.backgroundColor;
    probe.remove();
    runtime.demoColour = colour && colour !== 'rgba(0, 0, 0, 0)' ? colour : DEMO_COLOUR;
    runtime.document.documentElement.style.setProperty('--nimhunt-demo-colour', runtime.demoColour);
    return runtime.demoColour;
}

function styleDemoLayers(runtime) {
    const spot = runtime.demoSpot;
    const map = runtime.map;
    if (!spot || !map?.eachLayer) return;
    map.eachLayer((layer) => {
        const latLng = layer?.getLatLng?.();
        if (!latLng || !layer?.setStyle) return;
        if (distanceMetres(latLng.lat, latLng.lng, spot.lat, spot.long) > 2) return;
        const colour = resolvedDemoColour(runtime);
        layer.setStyle({ color: colour, fillColor: colour });
    });
    runtime.document.querySelector(`[data-spot-id="${DEMO_SPOT_ID}"]`)?.classList.add('is-demo-spot');
}

function showCreatedToast(runtime) {
    let toast = runtime.document.getElementById('demo-spot-created-toast');
    if (!toast) {
        toast = runtime.document.createElement('section');
        toast.id = 'demo-spot-created-toast';
        toast.className = 'notice-card demo-spot-toast';
        toast.setAttribute('role', 'status');
        runtime.document.body.append(toast);
    }
    const heading = runtime.document.createElement('strong');
    heading.textContent = 'Demo Spot created!';
    const body = runtime.document.createElement('span');
    body.textContent = "We've placed a practice spot nearby. Head into the purple area and claim it just like a real NimHunt spot.";
    toast.replaceChildren(heading, body);
    toast.hidden = false;
    runtime.window.setTimeout(() => { toast.hidden = true; }, 6500);
}

function renderEmptyState(runtime) {
    const empty = runtime.document.getElementById('empty-spots');
    const list = runtime.document.getElementById('spot-list');
    if (!empty || !list || empty.hidden || !list.hidden || runtime.lastRealSpotCount !== 0) return;

    const wrapper = runtime.document.createElement('span');
    wrapper.className = 'demo-empty-copy';
    const first = runtime.document.createElement('span');
    first.textContent = 'There are no spots in your area.';
    wrapper.append(first);

    const canDemo = runtime.walletUserId !== null
        && runtime.userLocation
        && !runtime.demoSpot
        && !runtime.completed;
    if (canDemo) {
        const line = runtime.document.createElement('span');
        line.append(runtime.document.createTextNode('Would you like to '));
        const link = runtime.document.createElement('a');
        link.href = '#spot-map';
        link.className = 'welcome-link';
        link.textContent = 'try a Demo Spot?';
        link.addEventListener('click', (event) => {
            event.preventDefault();
            createDemo(runtime);
        });
        line.append(link);
        wrapper.append(line);
    }

    const globalLine = runtime.document.createElement('span');
    globalLine.append(runtime.document.createTextNode(canDemo ? 'Or would you like to ' : 'Would you like to '));
    const globalLink = runtime.document.createElement('a');
    globalLink.href = '#spot-map';
    globalLink.className = 'welcome-link';
    globalLink.textContent = 'check out global spots?';
    globalLink.addEventListener('click', (event) => {
        event.preventDefault();
        showGlobalSpots(runtime);
    });
    globalLine.append(globalLink);
    wrapper.append(globalLine);

    empty.classList.add('demo-empty-state');
    empty.replaceChildren(wrapper);
}

function createDemo(runtime) {
    if (!runtime.userLocation || runtime.walletUserId === null || runtime.demoSpot || runtime.completed) return;
    runtime.demoSpot = makeDemoSpot({
        userId: runtime.walletUserId,
        lat: runtime.userLocation.lat,
        long: runtime.userLocation.long,
    });
    writeJson(runtime.storage, DEMO_STORAGE_KEY, runtime.demoSpot);
    runtime.currentLocation = { ...runtime.userLocation };
    startDemoLocationWatch(runtime);
    scrollMapIntoView(runtime.document);
    if (runtime.map?.fitBounds) {
        runtime.map.fitBounds([
            [runtime.userLocation.lat, runtime.userLocation.long],
            [runtime.demoSpot.lat, runtime.demoSpot.long],
        ], { padding: [42, 42], maxZoom: 16, animate: true });
    } else {
        runtime.refreshRequested = true;
    }
    showCreatedToast(runtime);
    runtime.window.setTimeout(() => styleDemoLayers(runtime), 300);
}

function showGlobalSpots(runtime) {
    scrollMapIntoView(runtime.document);
    if (runtime.map?.setView) {
        runtime.map.setView([20, 0], 0, { animate: true });
    } else {
        runtime.refreshRequested = 'global';
    }
}

function moveUserMarker(runtime, location) {
    const map = runtime.map;
    if (!map?.eachLayer) return;
    map.eachLayer((layer) => {
        if (!layer?.setLatLng || layer?.options?.color !== '#1f2348' || Number(layer?.options?.radius) !== 12) return;
        layer.setLatLng([location.lat, location.long]);
    });
}

function updateLocation(runtime, location) {
    const previous = runtime.currentLocation;
    runtime.currentLocation = location;
    moveUserMarker(runtime, location);
    if (!runtime.demoSpot) return;
    const before = previous
        ? distanceMetres(previous.lat, previous.long, runtime.demoSpot.lat, runtime.demoSpot.long) <= DEMO_RADIUS_METRES
        : null;
    const after = distanceMetres(location.lat, location.long, runtime.demoSpot.lat, runtime.demoSpot.long) <= DEMO_RADIUS_METRES;
    if (before !== after) runtime.map?.fire?.('moveend');
}

function startDemoLocationWatch(runtime) {
    if (!runtime.demoSpot || runtime.watchId !== null || !runtime.window.navigator?.geolocation?.watchPosition) return;
    runtime.watchId = runtime.window.navigator.geolocation.watchPosition(
        (position) => updateLocation(runtime, {
            lat: Number(position.coords.latitude),
            long: Number(position.coords.longitude),
            accuracy: Number(position.coords.accuracy || 0),
        }),
        () => {},
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
    );
}

function completeDemo(runtime) {
    runtime.completed = true;
    try {
        runtime.storage?.removeItem(DEMO_STORAGE_KEY);
        runtime.storage?.setItem(DEMO_COMPLETED_KEY, '1');
    } catch (_err) {}
    if (runtime.watchId !== null) {
        runtime.window.navigator?.geolocation?.clearWatch?.(runtime.watchId);
        runtime.watchId = null;
    }
    runtime.window.location.href = '/static/demo_claim_success.html';
}

function interceptDemoClaim(runtime, event) {
    if (!runtime.demoSpot) return;
    const item = event.target?.closest?.(`[data-spot-id="${DEMO_SPOT_ID}"]`);
    const button = event.target?.closest?.('.spot-claim-button');
    if (!item || !button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    event.stopPropagation();

    const location = runtime.currentLocation || runtime.userLocation;
    const inRange = location && distanceMetres(
        location.lat,
        location.long,
        runtime.demoSpot.lat,
        runtime.demoSpot.long,
    ) <= DEMO_RADIUS_METRES;
    if (!inRange) return;
    completeDemo(runtime);
}

function captureMap(runtime) {
    const L = runtime.window.L;
    if (!L?.map || L.__nimHuntDemoMapCapture) return;
    const original = L.map;
    L.map = function nimHuntDemoMapCapture(target, options) {
        const map = original.call(this, target, options);
        const id = typeof target === 'string' ? target : target?.id;
        if (id === 'spot-map') {
            runtime.map = map;
            if (runtime.searchWhenMapReady) {
                runtime.searchWhenMapReady = false;
                runtime.window.setTimeout(() => map.fire?.('moveend'), 0);
            }
            if (runtime.refreshRequested === 'global') showGlobalSpots(runtime);
            else if (runtime.refreshRequested && runtime.demoSpot && runtime.userLocation) {
                runtime.refreshRequested = false;
                map.fitBounds([
                    [runtime.userLocation.lat, runtime.userLocation.long],
                    [runtime.demoSpot.lat, runtime.demoSpot.long],
                ], { padding: [42, 42], maxZoom: 16, animate: false });
            }
        }
        return map;
    };
    L.__nimHuntDemoMapCapture = true;
}

function installFetch(runtime) {
    const originalFetch = runtime.window.fetch.bind(runtime.window);
    runtime.window.fetch = async (input, options = {}) => {
        const url = requestUrl(input, runtime.window.location.origin);
        const response = await originalFetch(input, options);
        if (!url) return response;

        if (url.pathname === '/api/home/session') {
            response.clone().json().then((data) => {
                runtime.walletUserId = data?.user?.id === undefined || data?.user?.id === null
                    ? null
                    : Number(data.user.id);
                if (runtime.demoSpot && runtime.demoSpot.owner_user_id !== runtime.walletUserId) {
                    runtime.demoSpot = null;
                } else if (runtime.demoSpot && runtime.demoSpot.owner_user_id === runtime.walletUserId) {
                    if (runtime.map?.fire) runtime.map.fire('moveend');
                    else runtime.searchWhenMapReady = true;
                }
                renderEmptyState(runtime);
            }).catch(() => {});
            return response;
        }

        if (url.pathname !== '/api/spots/search' || !response.ok) return response;
        const body = requestBodyJson(options);
        const queryLat = finite(url.searchParams.get('distance_lat')) ?? finite(body.lat);
        const queryLong = finite(url.searchParams.get('distance_long')) ?? finite(body.long);
        if (queryLat !== null && queryLong !== null) {
            runtime.userLocation = { lat: queryLat, long: queryLong };
            if (!runtime.currentLocation) runtime.currentLocation = { ...runtime.userLocation };
        }

        const data = await response.clone().json().catch(() => null);
        if (!data || !Array.isArray(data.spots)) return response;
        runtime.lastRealSpotCount = data.spots.filter((spot) => Number(spot?.id) !== DEMO_SPOT_ID).length;
        runtime.window.queueMicrotask?.(() => renderEmptyState(runtime));

        if (!runtime.demoSpot
            || runtime.demoSpot.owner_user_id !== runtime.walletUserId
            || !spotInSearchViewport(runtime.demoSpot, url, runtime.window.location.origin)) {
            return response;
        }

        const source = runtime.currentLocation || runtime.userLocation;
        const distance = source
            ? Math.round(distanceMetres(source.lat, source.long, runtime.demoSpot.lat, runtime.demoSpot.long))
            : null;
        const demo = { ...runtime.demoSpot, distance_m: distance };
        return makeJsonResponse(response, {
            ...data,
            spots: [demo, ...data.spots.filter((spot) => Number(spot?.id) !== DEMO_SPOT_ID)],
        }, runtime.window);
    };
}

export function installFindSpotsDemo({ windowObj = window, documentObj = document } = {}) {
    if (!documentObj.getElementById('spot-map') || windowObj.__nimHuntDemoRuntime) {
        return windowObj.__nimHuntDemoRuntime || null;
    }
    const storage = windowObj.sessionStorage;
    const stored = readJson(storage, DEMO_STORAGE_KEY);
    const runtime = {
        window: windowObj,
        document: documentObj,
        storage,
        demoSpot: stored,
        completed: storage?.getItem(DEMO_COMPLETED_KEY) === '1',
        walletUserId: null,
        userLocation: null,
        currentLocation: null,
        map: null,
        watchId: null,
        refreshRequested: false,
        searchWhenMapReady: false,
        lastRealSpotCount: null,
        demoColour: null,
    };
    windowObj.__nimHuntDemoRuntime = runtime;

    captureMap(runtime);
    installFetch(runtime);
    documentObj.addEventListener('click', (event) => interceptDemoClaim(runtime, event), true);
    documentObj.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') interceptDemoClaim(runtime, event);
    }, true);

    const observer = new MutationObserver(() => {
        renderEmptyState(runtime);
        styleDemoLayers(runtime);
    });
    const empty = documentObj.getElementById('empty-spots');
    const list = documentObj.getElementById('spot-list');
    if (empty) observer.observe(empty, { childList: true, attributes: true, attributeFilter: ['hidden'] });
    if (list) observer.observe(list, { childList: true, attributes: true, attributeFilter: ['hidden'] });

    if (runtime.demoSpot && !runtime.completed) startDemoLocationWatch(runtime);
    return runtime;
}
