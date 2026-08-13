(() => {
    'use strict';

    const isDetailMapPage = Boolean(
        document.getElementById('spot-detail-list')
        || document.getElementById('claim-detail-list')
    );
    if (!isDetailMapPage) return;

    const L = window.L;
    if (!L?.map || !L?.Circle || !L?.latLngBounds) return;

    const originalMapFactory = L.map;
    const RADIUS_PADDING_RATIO = 0.18;

    function metreBoundsAround(lat, long, radiusMetres) {
        const radius = Math.max(1, Number(radiusMetres || 25));
        const latNum = Number(lat);
        const longNum = Number(long);
        const metresPerDegreeLat = 111320;
        const cosLat = Math.max(0.01, Math.abs(Math.cos(latNum * Math.PI / 180)));
        const latDelta = radius / metresPerDegreeLat;
        const longDelta = radius / (metresPerDegreeLat * cosLat);

        return L.latLngBounds(
            [latNum - latDelta, longNum - longDelta],
            [latNum + latDelta, longNum + longDelta]
        );
    }

    L.map = function createLockedDetailMap(mapEl, options = {}) {
        const map = originalMapFactory.call(L, mapEl, {
            ...options,
            zoomControl: true,
            dragging: false,
            touchZoom: false,
            scrollWheelZoom: false,
            doubleClickZoom: false,
            boxZoom: false,
            keyboard: false,
            tap: false,
        });

        let anchor = null;
        let radiusMetres = null;
        let recentring = false;

        const keepCentred = () => {
            if (!anchor || recentring) return;
            const current = map.getCenter?.();
            if (!current) return;

            const drifted = Math.abs(Number(current.lat) - anchor.lat) > 0.000001
                || Math.abs(Number(current.lng) - anchor.lng) > 0.000001;
            if (!drifted) return;

            recentring = true;
            map.setView([anchor.lat, anchor.lng], map.getZoom(), { animate: false });
            recentring = false;
        };

        const syncRadiusZoomFloor = () => {
            if (!anchor || !Number.isFinite(radiusMetres)) return;
            map.invalidateSize?.(false);

            const paddedBounds = metreBoundsAround(anchor.lat, anchor.lng, radiusMetres)
                .pad(RADIUS_PADDING_RATIO);
            const requestedMinZoom = Number(map.getBoundsZoom?.(paddedBounds, false));
            if (!Number.isFinite(requestedMinZoom)) return;

            const mapMaxZoom = Number(map.getMaxZoom?.());
            const minimumZoom = Number.isFinite(mapMaxZoom)
                ? Math.min(requestedMinZoom, mapMaxZoom)
                : requestedMinZoom;

            map.setMinZoom?.(minimumZoom);
            if (Number(map.getZoom?.()) < minimumZoom) {
                map.setView([anchor.lat, anchor.lng], minimumZoom, { animate: false });
            } else {
                keepCentred();
            }
        };

        map.on?.('moveend', keepCentred);
        map.on?.('layeradd', (event) => {
            const layer = event?.layer;
            if (anchor || !(layer instanceof L.Circle)) return;

            const centre = layer.getLatLng?.();
            const radius = Number(layer.getRadius?.());
            if (!centre || !Number.isFinite(Number(centre.lat)) || !Number.isFinite(Number(centre.lng))) return;
            if (!Number.isFinite(radius) || radius <= 0) return;

            anchor = { lat: Number(centre.lat), lng: Number(centre.lng) };
            radiusMetres = radius;

            syncRadiusZoomFloor();
            window.requestAnimationFrame?.(syncRadiusZoomFloor);
            window.setTimeout(syncRadiusZoomFloor, 0);
        });

        return map;
    };
})();
