import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../static/geo_coordinates.js', import.meta.url);
const source = await readFile(sourceUrl, 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const {
    normaliseLongitude,
    validCanonicalCoordinates,
} = await import(moduleUrl);

test('wrapped Auckland longitudes normalise to the same canonical coordinate', () => {
    const auckland = 174.7633;

    assert.ok(Math.abs(normaliseLongitude(auckland) - auckland) < 1e-9);
    assert.ok(Math.abs(normaliseLongitude(auckland - 360) - auckland) < 1e-9);
    assert.ok(Math.abs(normaliseLongitude(auckland + 360) - auckland) < 1e-9);
});

test('normaliseLongitude handles repeated worlds and canonical boundaries', () => {
    assert.equal(normaliseLongitude(545), -175);
    assert.equal(normaliseLongitude(-535), -175);
    assert.equal(normaliseLongitude(180), -180);
    assert.equal(normaliseLongitude(-180), -180);
    assert.equal(normaliseLongitude(-0), 0);
    assert.equal(normaliseLongitude('not-a-number'), null);
});

test('canonical coordinate validation rejects out-of-range map copies', () => {
    assert.equal(validCanonicalCoordinates(-36.8485, 174.7633), true);
    assert.equal(validCanonicalCoordinates(-36.8485, -185.2367), false);
    assert.equal(validCanonicalCoordinates(-36.8485, 534.7633), false);
    assert.equal(validCanonicalCoordinates(91, 0), false);
    assert.equal(validCanonicalCoordinates(-91, 0), false);
    assert.equal(validCanonicalCoordinates(0, 181), false);
    assert.equal(validCanonicalCoordinates(0, -181), false);
});
