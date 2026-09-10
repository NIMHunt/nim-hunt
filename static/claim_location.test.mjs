import assert from 'node:assert/strict';
import test from 'node:test';
import { combineFreshClaimLocations, collectFreshClaimLocation } from './claim_location.js';

test('three fresh readings use coordinate and accuracy medians', () => {
    const now = 100000;
    const point = combineFreshClaimLocations([
        { lat: 55.1, long: -4.1, accuracy: 12, timestamp: now - 300 },
        { lat: 80, long: 40, accuracy: 900, timestamp: now - 200 },
        { lat: 55.2, long: -4.2, accuracy: 10, timestamp: now - 100 },
    ], { now });
    assert.deepEqual(point, { lat: 55.2, long: -4.1, accuracy: 12, timestamps: [99700, 99800, 99900] });
});

test('stale and non-finite samples are excluded', () => {
    const now = 100000;
    const point = combineFreshClaimLocations([
        { lat: 1, long: 2, accuracy: 3, timestamp: now - 20000 },
        { lat: Number.NaN, long: 2, accuracy: 3, timestamp: now },
        { lat: 4, long: 5, accuracy: 6, timestamp: now - 10 },
        { lat: 4.1, long: 5.1, accuracy: 7, timestamp: now - 5 },
    ], { now });
    assert.equal(point.lat, 4.1);
});

test('permission denial is understandable', async () => {
    const geolocation = { getCurrentPosition(_ok, fail) { fail({ code: 1 }); } };
    await assert.rejects(collectFreshClaimLocation({ geolocation }), /permission was denied/i);
});

test('three sequential requests obey one overall collection budget', async () => {
    const started = Date.now();
    const geolocation = { getCurrentPosition() {} };
    await assert.rejects(
        collectFreshClaimLocation({ geolocation, budgetMs: 40 }),
        /fresh GPS readings are unavailable/i,
    );
    assert.ok(Date.now() - started < 200, 'overall timeout must not multiply by sample count');
});
