import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
    buildCreateDraftPayload,
    createDraftEditUrl,
} from '../static/find_spots_create.js';

test('Find Spots Create Spot reuses the identified session and creates a normal draft payload', () => {
    const payload = buildCreateDraftPayload(
        {
            device_id_hash: 'a'.repeat(64),
            wallet_available: true,
            language: 'en',
            location_available: false,
            lat: null,
            long: null,
            accuracy: null,
        },
        {
            title: '  Test Spot  ',
            isPrizeDraw: true,
            captchaPayload: { captcha_a: 3, captcha_b: 4, captcha_answer: 7 },
        },
    );

    assert.equal(payload.device_id_hash, 'a'.repeat(64));
    assert.equal(payload.wallet_available, true);
    assert.equal(payload.title, 'Test Spot');
    assert.equal(payload.is_prizedraw, true);
    assert.equal(payload.captcha_answer, 7);
});

test('Find Spots Create Spot proceeds to the full draft editor only after a draft exists', () => {
    assert.equal(createDraftEditUrl({ edit_url: '/create/44' }), '/create/44');
    assert.equal(createDraftEditUrl({ spot: { id: 45 } }), '/create/45');
    assert.equal(createDraftEditUrl({}, '/create'), '/create');
});

test('Find Spots opens the standard Create Spot card in place rather than routing through My Spots', () => {
    const source = readFileSync(new URL('../static/find_spots_create.js', import.meta.url), 'utf8');

    assert.match(source, /CREATE_TRIGGER_SELECTOR = 'a\[data-nim-hunt-create-spot="1"\]'/);
    assert.match(source, /class="notice-card create-spot-modal-card"/);
    assert.match(source, /class="create-spot-type-field"/);
    assert.match(source, /class="create-spot-captcha-field"/);
    assert.match(source, /runtime\.window\.fetch\('\/api\/create-spot\/draft'/);
    assert.match(source, /event\.preventDefault\(\);\s*show\(\);/s);
    assert.doesNotMatch(source, /my-spots\?create=1/);
});
