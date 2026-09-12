// Shared helpers for converting map/display coordinates into canonical geographic values.
// Leaflet may report a repeated-world longitude outside -180..180; API/database values must not.

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

export function normaliseLongitude(value) {
    const longitude = finiteNumber(value);
    if (longitude === null) return null;

    const wrapped = ((((longitude + 180) % 360) + 360) % 360) - 180;
    return Object.is(wrapped, -0) ? 0 : wrapped;
}

export function validCanonicalCoordinates(lat, long) {
    const latitude = finiteNumber(lat);
    const longitude = finiteNumber(long);
    return latitude !== null
        && latitude >= -90
        && latitude <= 90
        && longitude !== null
        && longitude >= -180
        && longitude <= 180;
}
