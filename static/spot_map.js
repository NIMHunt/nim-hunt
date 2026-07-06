const DEFAULT_MAP_CENTRE = [54.5, -3.4];
const DEFAULT_MAP_ZOOM = 5;

function validSpotCoordinate(spot) {
    const lat = Number(spot.lat);
    const long = Number(spot.long);
    return Number.isFinite(lat) && Number.isFinite(long);
}

function spotLatLng(spot) {
    return [Number(spot.lat), Number(spot.long)];
}

function clearLayer(layer) {
    if (layer?.clearLayers) layer.clearLayers();
}

function spotHref(spot) {
    return typeof spot?.href === 'string' && spot.href.trim() ? spot.href : null;
}

function defaultPopupContent(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    title.textContent = spot.title || 'NimHunt Spot';
    return title;
}

function makePopupContent({ spot, popupBuilder, onSpotClick }) {
    const content = typeof popupBuilder === 'function'
        ? popupBuilder(spot)
        : defaultPopupContent(spot);

    const wrap = document.createElement('div');
    wrap.className = 'nh-spot-popup-content';

    if (content instanceof Node) {
        wrap.append(content);
    } else {
        wrap.textContent = String(content || spot.title || 'NimHunt Spot');
    }

    const href = spotHref(spot);
    const canOpenSpot = typeof onSpotClick === 'function' || href;
    if (canOpenSpot) {
        wrap.classList.add('is-clickable');
        wrap.setAttribute('role', 'link');
        wrap.tabIndex = 0;

        const openSpot = () => {
            if (typeof onSpotClick === 'function') {
                onSpotClick(spot);
                return;
            }

            window.location.href = href;
        };

        wrap.addEventListener('click', openSpot);
        wrap.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            openSpot();
        });
    }

    return wrap;
}

function popupMaxWidthForMap(map) {
    const mapWidth = Number(map?.getSize?.().x || 0);
    if (!Number.isFinite(mapWidth) || mapWidth <= 0) return 300;
    return Math.max(120, Math.min(420, Math.floor(mapWidth - 56)));
}

function bindSpotPopup({ map, layer, spot, popupBuilder, onSpotClick }) {
    layer.bindPopup(
        makePopupContent({ spot, popupBuilder, onSpotClick }),
        {
            closeButton: false,
            className: 'nh-spot-popup',
            maxWidth: popupMaxWidthForMap(map),
            autoPan: true,
            keepInView: true,
        }
    );
}

export function createReusableSpotMap({
    mapEl,
    tileUrl,
    tileAttribution,
    spots = [],
    colourForSpot,
    popupBuilder = null,
    onSpotClick = null,
}) {
    if (!mapEl || !window.L) return null;

    const map = window.L.map(mapEl, {
        zoomControl: true,
        attributionControl: true,
    });

    window.L.tileLayer(tileUrl, {
        attribution: tileAttribution,
        maxZoom: 19,
    }).addTo(map);

    const spotLayer = window.L.layerGroup().addTo(map);

    const api = {
        map,
        spotLayer,
        setSpots(nextSpots = []) {
            renderSpotsOnMap({
                map,
                spotLayer,
                spots: nextSpots,
                colourForSpot,
                popupBuilder,
                onSpotClick,
            });
        },
    };

    api.setSpots(spots);
    return api;
}

export function renderSpotsOnMap({
    map,
    spotLayer,
    spots = [],
    colourForSpot,
    popupBuilder = null,
    onSpotClick = null,
}) {
    if (!map || !spotLayer || !window.L) return;

    clearLayer(spotLayer);
    const visibleSpots = spots.filter(validSpotCoordinate);

    if (visibleSpots.length === 0) {
        map.setView(DEFAULT_MAP_CENTRE, DEFAULT_MAP_ZOOM);
        return;
    }

    const bounds = [];

    for (const spot of visibleSpots) {
        const latLng = spotLatLng(spot);
        const colour = typeof colourForSpot === 'function' ? colourForSpot(spot) : '#21bca5';
        bounds.push(latLng);

        const radius = Math.max(1, Number(spot.radius || 25));
        const circle = window.L.circle(latLng, {
            radius,
            color: colour,
            fillColor: colour,
            fillOpacity: 0.14,
            opacity: 0.82,
            weight: 2,
        });

        const marker = window.L.circleMarker(latLng, {
            radius: 7,
            color: '#ffffff',
            fillColor: colour,
            fillOpacity: 1,
            opacity: 1,
            weight: 2,
        });

        bindSpotPopup({ map, layer: marker, spot, popupBuilder, onSpotClick });
        bindSpotPopup({ map, layer: circle, spot, popupBuilder, onSpotClick });

        circle.addTo(spotLayer);
        marker.addTo(spotLayer);
    }

    if (bounds.length === 1) {
        map.setView(bounds[0], 13);
        return;
    }

    map.fitBounds(window.L.latLngBounds(bounds), {
        padding: [28, 28],
        maxZoom: 13,
    });
}
