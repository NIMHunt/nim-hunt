// Shared helpers for converting map/display coordinates into canonical geographic values.
// Leaflet may report a repeated-world longitude outside -180..180; API/database values must not.

export function normaliseLongitude(value) {
    const longitude = Number(value);
    if (!Number.isFinite(longitude)) return null;

    const wrapped = ((((longitude + 180) % 360) + 360) % 360) - 180;
    return Object.is(wrapped, -0) ? 0 : wrapped;
}

export function validCanonicalCoordinates(lat, long) {
    const latitude = Number(lat);
    const longitude = Number(long);
    return Number.isFinite(latitude)
        && latitude >= -90
        && latitude <= 90
        && Number.isFinite(longitude)
        && longitude >= -180
        && longitude <= 180;
}
