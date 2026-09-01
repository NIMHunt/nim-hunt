import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createDuplicateAwareFetch,
  duplicateSpotEndpoint,
  requestTargetsOrdinaryDraftCreation,
  spotFromListItem,
} from '../static/spot_duplicate.js';

test('duplicate endpoint requires and embeds a positive Spot id', () => {
  assert.equal(duplicateSpotEndpoint(42), '/api/my-spots/42/duplicate');
  assert.throws(() => duplicateSpotEndpoint(0), /positive source Spot id/);
});

test('only the ordinary POST draft request is eligible for rewriting', () => {
  const base = 'https://nimhunt.app/my-spots';
  assert.equal(
    requestTargetsOrdinaryDraftCreation(
      '/api/create-spot/draft',
      { method: 'POST' },
      base,
    ),
    true,
  );
  assert.equal(
    requestTargetsOrdinaryDraftCreation(
      '/api/create-spot/draft',
      { method: 'GET' },
      base,
    ),
    false,
  );
  assert.equal(
    requestTargetsOrdinaryDraftCreation(
      '/api/my-spots',
      { method: 'POST' },
      base,
    ),
    false,
  );
});

test('active duplication rewrites one creation request and preserves its body', async () => {
  const calls = [];
  let successCount = 0;
  const fetch = createDuplicateAwareFetch(
    async (input, init) => {
      calls.push([input, init]);
      return { ok: true };
    },
    {
      sourceSpotId: () => 17,
      baseUrl: 'https://nimhunt.app',
      onSuccess: () => { successCount += 1; },
    },
  );
  const init = { method: 'POST', body: '{"title":"Copy"}' };
  await fetch('/api/create-spot/draft', init);

  assert.deepEqual(calls, [['/api/my-spots/17/duplicate', init]]);
  assert.equal(successCount, 1);
});

test('failed duplication stays selected for a manual retry', async () => {
  let successCount = 0;
  const fetch = createDuplicateAwareFetch(
    async () => ({ ok: false }),
    {
      sourceSpotId: () => 4,
      baseUrl: 'https://nimhunt.app',
      onSuccess: () => { successCount += 1; },
    },
  );
  await fetch('/api/create-spot/draft', { method: 'POST' });
  assert.equal(successCount, 0);
});

test('list-item Spot parsing rejects malformed or missing identifiers', () => {
  assert.deepEqual(
    spotFromListItem({ dataset: { renderSignature: '{"id":8,"title":"Eight"}' } }),
    { id: 8, title: 'Eight' },
  );
  assert.equal(spotFromListItem({ dataset: { renderSignature: '{bad' } }), null);
  assert.equal(spotFromListItem({ dataset: { renderSignature: '{"id":0}' } }), null);
});
