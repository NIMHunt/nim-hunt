const SPOT_SEARCH_PATH = '/api/spots/search';
const MIN_LONGITUDE = -180;
const MAX_LONGITUDE = 180;
const WORLD_LONGITUDE_SPAN = 360;

function finiteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
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

export function wrappedSpotSearchUrls(input, origin = 'http://localhost') {
    const rawUrl = typeof input === 'string' || input instanceof URL
        ? input
        : input?.url;
    if (!rawUrl) return [];

    const url = new URL(rawUrl, origin);
    if (url.pathname !== SPOT_SEARCH_PATH) return [];

    const ranges = longitudeSearchRanges(
        url.searchParams.get('min_long'),
        url.searchParams.get('max_long'),
    );
    if (ranges.length === 0) return [url];

    return ranges.map(({ minLong, maxLong }) => {
        const nextUrl = new URL(url);
        nextUrl.searchParams.set('min_long', String(minLong));
        nextUrl.searchParams.set('max_long', String(maxLong));
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

    const fallbackResponse = (error) => {
        onSuppressedFailure?.(error);
        return jsonResponse(lastSuccessfulPayload, ResponseCtor);
    };

    const fetchSearch = async (input, options = {}) => {
        const urls = wrappedSpotSearchUrls(input, origin);
        if (urls.length === 0) return fetchImpl(input, options);

        try {
            const responses = await Promise.all(
                urls.map((url) => fetchImpl(url, options)),
            );
            const failedResponse = responses.find((response) => !response.ok);
            if (failedResponse) {
                if (failureAlreadyReported) return fallbackResponse(failedResponse);
                failureAlreadyReported = true;
                return failedResponse;
            }

            const payloads = await Promise.all(
                responses.map((response) => response.clone().json()),
            );
            lastSuccessfulPayload = mergeSpotSearchPayloads(payloads);
            failureAlreadyReported = false;

            if (responses.length === 1) return responses[0];
            return jsonResponse(lastSuccessfulPayload, ResponseCtor);
        } catch (error) {
            if (error?.name === 'AbortError') throw error;
            if (!failureAlreadyReported) {
                failureAlreadyReported = true;
                throw error;
            }
            return fallbackResponse(error);
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
