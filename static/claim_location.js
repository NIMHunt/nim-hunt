const FRESH_SAMPLE_MAX_AGE_MS = 15000;
const COLLECTION_BUDGET_MS = 12000;

function median(values) {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
}

export function combineFreshClaimLocations(samples, { now = Date.now(), maxAgeMs = FRESH_SAMPLE_MAX_AGE_MS } = {}) {
    const valid = (samples || []).map((sample) => ({
        lat: Number(sample?.lat),
        long: Number(sample?.long),
        accuracy: Number(sample?.accuracy),
        timestamp: Number(sample?.timestamp),
    })).filter((sample) => (
        Number.isFinite(sample.lat) && sample.lat >= -90 && sample.lat <= 90
        && Number.isFinite(sample.long) && sample.long >= -180 && sample.long <= 180
        && Number.isFinite(sample.accuracy) && sample.accuracy >= 0
        && Number.isFinite(sample.timestamp) && sample.timestamp <= now
        && now - sample.timestamp <= maxAgeMs
    ));
    if (valid.length < 2) throw new Error('Could not collect enough fresh location readings. Move near a window and retry.');
    return {
        lat: median(valid.map((item) => item.lat)),
        long: median(valid.map((item) => item.long)),
        accuracy: median(valid.map((item) => item.accuracy)),
        timestamps: valid.map((item) => item.timestamp).sort((a, b) => a - b),
    };
}

function onePosition(geolocation, options) {
    return new Promise((resolve, reject) => geolocation.getCurrentPosition(resolve, reject, options));
}

function withinBudget(promise, milliseconds) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Location collection timed out.')), milliseconds);
        promise.then(
            (value) => { clearTimeout(timer); resolve(value); },
            (error) => { clearTimeout(timer); reject(error); },
        );
    });
}

export async function collectFreshClaimLocation({
    geolocation = globalThis.navigator?.geolocation,
    count = 3,
    budgetMs = COLLECTION_BUDGET_MS,
    now = () => Date.now(),
} = {}) {
    if (!geolocation?.getCurrentPosition) throw new Error('Location permission is required to claim.');
    const samples = [];
    let lastError = null;
    const deadline = now() + budgetMs;
    for (let index = 0; index < count; index += 1) {
        const remaining = deadline - now();
        if (remaining <= 0) break;
        try {
            const timeout = Math.min(6000, remaining);
            const position = await withinBudget(onePosition(geolocation, {
                enableHighAccuracy: true, maximumAge: 0, timeout,
            }), timeout);
            samples.push({
                lat: position?.coords?.latitude, long: position?.coords?.longitude,
                accuracy: position?.coords?.accuracy, timestamp: position?.timestamp,
            });
        } catch (error) {
            lastError = error;
            if (Number(error?.code) === 1) throw new Error('Location permission was denied. Allow precise location and retry.');
        }
        if (index + 1 < count && deadline - now() > 250) {
            await new Promise((resolve) => setTimeout(resolve, 250));
        }
    }
    try {
        return combineFreshClaimLocations(samples);
    } catch (error) {
        if (samples.length < 2 && lastError) throw new Error('Fresh GPS readings are unavailable. Move near a window and retry.');
        throw error;
    }
}
