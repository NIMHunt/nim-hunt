export const FIND_SPOTS_LONDON_FALLBACK = Object.freeze([51.5074, -0.1278]);
export const LEGACY_FIND_SPOTS_GLASGOW_FALLBACK = Object.freeze([55.8642, -4.2518]);
export const FIND_SPOTS_INITIAL_MAX_ZOOM = 11;
export const FIND_SPOTS_MIN_ZOOM = 0;

function numericLatLng(value) {
    if (Array.isArray(value) && value.length >= 2) {
        return [Number(value[0]), Number(value[1])];
    }
    if (value && typeof value === 'object') {
        return [Number(value.lat), Number(value.lng ?? value.lon)];
    }
    return [NaN, NaN];
}

function sameLatLng(left, right, tolerance = 1e-7) {
    const [leftLat, leftLong] = numericLatLng(left);
    const [rightLat, rightLong] = numericLatLng(right);
    return Number.isFinite(leftLat)
        && Number.isFinite(leftLong)
        && Number.isFinite(rightLat)
        && Number.isFinite(rightLong)
        && Math.abs(leftLat - rightLat) <= tolerance
        && Math.abs(leftLong - rightLong) <= tolerance;
}

function isFindSpotsMapTarget(target) {
    if (typeof target === 'string') return target === 'spot-map';
    return target?.id === 'spot-map';
}

function mapStartsWithoutLocation(options = {}) {
    return options.dragging !== false
        && options.keyboard !== false
        && options.boxZoom !== false;
}

function scheduleFlagReset(callback) {
    if (typeof globalThis.queueMicrotask === 'function') {
        globalThis.queueMicrotask(callback);
        return;
    }
    Promise.resolve().then(callback);
}

function adaptFindSpotsMap(map, { startsWithoutLocation }) {
    if (!map || map.__nimHuntInitialViewAdapted) return map;

    const originalSetView = map.setView?.bind(map);
    const originalFitBounds = map.fitBounds?.bind(map);
    const originalSetZoom = map.setZoom?.bind(map);
    if (!originalSetView || !originalFitBounds || !originalSetZoom) return map;

    let firstSetView = true;
    let firstInitialFit = startsWithoutLocation;
    let suppressLegacyZoomClamp = false;

    map.setView = (centre, zoom, options) => {
        let nextCentre = centre;
        if (
            firstSetView
            && startsWithoutLocation
            && Number(zoom) === FIND_SPOTS_INITIAL_MAX_ZOOM
            && sameLatLng(centre, LEGACY_FIND_SPOTS_GLASGOW_FALLBACK)
        ) {
            nextCentre = FIND_SPOTS_LONDON_FALLBACK;
        }
        firstSetView = false;
        return originalSetView(nextCentre, zoom, options);
    };

    map.fitBounds = (bounds, options = {}) => {
        if (!firstInitialFit) return originalFitBounds(bounds, options);
        firstInitialFit = false;

        const result = originalFitBounds(bounds, {
            ...options,
            maxZoom: FIND_SPOTS_INITIAL_MAX_ZOOM,
        });

        suppressLegacyZoomClamp = true;
        scheduleFlagReset(() => {
            suppressLegacyZoomClamp = false;
        });
        return result;
    };

    map.setZoom = (zoom, options) => {
        if (
            suppressLegacyZoomClamp
            && Number(zoom) === FIND_SPOTS_INITIAL_MAX_ZOOM
            && Number(map.getZoom?.()) < FIND_SPOTS_INITIAL_MAX_ZOOM
        ) {
            suppressLegacyZoomClamp = false;
            return map;
        }
        suppressLegacyZoomClamp = false;
        return originalSetZoom(zoom, options);
    };

    map.__nimHuntInitialViewAdapted = true;
    return map;
}

export function installFindSpotsInitialView(L) {
    if (!L?.map || !L?.Map?.mergeOptions) return null;
    if (L.__nimHuntFindSpotsInitialView) return L.__nimHuntFindSpotsInitialView;

    const originalMap = L.map;
    const previousMinZoom = Number(L.Map.prototype?.options?.minZoom);

    L.Map.mergeOptions({ minZoom: FIND_SPOTS_MIN_ZOOM });
    L.map = function nimHuntFindSpotsMap(target, options = {}) {
        const map = originalMap.call(this, target, options);
        if (!isFindSpotsMapTarget(target)) return map;
        return adaptFindSpotsMap(map, {
            startsWithoutLocation: mapStartsWithoutLocation(options),
        });
    };

    const installation = {
        restore() {
            L.map = originalMap;
            if (Number.isFinite(previousMinZoom)) {
                L.Map.mergeOptions({ minZoom: previousMinZoom });
            }
            delete L.__nimHuntFindSpotsInitialView;
        },
    };

    L.__nimHuntFindSpotsInitialView = installation;
    return installation;
}
