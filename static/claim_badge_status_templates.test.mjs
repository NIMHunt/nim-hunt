import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const claimTemplate = await readFile(new URL('../templates/claim.html', import.meta.url), 'utf8');
const myClaimsTemplate = await readFile(new URL('../templates/my_claims.html', import.meta.url), 'utf8');

test('Claim badge helper loads on detail and My Claims pages', () => {
    assert.match(claimTemplate, /claim_badge_status\.js/);
    assert.match(myClaimsTemplate, /claim_badge_status\.js\?v=claim-badge-v2-20260726/);
});
