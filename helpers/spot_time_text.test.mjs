import test from 'node:test';
import assert from 'node:assert/strict';
import {
  highestTimeUnitText,
  spotScheduleSummary,
} from '../static/spot_ui.js';

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

test('Spot time text caps its largest unit at days', () => {
  assert.equal(highestTimeUnitText((21 * DAY) - 2, 'Remaining'), '21 Days Remaining');
  assert.equal(highestTimeUnitText(40 * HOUR, 'Remaining'), '2 Days Remaining');
});

test('Spot time text chooses the unit before rounding the value', () => {
  assert.equal(highestTimeUnitText((36 * HOUR) - 1, 'Remaining'), '1 Day Remaining');
  assert.equal(highestTimeUnitText(36 * HOUR, 'Remaining'), '2 Days Remaining');
  assert.equal(highestTimeUnitText(23.8 * HOUR, 'Remaining'), '24 Hours Remaining');
  assert.equal(highestTimeUnitText(59.8 * MINUTE, 'Remaining'), '60 Minutes Remaining');
  assert.equal(highestTimeUnitText(60, 'Remaining'), '1 Minute Remaining');
  assert.equal(highestTimeUnitText(59, 'Remaining'), 'Less than 1 Minute Remaining');
});

test('Spot schedule summaries use the rounded display for active and upcoming Spots', () => {
  const now = 1_000_000;

  assert.equal(
    spotScheduleSummary({ status_label: 'active', ends_at: now + (40 * HOUR) }, { now }),
    '2 Days Remaining',
  );
  assert.equal(
    spotScheduleSummary({ status_label: 'upcoming', starts_at: now + (23.8 * HOUR) }, { now }),
    '24 Hours Until Start',
  );
});
