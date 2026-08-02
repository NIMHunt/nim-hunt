import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../static/spot_map.js', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const { createReusableSpotMap } = await import(moduleUrl);

function makeLeaflet() {
    const map = {
        createOptions: null,
        fitBoundsCalls: [],
        setViewCalls: [],
        fitBounds(bounds, options) {
            this.fitBoundsCalls.push({ bounds, options });
        },
        setView(centre, zoom) {
            this.setViewCalls.push({ centre, zoom });
        },
        getSize() {
            return { x: 320 };
        },
    };

    const makeLayer = () => ({
        addTo() {
            return this;
        },
        bindPopup() {},
        on() {},
        setStyle() {},
        bringToFront() {},
    });

    const spotLayer = {
        clearCount: 0,
        addTo() {
            return this;
        },
        clearLayers() {
            this.clearCount += 1;
        },
    };

    return {
        map,
        spotLayer,
        L: {
            map: (_mapEl, options) => {
                map.createOptions = options;
                return map;
            },
            tileLayer: () => ({ addTo() {} }),
            layerGroup: () => spotLayer,
            circle: makeLayer,
            circleMarker: makeLayer,
            latLngBounds: (bounds) => bounds,
        },
    };
}

function createMap(spots, options = {}) {
    const leaflet = makeLeaflet();
    globalThis.window = { L: leaflet.L };
    const api = createReusableSpotMap({
        mapEl: {},
        tileUrl: 'https://example.test/{z}/{x}/{y}.png',
        tileAttribution: 'Test',
        spots,
        onSpotCentreClick: () => {},
        radiusInteractive: false,
        ...options,
    });
    return { api, ...leaflet };
}

test('reusable maps fit once, then preserve the user-controlled view on refresh', () => {
    const spots = [
        { id: 1, lat: 55.84, long: -5.05, radius: 100 },
        { id: 2, lat: 55.95, long: -4.90, radius: 100 },
    ];
    const { api, map, spotLayer } = createMap(spots);

    assert.equal(map.fitBoundsCalls.length, 1);
    assert.equal(map.setViewCalls.length, 0);

    api.setSpots(spots.map((spot) => ({ ...spot, title: 'Updated' })));

    assert.equal(spotLayer.clearCount, 2, 'markers should still be refreshed');
    assert.equal(map.fitBoundsCalls.length, 1, 'refresh must not recenter the map');
    assert.equal(map.setViewCalls.length, 0, 'refresh must not reset the map view');
});

test('unlocated drafts are excluded instead of being treated as latitude 0 longitude 0', () => {
    const locatedSpot = { id: 2, lat: 55.84, long: -5.05, radius: 100 };
    const { map } = createMap([
        { id: 1, lat: null, long: null, radius: 25 },
        locatedSpot,
    ]);

    assert.equal(map.fitBoundsCalls.length, 0);
    assert.deepEqual(map.setViewCalls, [{ centre: [locatedSpot.lat, locatedSpot.long], zoom: 13 }]);
});

test('the exact Null Island placeholder is excluded from overview bounds', () => {
    const locatedSpot = { id: 2, lat: 55.84, long: -5.05, radius: 100 };
    const { map } = createMap([
        { id: 1, lat: 0, long: 0, radius: 25 },
        locatedSpot,
    ]);

    assert.equal(map.fitBoundsCalls.length, 0);
    assert.deepEqual(map.setViewCalls, [{ centre: [locatedSpot.lat, locatedSpot.long], zoom: 13 }]);
});

test('a reusable map can override the global zoom-out floor', () => {
    const { map } = createMap([
        { id: 1, lat: 55.84, long: -5.05, radius: 100 },
        { id: 2, lat: -33.87, long: 151.21, radius: 100 },
    ], { minZoom: 0 });

    assert.equal(map.createOptions.minZoom, 0);
    assert.equal(map.fitBoundsCalls.length, 1);
});
