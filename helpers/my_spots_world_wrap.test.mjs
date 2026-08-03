import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../static/my_spots_world_wrap.js', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const {
    compactWorldPoints,
    installMySpotsWorldWrap,
    longitudeNear,
    wrapLongitude,
} = await import(moduleUrl);

test('longitudes are projected into the repeated world nearest the map centre', () => {
    assert.equal(wrapLongitude(545), -175);
    assert.equal(longitudeNear(-175, 180), 185);
    assert.equal(longitudeNear(-175, 540), 545);
    assert.equal(longitudeNear(175, 540), 535);
});

test('date-line neighbours form compact initial bounds instead of spanning the world', () => {
    assert.deepEqual(compactWorldPoints([
        [10, 175],
        [-10, -175],
    ]), [
        [10, 175],
        [-10, 185],
    ]);
});

test('ordinary overview points retain their natural longitudes', () => {
    assert.deepEqual(compactWorldPoints([
        [55.86, -4.25],
        [-33.87, 151.21],
    ]), [
        [55.86, -4.25],
        [-33.87, 151.21],
    ]);
});

function makeLeaflet() {
    const listeners = new Map();
    const layers = [];
    const map = {
        centre: { lat: 0, lng: 545 },
        on(name, handler) {
            listeners.set(name, handler);
        },
        getCenter() {
            return this.centre;
        },
        eachLayer(visitor) {
            for (const layer of layers) visitor(layer);
        },
    };
    const calls = {
        circle: [],
        circleMarker: [],
        bounds: [],
    };

    const makeLayer = (latLng) => {
        let current = { lat: Number(latLng[0]), lng: Number(latLng[1]) };
        const layer = {
            getLatLng() {
                return current;
            },
            setLatLng(next) {
                current = { lat: Number(next[0]), lng: Number(next[1]) };
            },
        };
        layers.push(layer);
        return layer;
    };

    const L = {
        map: () => map,
        circle: (latLng) => {
            calls.circle.push(latLng);
            return makeLayer(latLng);
        },
        circleMarker: (latLng) => {
            calls.circleMarker.push(latLng);
            return makeLayer(latLng);
        },
        latLngBounds: (points) => {
            calls.bounds.push(points);
            return points;
        },
    };

    return { L, map, calls, listeners, layers };
}

test('the My Spots adapter projects new and existing overlays without moving the map', () => {
    const { L, map, calls, listeners, layers } = makeLeaflet();
    const installed = installMySpotsWorldWrap(L);

    L.map({});
    L.circle([0, -175]);
    L.circleMarker([0, 175]);
    L.latLngBounds([[0, 175], [0, -175]]);

    assert.deepEqual(calls.circle, [[0, 545]]);
    assert.deepEqual(calls.circleMarker, [[0, 535]]);
    assert.deepEqual(calls.bounds, [[[0, 175], [0, 185]]]);

    map.centre = { lat: 0, lng: 180 };
    listeners.get('moveend')();
    assert.deepEqual(layers.map((layer) => layer.getLatLng().lng), [185, 175]);

    installed.restore();
    assert.equal(L.__nimHuntMySpotsWorldWrap, undefined);
});

test('installing the My Spots adapter twice is idempotent', () => {
    const { L } = makeLeaflet();
    const first = installMySpotsWorldWrap(L);
    const second = installMySpotsWorldWrap(L);

    assert.equal(second, first);
    first.restore();
});
