import assert from 'node:assert/strict';
import test from 'node:test';

import {
    MY_SPOTS_MAP_COLOURS,
    mySpotEndHasElapsed,
    mySpotsMapColourForSpot,
    spotsVisibleOnMySpotsMap,
} from '../static/my_spots_map_policy.js';

const NOW = 2_000_000_000;

test('only Spots whose end datetime has elapsed are removed from My Spots map', () => {
    const spots = [
        { id: 1, status_label: 'active', ends_at: NOW + 60 },
        { id: 2, status_label: 'completed', ends_at: NOW + 60 },
        { id: 3, status_label: 'cancelled', ends_at: NOW + 60 },
        { id: 4, status_label: 'completed', ends_at: NOW },
        { id: 5, status_label: 'cancelled', ends_at: NOW - 1 },
        { id: 6, status_label: 'completed', ends_at: null },
    ];

    assert.deepEqual(
        spotsVisibleOnMySpotsMap(spots, NOW).map((spot) => spot.id),
        [1, 2, 3, 6],
    );
    assert.equal(mySpotEndHasElapsed(spots[3], NOW), true);
    assert.equal(mySpotEndHasElapsed(spots[1], NOW), false);
});

test('completed and cancelled Spots use Nimiq blue and red while still visible', () => {
    assert.equal(
        mySpotsMapColourForSpot({ status_label: 'completed' }),
        MY_SPOTS_MAP_COLOURS.completed,
    );
    assert.equal(MY_SPOTS_MAP_COLOURS.completed, '#0582ca');

    assert.equal(
        mySpotsMapColourForSpot({ status_label: 'cancelled' }),
        MY_SPOTS_MAP_COLOURS.cancelled,
    );
    assert.equal(MY_SPOTS_MAP_COLOURS.cancelled, '#d94432');
});

test('existing active, prizedraw and muted map colours are preserved', () => {
    assert.equal(
        mySpotsMapColourForSpot({ status_label: 'active', is_prizedraw: false }),
        '#21bca5',
    );
    assert.equal(
        mySpotsMapColourForSpot({ status_label: 'active', is_prizedraw: true }),
        '#ffc435',
    );
    assert.equal(mySpotsMapColourForSpot({ status_label: 'upcoming' }), '#8c90a8');
});
