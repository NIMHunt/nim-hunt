import test from 'node:test';
import assert from 'node:assert/strict';

import {
  adjustedPrizedrawLimits,
  largestOptionBelow,
  prizedrawLimitsAreValid,
  smallestOptionAbove,
} from '../static/prizedraw_rules.js';

const participantOptions = [2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 30, 100, 200, 1000, 0];
const perUserOptions = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const prizeOptions = [1, 2, 3, 4, 5, 10, 20, 50, 100];

test('option helpers preserve the existing discrete increments', () => {
  assert.equal(smallestOptionAbove(participantOptions, 10), 20);
  assert.equal(largestOptionBelow(prizeOptions, 10), 5);
  assert.equal(smallestOptionAbove(participantOptions, 1000), null);
});

test('finite Prizedraw limits use strict relationships', () => {
  assert.equal(prizedrawLimitsAreValid({ maxClaimsPerUser: 1, maxTotalClaims: 2, prizeCount: 1 }), true);
  assert.equal(prizedrawLimitsAreValid({ maxClaimsPerUser: 2, maxTotalClaims: 2, prizeCount: 1 }), false);
  assert.equal(prizedrawLimitsAreValid({ maxClaimsPerUser: 1, maxTotalClaims: 2, prizeCount: 2 }), false);
  assert.equal(prizedrawLimitsAreValid({ maxClaimsPerUser: 1, maxTotalClaims: 1, prizeCount: 1 }), false);
  assert.equal(prizedrawLimitsAreValid({ maxClaimsPerUser: 10, maxTotalClaims: 0, prizeCount: 100 }), true);
});

test('changing a finite child limit raises Total Participants to the next option', () => {
  assert.deepEqual(adjustedPrizedrawLimits({
    changedName: 'perUser',
    maxClaimsPerUser: 10,
    maxTotalClaims: 10,
    prizeCount: 1,
    participantOptions,
    perUserOptions,
    prizeOptions,
  }), { maxClaimsPerUser: 10, maxTotalClaims: 20, prizeCount: 1 });

  assert.deepEqual(adjustedPrizedrawLimits({
    changedName: 'prizeCount',
    maxClaimsPerUser: 1,
    maxTotalClaims: 10,
    prizeCount: 10,
    participantOptions,
    perUserOptions,
    prizeOptions,
  }), { maxClaimsPerUser: 1, maxTotalClaims: 20, prizeCount: 10 });
});

test('lowering Total Participants lowers dependent values to existing options', () => {
  assert.deepEqual(adjustedPrizedrawLimits({
    changedName: 'totalParticipants',
    maxClaimsPerUser: 5,
    maxTotalClaims: 5,
    prizeCount: 5,
    participantOptions,
    perUserOptions,
    prizeOptions,
  }), { maxClaimsPerUser: 4, maxTotalClaims: 5, prizeCount: 4 });
});
