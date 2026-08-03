import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../static/find_spots_search_transport.js', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const {
    createFindSpotsSearchTransport,
    longitudeForViewport,
    longitudeSearchRanges,
    mergeSpotSearchPayloads,
    spotSearchViewport,
    wrappedSpotSearchUrls,
} = await import(moduleUrl);

function searchUrl(minLong, maxLong, distanceLong = null) {
    const distance = distanceLong === null ? '' : `&distance_long=${distanceLong}`;
    return `/api/spots/search?min_lat=-20&max_lat=20&min_long=${minLong}&max_long=${maxLong}&limit=150${distance}`;
}

function response(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
    });
}

test('normal longitude bounds remain one canonical search range', () => {
    assert.deepEqual(longitudeSearchRanges(-12, 8), [
        { minLong: -12, maxLong: 8 },
    ]);
});

test('date-line-crossing and repeated-world bounds split into two valid ranges', () => {
    const expected = [
        { minLong: 170, maxLong: 180 },
        { minLong: -180, maxLong: -170 },
    ];

    assert.deepEqual(longitudeSearchRanges(170, 190), expected);
    assert.deepEqual(longitudeSearchRanges(-190, -170), expected);
    assert.deepEqual(longitudeSearchRanges(530, 550), expected);
    assert.deepEqual(longitudeSearchRanges(179, -179), [
        { minLong: 179, maxLong: 180 },
        { minLong: -180, maxLong: -179 },
    ]);
});

test('a viewport spanning the whole world uses one full-world search', () => {
    assert.deepEqual(longitudeSearchRanges(-200, 200), [
        { minLong: -180, maxLong: 180 },
    ]);
});

test('wrapped search URLs preserve parameters and normalise test-location longitude', () => {
    const urls = wrappedSpotSearchUrls(searchUrl(170, 190, 545), 'https://nimhunt.example');

    assert.equal(urls.length, 2);
    assert.deepEqual(urls.map((url) => [
        Number(url.searchParams.get('min_long')),
        Number(url.searchParams.get('max_long')),
        Number(url.searchParams.get('distance_long')),
        url.searchParams.get('limit'),
    ]), [
        [170, 180, -175, '150'],
        [-180, -170, -175, '150'],
    ]);
});

test('canonical Spot longitudes are projected into the visible repeated world', () => {
    const firstCopy = spotSearchViewport(searchUrl(170, 190), 'https://nimhunt.example');
    const laterCopy = spotSearchViewport(searchUrl(530, 550), 'https://nimhunt.example');

    assert.equal(longitudeForViewport(-175, firstCopy), 185);
    assert.equal(longitudeForViewport(175, firstCopy), 175);
    assert.equal(longitudeForViewport(-175, laterCopy), 545);
    assert.equal(longitudeForViewport(175, laterCopy), 535);
});

test('split search responses are merged, deduplicated, sorted and projected', async () => {
    const calls = [];
    const fetchImpl = async (input) => {
        const url = new URL(input);
        calls.push(url);
        const minLong = Number(url.searchParams.get('min_long'));
        if (minLong >= 0) {
            return response({
                ok: true,
                spots: [
                    { id: 2, long: 175, status_label: 'upcoming', starts_at: 200 },
                    { id: 1, long: 176, status_label: 'active', starts_at: 300 },
                ],
            });
        }
        return response({
            ok: true,
            spots: [
                { id: 1, long: 176, status_label: 'active', starts_at: 300 },
                { id: 3, long: -175, status_label: 'active', starts_at: 100 },
            ],
        });
    };
    const transport = createFindSpotsSearchTransport({
        fetchImpl,
        origin: 'https://nimhunt.example',
    });

    const result = await transport.fetch(searchUrl(170, 190));
    const payload = await result.json();

    assert.equal(calls.length, 2);
    assert.deepEqual(payload.spots.map((spot) => spot.id), [3, 1, 2]);
    assert.deepEqual(payload.spots.map((spot) => spot.long), [185, 176, 175]);
});

test('repeated failures reuse the last successful result without repeating the blocking error', async () => {
    let mode = 'success';
    let suppressedCount = 0;
    const fetchImpl = async () => {
        if (mode === 'success') {
            return response({
                ok: true,
                spots: [{ id: 7, long: -175, status_label: 'active' }],
            });
        }
        if (mode === 'http-failure') return response({ detail: 'Temporary failure' }, 503);
        throw new Error('Network unavailable');
    };
    const transport = createFindSpotsSearchTransport({
        fetchImpl,
        origin: 'https://nimhunt.example',
        onSuppressedFailure: () => { suppressedCount += 1; },
    });

    const firstSuccess = await transport.fetch(searchUrl(170, 190));
    assert.equal((await firstSuccess.json()).spots[0].long, 185);

    mode = 'http-failure';
    const firstFailure = await transport.fetch(searchUrl(170, 190));
    assert.equal(firstFailure.status, 503, 'the page should show one visible warning');

    const repeatedFailure = await transport.fetch(searchUrl(530, 550));
    assert.equal(repeatedFailure.status, 200);
    const fallbackPayload = await repeatedFailure.json();
    assert.deepEqual(fallbackPayload.spots.map((spot) => spot.id), [7]);
    assert.deepEqual(fallbackPayload.spots.map((spot) => spot.long), [545]);
    assert.equal(suppressedCount, 1);

    mode = 'success';
    await transport.fetch(searchUrl(-10, 10));
    mode = 'network-failure';
    await assert.rejects(() => transport.fetch(searchUrl(-10, 10)), /Network unavailable/);
});

test('aborted searches are always propagated and never treated as refresh failures', async () => {
    const fetchImpl = async () => {
        const error = new Error('Aborted');
        error.name = 'AbortError';
        throw error;
    };
    const transport = createFindSpotsSearchTransport({
        fetchImpl,
        origin: 'https://nimhunt.example',
    });

    await assert.rejects(
        () => transport.fetch(searchUrl(-10, 10)),
        (error) => error.name === 'AbortError',
    );
    await assert.rejects(
        () => transport.fetch(searchUrl(-10, 10)),
        (error) => error.name === 'AbortError',
    );
});

test('non-search requests pass through untouched', async () => {
    const calls = [];
    const fetchImpl = async (input) => {
        calls.push(input);
        return response({ ok: true });
    };
    const transport = createFindSpotsSearchTransport({
        fetchImpl,
        origin: 'https://nimhunt.example',
    });

    await transport.fetch('/api/spots/initial?include_active=true');
    assert.deepEqual(calls, ['/api/spots/initial?include_active=true']);
});

test('payload merging is safe when a response contains no spots array', () => {
    assert.deepEqual(mergeSpotSearchPayloads([{ ok: true }, null]), {
        ok: true,
        spots: [],
    });
});
