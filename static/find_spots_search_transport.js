const SPOT_SEARCH_PATH = '/api/spots/search';
const MIN_LONGITUDE = -180;
const MAX_LONGITUDE = 180;
const WORLD_LONGITUDE_SPAN = 360;

function finiteNumber(value) {
    if (
        value === null
        || value === undefined
        || (typeof value === 'string' && value.trim() === '')
    ) {
        return null;
    }

    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function requestUrl(input, origin) {
    const rawUrl = typeof input === 'string' || input instanceof URL
        ? input
        : input?.url;
    return rawUrl ? new URL(rawUrl, origin) : null;
}

export function wrapLongitude(value) {
    const longitude = finiteNumber(value);
    if (longitude === null) return null;

    const wrapped = ((((longitude + 180) % 360) + 360) % 360) - 180;
    return Object.is(wrapped, -0) ? 0 : wrapped;
}

export function longitudeSearchRanges(minLong, maxLong) {
    const rawWest = finiteNumber(minLong);
    const rawEast = finiteNumber(maxLong);
    if (rawWest === null || rawEast === null) return [];

    let width = rawEast - rawWest;
    while (width < 0) width += WORLD_LONGITUDE_SPAN;

    if (width >= WORLD_LONGITUDE_SPAN) {
        return [{ minLong: MIN_LONGITUDE, maxLong: MAX_LONGITUDE }];
    }

    const west = wrapLongitude(rawWest);
    if (west === null) return [];

    const unwrappedEast = west + width;
    if (unwrappedEast <= MAX_LONGITUDE) {
        return [{ minLong: west, maxLong: unwrappedEast }];
    }

    return [
        { minLong: west, maxLong: MAX_LONGITUDE },
        { minLong: MIN_LONGITUDE, maxLong: unwrappedEast - WORLD_LONGITUDE_SPAN },
    ];
}

export function spotSearchViewport(input, origin = 'http://localhost') {
    const url = requestUrl(input, origin);
    if (!url || url.pathname !== SPOT_SEARCH_PATH) return null;

    const west = finiteNumber(url.searchParams.get('min_long'));
    const rawEast = finiteNumber(url.searchParams.get('max_long'));
    if (west === null || rawEast === null) return null;

    let width = rawEast - west;
    while (width < 0) width += WORLD_LONGITUDE_SPAN;

    return {
        west,
        east: west + width,
        centre: west + (width / 2),
        width,
    };
}

export function longitudeForViewport(value, viewport) {
    const longitude = wrapLongitude(value);
    const centre = finiteNumber(viewport?.centre);
    if (longitude === null || centre === null) return value;

    const worldOffset = Math.round((centre - longitude) / WORLD_LONGITUDE_SPAN);
    return longitude + (worldOffset * WORLD_LONGITUDE_SPAN);
}

export function projectSpotSearchPayload(payload, viewport) {
    const safePayload = payload && typeof payload === 'object'
        ? payload
        : { ok: true, spots: [] };

    return {
        ...safePayload,
        spots: (Array.isArray(safePayload.spots) ? safePayload.spots : []).map((spot) => ({
            ...spot,
            long: longitudeForViewport(spot?.long, viewport),
        })),
    };
}

export function wrappedSpotSearchUrls(input, origin = 'http://localhost') {
    const url = requestUrl(input, origin);
    if (!url || url.pathname !== SPOT_SEARCH_PATH) return [];

    const ranges = longitudeSearchRanges(
        url.searchParams.get('min_long'),
        url.searchParams.get('max_long'),
    );
    if (ranges.length === 0) return [url];

    const distanceLongitude = url.searchParams.has('distance_long')
        ? wrapLongitude(url.searchParams.get('distance_long'))
        : null;

    return ranges.map(({ minLong, maxLong }) => {
        const nextUrl = new URL(url);
        nextUrl.searchParams.set('min_long', String(minLong));
        nextUrl.searchParams.set('max_long', String(maxLong));
        if (distanceLongitude !== null) {
            nextUrl.searchParams.set('distance_long', String(distanceLongitude));
        }
        return nextUrl;
    });
}

function spotSortValue(spot) {
    const distance = finiteNumber(spot?.distance_m);
    const status = String(spot?.status_label || '').toLowerCase() === 'active' ? 0 : 1;
    const startsAt = finiteNumber(spot?.starts_at) ?? 0;
    const id = finiteNumber(spot?.id) ?? Number.MAX_SAFE_INTEGER;
    return [distance ?? Number.POSITIVE_INFINITY, status, startsAt, id];
}

function compareSpots(left, right) {
    const leftValues = spotSortValue(left);
    const rightValues = spotSortValue(right);
    for (let index = 0; index < leftValues.length; index += 1) {
        if (leftValues[index] < rightValues[index]) return -1;
        if (leftValues[index] > rightValues[index]) return 1;
    }
    return 0;
}

export function mergeSpotSearchPayloads(payloads) {
    const safePayloads = Array.isArray(payloads)
        ? payloads.filter((payload) => payload && typeof payload === 'object')
        : [];
    const first = safePayloads[0] || { ok: true };
    const spotsById = new Map();
    let anonymousIndex = 0;

    for (const payload of safePayloads) {
        for (const spot of Array.isArray(payload.spots) ? payload.spots : []) {
            const id = finiteNumber(spot?.id);
            const key = id === null ? `anonymous-${anonymousIndex += 1}` : `spot-${id}`;
            if (!spotsById.has(key)) spotsById.set(key, spot);
        }
    }

    return {
        ...first,
        ok: true,
        spots: [...spotsById.values()].sort(compareSpots),
    };
}

function jsonResponse(payload, ResponseCtor = globalThis.Response) {
    return new ResponseCtor(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
    });
}

export function createFindSpotsSearchTransport({
    fetchImpl,
    origin = 'http://localhost',
    ResponseCtor = globalThis.Response,
    onSuppressedFailure = (error) => console.warn(
        'NimHunt kept the last visible Spots after another search refresh failed.',
        error,
    ),
} = {}) {
    if (typeof fetchImpl !== 'function') {
        throw new TypeError('A fetch implementation is required.');
    }

    let lastSuccessfulPayload = { ok: true, spots: [] };
    let failureAlreadyReported = false;

    const fallbackResponse = (error, viewport) => {
        onSuppressedFailure?.(error);
        return jsonResponse(
            projectSpotSearchPayload(lastSuccessfulPayload, viewport),
            ResponseCtor,
        );
    };

    const fetchSearch = async (input, options = {}) => {
        const urls = wrappedSpotSearchUrls(input, origin);
        if (urls.length === 0) return fetchImpl(input, options);
        const viewport = spotSearchViewport(input, origin);

        try {
            const responses = await Promise.all(
                urls.map((url) => fetchImpl(url, options)),
            );
            const failedResponse = responses.find((response) => !response.ok);
            if (failedResponse) {
                if (failureAlreadyReported) return fallbackResponse(failedResponse, viewport);
                failureAlreadyReported = true;
                return failedResponse;
            }

            const payloads = await Promise.all(
                responses.map((response) => response.clone().json()),
            );
            lastSuccessfulPayload = mergeSpotSearchPayloads(payloads);
            failureAlreadyReported = false;

            return jsonResponse(
                projectSpotSearchPayload(lastSuccessfulPayload, viewport),
                ResponseCtor,
            );
        } catch (error) {
            if (error?.name === 'AbortError') throw error;
            if (!failureAlreadyReported) {
                failureAlreadyReported = true;
                throw error;
            }
            return fallbackResponse(error, viewport);
        }
    };

    return {
        fetch: fetchSearch,
        state: () => ({
            failureAlreadyReported,
            lastSuccessfulPayload,
        }),
    };
}

export function installFindSpotsSearchTransport() {
    if (window.__nimHuntFindSpotsSearchTransport) {
        return window.__nimHuntFindSpotsSearchTransport;
    }

    const originalFetch = window.fetch.bind(window);
    const transport = createFindSpotsSearchTransport({
        fetchImpl: originalFetch,
        origin: window.location.origin,
        ResponseCtor: window.Response,
    });

    window.fetch = transport.fetch;
    const installed = {
        ...transport,
        restore() {
            window.fetch = originalFetch;
            delete window.__nimHuntFindSpotsSearchTransport;
        },
    };
    window.__nimHuntFindSpotsSearchTransport = installed;
    return installed;
}
