const GEOLOCATION_ERROR_KINDS = Object.freeze({
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
