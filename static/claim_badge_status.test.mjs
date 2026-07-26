import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const moduleSource = await readFile(new URL('./claim_badge_status.js', import.meta.url), 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(moduleSource).toString('base64')}`;
const { compactClaimBadgeStatus } = await import(moduleUrl);

test('Claim badges omit trailing parenthetical detail', () => {
    assert.equal(compactClaimBadgeStatus('Success (Pending)'), 'Success');
    assert.equal(compactClaimBadgeStatus('Success(Pending)'), 'Success');
    assert.equal(compactClaimBadgeStatus('Success(Processing)'), 'Success');
    assert.equal(compactClaimBadgeStatus('Success (Verifying)'), 'Success');
    assert.equal(compactClaimBadgeStatus('Won! (Payment pending)'), 'Won!');
});

test('Claim badges preserve statuses without trailing detail', () => {
    assert.equal(compactClaimBadgeStatus('Success'), 'Success');
    assert.equal(compactClaimBadgeStatus('Pending'), 'Pending');
    assert.equal(compactClaimBadgeStatus('Failed'), 'Failed');
    assert.equal(compactClaimBadgeStatus('Success (Pending) later'), 'Success (Pending) later');
});

test('Claim badge compactor covers detail and My Claims badge lists only', () => {
    assert.match(moduleSource, /'claim-detail-list'/);
    assert.match(moduleSource, /'my-claims-list'/);
    assert.match(moduleSource, /querySelectorAll\('\.spot-badge'\)/);
    assert.doesNotMatch(moduleSource, /claim-status-keyword/);
});
