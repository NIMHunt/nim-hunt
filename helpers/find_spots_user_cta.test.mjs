import test from 'node:test';
import assert from 'node:assert/strict';
import {
    installFindSpotsCreateCtaGuard,
    syncFindSpotsCreateCta,
} from '../static/find_spots_user_cta.js';

function makeCreateLine() {
    const line = { hidden: false };
    const link = {
        textContent: 'make one',
        href: '/create',
        getAttribute(name) {
            return name === 'href' ? '/create' : null;
        },
        closest(selector) {
            return selector === 'span' ? line : null;
        },
    };
    return { line, link };
}

function makeRuntime(walletUserId = null) {
    const first = makeCreateLine();
    const empty = {
        links: [first.link],
        querySelectorAll() {
            return this.links;
        },
    };
    const runtime = {
        walletUserId,
        window: { location: { origin: 'https://nimhunt.app' } },
        document: {
            body: { dataset: { createSpotUrl: '/create' } },
            getElementById(id) {
                return id === 'empty-spots' ? empty : null;
            },
        },
    };
    return { runtime, empty, first };
}

test('Create Spot CTA is hidden for an unidentified desktop visitor and shown for a user', () => {
    const { runtime, first } = makeRuntime(null);
    assert.equal(syncFindSpotsCreateCta(runtime), false);
    assert.equal(first.line.hidden, true);

    runtime.walletUserId = 7;
    assert.equal(syncFindSpotsCreateCta(runtime), true);
    assert.equal(first.line.hidden, false);
});

test('Create Spot CTA guard follows async identity changes and later empty-state renders', () => {
    const { runtime, empty, first } = makeRuntime(null);
    let mutationCallback = null;
    class FakeMutationObserver {
        constructor(callback) {
            mutationCallback = callback;
        }
        observe() {}
    }

    installFindSpotsCreateCtaGuard(runtime, { MutationObserverCtor: FakeMutationObserver });
    assert.equal(first.line.hidden, true);

    runtime.walletUserId = 9;
    assert.equal(first.line.hidden, false);

    runtime.walletUserId = null;
    assert.equal(first.line.hidden, true);

    const replacement = makeCreateLine();
    empty.links = [replacement.link];
    mutationCallback();
    assert.equal(replacement.line.hidden, true);

    runtime.walletUserId = 9;
    assert.equal(replacement.line.hidden, false);
});
