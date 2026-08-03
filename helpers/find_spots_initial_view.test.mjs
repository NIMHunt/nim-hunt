import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../static/find_spots_initial_view.js', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const {
    FIND_SPOTS_INITIAL_MAX_ZOOM,
    FIND_SPOTS_LONDON_FALLBACK,
    FIND_SPOTS_MIN_ZOOM,
    LEGACY_FIND_SPOTS_GLASGOW_FALLBACK,
    installFindSpotsInitialView,
} = await import(moduleUrl);

function makeLeaflet() {
    const calls = {
        mergeOptions: [],
        maps: [],
    };

    function MapClass() {}
    MapClass.prototype.options = { minZoom: 5 };
    MapClass.mergeOptions = (options) => {
        calls.mergeOptions.push({ ...options });
        Object.assign(MapClass.prototype.options, options);
    };

    const L = {
        Map: MapClass,
        map(target, options = {}) {
            const mapCalls = {
                target,
                options,
                setView: [],
                fitBounds: [],
                setZoom: [],
            };
            const map = {
                zoom: null,
                setView(centre, zoom, setViewOptions) {
                    mapCalls.setView.push({ centre, zoom, options: setViewOptions });
                    this.zoom = Number(zoom);
                    return this;
                },
                fitBounds(bounds, fitOptions = {}) {
                    mapCalls.fitBounds.push({ bounds, options: fitOptions });
                    this.zoom = 3;
                    return this;
                },
                setZoom(zoom, setZoomOptions) {
                    mapCalls.setZoom.push({ zoom, options: setZoomOptions });
                    this.zoom = Number(zoom);
                    return this;
                },
                getZoom() {
                    return this.zoom;
                },
            };
            calls.maps.push({ map, calls: mapCalls });
            return map;
        },
    };

    return { L, calls };
}

test('no-location Find Spots starts in London and keeps a genuinely wide initial fit', async () => {
    const { L, calls } = makeLeaflet();
    const installation = installFindSpotsInitialView(L);

    assert.equal(L.Map.prototype.options.minZoom, FIND_SPOTS_MIN_ZOOM);

    const map = L.map({ id: 'spot-map' }, {
        dragging: true,
        keyboard: true,
        boxZoom: true,
    });
    const mapCalls = calls.maps[0].calls;

    map.setView(LEGACY_FIND_SPOTS_GLASGOW_FALLBACK, FIND_SPOTS_INITIAL_MAX_ZOOM, {
        animate: false,
    });
    assert.deepEqual(mapCalls.setView[0].centre, FIND_SPOTS_LONDON_FALLBACK);

    map.fitBounds([[55.86, -4.25], [-33.87, 151.21]], {
        padding: [34, 34],
        animate: false,
    });
    assert.equal(mapCalls.fitBounds[0].options.maxZoom, FIND_SPOTS_INITIAL_MAX_ZOOM);
    assert.equal(map.getZoom(), 3);

    map.setZoom(FIND_SPOTS_INITIAL_MAX_ZOOM, { animate: false });
    assert.equal(mapCalls.setZoom.length, 0);
    assert.equal(map.getZoom(), 3);

    await Promise.resolve();
    map.setZoom(8, { animate: false });
    assert.equal(mapCalls.setZoom.length, 1);
    assert.equal(map.getZoom(), 8);

    installation.restore();
    assert.equal(L.Map.prototype.options.minZoom, 5);
});

test('a real located start is never mistaken for the old Glasgow fallback', () => {
    const { L, calls } = makeLeaflet();
    installFindSpotsInitialView(L);

    const map = L.map({ id: 'spot-map' }, {
        dragging: false,
        keyboard: false,
        boxZoom: false,
    });
    const mapCalls = calls.maps[0].calls;

    map.setView(LEGACY_FIND_SPOTS_GLASGOW_FALLBACK, 14, { animate: false });
    assert.deepEqual(mapCalls.setView[0].centre, LEGACY_FIND_SPOTS_GLASGOW_FALLBACK);

    map.fitBounds([[55.8, -4.3], [55.9, -4.2]], { padding: [34, 34] });
    assert.equal('maxZoom' in mapCalls.fitBounds[0].options, false);
});

test('the adapter ignores other maps and installation is idempotent', () => {
    const { L, calls } = makeLeaflet();
    const first = installFindSpotsInitialView(L);
    const second = installFindSpotsInitialView(L);
    assert.equal(second, first);

    const map = L.map({ id: 'another-map' }, {
        dragging: true,
        keyboard: true,
        boxZoom: true,
    });
    const mapCalls = calls.maps[0].calls;
    map.setView(LEGACY_FIND_SPOTS_GLASGOW_FALLBACK, FIND_SPOTS_INITIAL_MAX_ZOOM);

    assert.deepEqual(mapCalls.setView[0].centre, LEGACY_FIND_SPOTS_GLASGOW_FALLBACK);
    first.restore();
});
