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

export function wrapLongitude(value) {
    const longitude = finiteNumber(value);
    if (longitude === null) return null;

    const wrapped = ((((longitude + 180) % 360) + 360) % 360) - 180;
    return Object.is(wrapped, -0) ? 0 : wrapped;
}

export function longitudeNear(value, centreLongitude) {
    const longitude = wrapLongitude(value);
    const centre = finiteNumber(centreLongitude);
    if (longitude === null || centre === null) return value;

    const worldOffset = Math.round((centre - longitude) / WORLD_LONGITUDE_SPAN);
    return longitude + (worldOffset * WORLD_LONGITUDE_SPAN);
}

function pointParts(point) {
    if (Array.isArray(point)) {
        const lat = finiteNumber(point[0]);
        const long = finiteNumber(point[1]);
        return lat === null || long === null ? null : { lat, long };
    }

    if (point && typeof point === 'object') {
        const lat = finiteNumber(point.lat);
        const long = finiteNumber(point.lng ?? point.lon);
        return lat === null || long === null ? null : { lat, long };
    }

    return null;
}

export function compactWorldPoints(points) {
    if (!Array.isArray(points) || points.length === 0) return points;

    const parsed = points.map(pointParts);
    if (parsed.some((point) => point === null)) return points;

    const canonicalLongitudes = parsed.map((point) => wrapLongitude(point.long));
    const sorted = [...canonicalLongitudes].sort((left, right) => left - right);
    let largestGap = -1;
    let startLongitude = sorted[0];

    for (let index = 0; index < sorted.length; index += 1) {
        const current = sorted[index];
        const next = index === sorted.length - 1
            ? sorted[0] + WORLD_LONGITUDE_SPAN
            : sorted[index + 1];
        const gap = next - current;
        if (gap > largestGap) {
            largestGap = gap;
            startLongitude = index === sorted.length - 1 ? sorted[0] : sorted[index + 1];
        }
    }

    return parsed.map((point, index) => {
        let longitude = canonicalLongitudes[index];
        while (longitude < startLongitude) longitude += WORLD_LONGITUDE_SPAN;
        return [point.lat, longitude];
    });
}

function mapCentreLongitude(map) {
    try {
        return finiteNumber(map?.getCenter?.().lng);
    } catch (error) {
        return null;
    }
}

function projectLatLngForMap(latLng, map) {
    const point = pointParts(latLng);
    const centre = mapCentreLongitude(map);
    if (!point || centre === null) return latLng;
    return [point.lat, longitudeNear(point.long, centre)];
}

function projectExistingLayers(map) {
    const centre = mapCentreLongitude(map);
    if (centre === null || typeof map?.eachLayer !== 'function') return;

    map.eachLayer((layer) => {
        if (typeof layer?.getLatLng !== 'function' || typeof layer?.setLatLng !== 'function') return;
        const current = layer.getLatLng();
        const lat = finiteNumber(current?.lat);
        const long = finiteNumber(current?.lng);
        if (lat === null || long === null) return;

        const projected = longitudeNear(long, centre);
        if (Math.abs(projected - long) < 1e-9) return;
        layer.setLatLng([lat, projected]);
    });
}

export function installMySpotsWorldWrap(leaflet = globalThis.window?.L) {
    if (!leaflet || leaflet.__nimHuntMySpotsWorldWrap) {
        return leaflet?.__nimHuntMySpotsWorldWrap || null;
    }

    const originalMap = leaflet.map.bind(leaflet);
    const originalCircle = leaflet.circle.bind(leaflet);
    const originalCircleMarker = leaflet.circleMarker.bind(leaflet);
    const originalLatLngBounds = leaflet.latLngBounds.bind(leaflet);
    let activeMap = null;

    leaflet.map = (...args) => {
        const map = originalMap(...args);
        activeMap = map;
        map.on?.('moveend', () => projectExistingLayers(map));
        return map;
    };

    leaflet.circle = (latLng, options) => (
        originalCircle(projectLatLngForMap(latLng, activeMap), options)
    );
    leaflet.circleMarker = (latLng, options) => (
        originalCircleMarker(projectLatLngForMap(latLng, activeMap), options)
    );
    leaflet.latLngBounds = (first, second) => {
        if (second === undefined && Array.isArray(first)) {
            return originalLatLngBounds(compactWorldPoints(first));
        }
        return originalLatLngBounds(first, second);
    };

    const installed = {
        projectExistingLayers: () => projectExistingLayers(activeMap),
        restore() {
            leaflet.map = originalMap;
            leaflet.circle = originalCircle;
            leaflet.circleMarker = originalCircleMarker;
            leaflet.latLngBounds = originalLatLngBounds;
            delete leaflet.__nimHuntMySpotsWorldWrap;
        },
    };
    leaflet.__nimHuntMySpotsWorldWrap = installed;
    return installed;
}
