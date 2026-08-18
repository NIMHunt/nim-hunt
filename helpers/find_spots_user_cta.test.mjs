import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
    installFindSpotsCreateCtaGuard,
    syncFindSpotsEmptyChoices,
} from '../static/find_spots_user_cta.js';

function textNode(text) {
    return { nodeType: 3, textContent: text };
}

function nodeText(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.textContent || '';
    return `${node.textContent || ''}${(node.children || []).map(nodeText).join('')}`;
}

function makeLine(linkText, href) {
    const line = {
        nodeType: 1,
        hidden: false,
        dataset: {},
        children: [],
        replaceChildren(...children) {
            this.children = children;
        },
    };
    const link = {
        nodeType: 1,
        textContent: linkText,
        href,
        dataset: {},
        children: [],
        getAttribute(name) {
            return name === 'href' ? this.href : null;
        },
        closest(selector) {
            return selector === 'span' ? line : null;
        },
    };
    line.children = [link];
    return { line, link };
}

function makeRuntime({
    walletUserId = 7,
    userLocation = { lat: 51.5, long: -0.1 },
    claimHistoryKnown = true,
    hasExistingClaims = false,
    completed = false,
} = {}) {
    const demo = makeLine('try a Demo Spot?', '#spot-map');
    const global = makeLine('check out global spots?', '#spot-map');
    const create = makeLine('make one', '/create');
    const empty = {
        links: [demo.link, global.link, create.link],
        querySelectorAll() {
            return this.links;
        },
    };
    const documentObj = {
        body: { dataset: { createSpotUrl: '/create' } },
        createTextNode: textNode,
        getElementById(id) {
            return id === 'empty-spots' ? empty : null;
        },
    };
    const runtime = {
        walletUserId,
        userLocation,
        demoSpot: null,
        completed,
        claimHistoryKnown,
        hasExistingClaims,
        claimHistoryInFlight: false,
        lastRealSpotCount: 0,
        lastSessionPayload: null,
        document: documentObj,
        window: {
            location: { origin: 'https://nimhunt.app' },
            queueMicrotask(callback) { callback(); },
        },
    };
    return { runtime, empty, demo, global, create };
}

test('first-time user with no claims sees only the Demo Hunt choice', () => {
    const { runtime, demo, global, create } = makeRuntime();
    const result = syncFindSpotsEmptyChoices(runtime);

    assert.equal(result.demoVisible, true);
    assert.equal(result.globalVisible, false);
    assert.equal(result.createVisible, false);
    assert.equal(demo.line.hidden, false);
    assert.equal(global.line.hidden, true);
    assert.equal(create.line.hidden, true);
});

test('user with a real claim sees global and Create Spot choices instead of Demo Hunt', () => {
    const { runtime, demo, global, create } = makeRuntime({ hasExistingClaims: true });
    const result = syncFindSpotsEmptyChoices(runtime);

    assert.equal(result.demoVisible, false);
    assert.equal(result.globalVisible, true);
    assert.equal(result.createVisible, true);
    assert.equal(demo.line.hidden, true);
    assert.equal(nodeText(global.line), 'Would you like to check out global spots?');
    assert.equal(nodeText(create.line), 'Be the first to make a spot here.');
    assert.equal(create.link.textContent, 'make a spot');
    assert.equal(create.link.dataset.nimHuntCreateSpot, '1');
});

test('Create Spot copy normalisation is idempotent under repeated observer callbacks', () => {
    const { runtime, create } = makeRuntime({ hasExistingClaims: true });
    let text = create.link.textContent;
    let textWrites = 0;
    Object.defineProperty(create.link, 'textContent', {
        configurable: true,
        get() {
            return text;
        },
        set(value) {
            textWrites += 1;
            text = value;
        },
    });

    syncFindSpotsEmptyChoices(runtime);
    assert.equal(textWrites, 1);
    assert.equal(create.link.textContent, 'make a spot');

    // A real MutationObserver callback follows the first text-node change. The
    // second synchronisation must not change textContent again, otherwise it
    // would schedule another mutation callback forever.
    syncFindSpotsEmptyChoices(runtime);
    syncFindSpotsEmptyChoices(runtime);
    assert.equal(textWrites, 1);
});

test('identified user waits for claim history before any optional onboarding choice appears', () => {
    const { runtime, demo, global, create } = makeRuntime({
        claimHistoryKnown: false,
        hasExistingClaims: null,
    });
    const result = syncFindSpotsEmptyChoices(runtime);

    assert.equal(result.waitingForHistory, true);
    assert.equal(demo.line.hidden, true);
    assert.equal(global.line.hidden, true);
    assert.equal(create.line.hidden, true);
});

test('desktop visitor keeps the global choice but cannot see Demo Hunt or Create Spot', () => {
    const { runtime, demo, global, create } = makeRuntime({
        walletUserId: null,
        userLocation: null,
        claimHistoryKnown: false,
        hasExistingClaims: null,
    });
    const result = syncFindSpotsEmptyChoices(runtime);

    assert.equal(result.demoVisible, false);
    assert.equal(result.globalVisible, true);
    assert.equal(result.createVisible, false);
    assert.equal(demo.line.hidden, true);
    assert.equal(global.line.hidden, false);
    assert.equal(create.line.hidden, true);
});

test('completed Demo Hunt reveals the normal global and Create Spot choices', () => {
    const { runtime, demo, global, create } = makeRuntime({ completed: true });
    const result = syncFindSpotsEmptyChoices(runtime);

    assert.equal(result.demoVisible, false);
    assert.equal(result.globalVisible, true);
    assert.equal(result.createVisible, true);
    assert.equal(demo.line.hidden, true);
    assert.equal(global.line.hidden, false);
    assert.equal(create.line.hidden, false);
});

test('guard still follows asynchronous identity changes without rewriting the empty-state container', () => {
    const { runtime, create } = makeRuntime({
        walletUserId: null,
        userLocation: null,
        claimHistoryKnown: false,
        hasExistingClaims: null,
    });
    let mutationCallback = null;
    class FakeMutationObserver {
        constructor(callback) {
            mutationCallback = callback;
        }
        observe() {}
    }

    installFindSpotsCreateCtaGuard(runtime, { MutationObserverCtor: FakeMutationObserver });
    assert.equal(create.line.hidden, true);

    runtime.walletUserId = 9;
    assert.equal(create.line.hidden, false);
    mutationCallback();
    assert.equal(create.line.hidden, false);
});

test('claim-history eligibility check requests only one real claim and fails closed', () => {
    const source = readFileSync(new URL('../static/find_spots_user_cta.js', import.meta.url), 'utf8');
    assert.match(source, /\/api\/my-claims\?limit=1/);
    assert.match(source, /runtime\.hasExistingClaims = true;\s*}\s*finally/s);
    assert.match(source, /runtime\.lastSessionPayload = requestBodyJson\(options\)/);
});
