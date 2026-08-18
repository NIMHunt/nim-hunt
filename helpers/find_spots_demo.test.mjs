import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
    DEMO_DISTANCE_METRES,
    DEMO_RADIUS_METRES,
    DEMO_SPOT_ID,
    GLOBAL_MAP_ZOOM,
    distanceMetres,
    makeDemoClaimStatus,
    makeDemoSpot,
    mapIsFullyZoomedOut,
    pointAtDistance,
    renderEmptyState,
    showDemoCreatedNotice,
    spotInSearchViewport,
} from '../static/find_spots_demo.js';

test('demo spot uses a 250 metre hunt distance with a 200 metre radius', () => {
    const origin = { lat: 51.5074, long: -0.1278 };
    const centre = pointAtDistance(origin.lat, origin.long, DEMO_DISTANCE_METRES, 1.25);
    const distance = distanceMetres(origin.lat, origin.long, centre.lat, centre.long);
    assert.equal(DEMO_DISTANCE_METRES, 250);
    assert.ok(Math.abs(distance - DEMO_DISTANCE_METRES) < 0.1);

    const spot = makeDemoSpot({ userId: 7, ...origin, bearingRadians: 1.25, now: 1_700_000_000_000 });
    assert.equal(spot.id, DEMO_SPOT_ID);
    assert.equal(spot.radius, DEMO_RADIUS_METRES);
    assert.equal(spot.total_value, 0);
    assert.equal(spot.is_prizedraw, false);
    assert.equal(spot.claim_duration, 0);
    assert.equal(spot.title, 'Demo Hunt!');
    assert.equal(spot.description, 'This is a practice spot. Move into its highlighted area and tap "Claim" to complete it.');
});

test('demo claim status is claimable inside the radius and unavailable outside it', () => {
    const origin = { lat: 51.5074, long: -0.1278 };
    const demoSpot = makeDemoSpot({ userId: 7, ...origin, bearingRadians: 0, now: 1_700_000_000_000 });
    const runtime = {
        walletUserId: 7,
        demoSpot,
        userLocation: origin,
        currentLocation: { lat: demoSpot.lat, long: demoSpot.long },
    };

    const inside = makeDemoClaimStatus(runtime);
    assert.equal(inside.allowed, true);
    assert.equal(inside.action, 'claim');
    assert.equal(inside.kind, 'demo');
    assert.equal(inside.within_radius, true);
    assert.equal(inside.reward_amount, 0);

    runtime.currentLocation = pointAtDistance(
        demoSpot.lat,
        demoSpot.long,
        DEMO_RADIUS_METRES + 25,
        0,
    );
    const outside = makeDemoClaimStatus(runtime);
    assert.equal(outside.allowed, false);
    assert.equal(outside.action, 'unavailable');
    assert.equal(outside.reason, 'outside_radius');
    assert.equal(outside.within_radius, false);
});

test('Demo Spot creation requests the ordinary NimHunt notice with the welcome-card Let’s Go! copy', () => {
    let dispatched = null;
    class FakeCustomEvent {
        constructor(type, { detail } = {}) {
            this.type = type;
            this.detail = detail;
        }
    }
    const runtime = {
        window: {
            CustomEvent: FakeCustomEvent,
            dispatchEvent(event) {
                dispatched = event;
                return true;
            },
        },
    };

    assert.equal(showDemoCreatedNotice(runtime), true);
    assert.equal(dispatched.type, 'nimhunt:demo-notice');
    assert.equal(dispatched.detail.title, 'Demo Spot created!');
    assert.equal(
        dispatched.detail.body,
        "We've placed a practice spot nearby. Head into the purple area and claim it just like a real NimHunt spot.",
    );
    assert.equal(dispatched.detail.buttonText, "Let's Go!");
});

test('demo spot is injected only into a search viewport that contains it', () => {
    const spot = { lat: 51.5, long: -0.1 };
    assert.equal(spotInSearchViewport(
        spot,
        'https://example.test/api/spots/search?min_lat=51&max_lat=52&min_long=-1&max_long=1',
    ), true);
    assert.equal(spotInSearchViewport(
        spot,
        'https://example.test/api/spots/search?min_lat=40&max_lat=41&min_long=-1&max_long=1',
    ), false);
});

test('global view detection only activates at the fully zoomed-out map level', () => {
    assert.equal(mapIsFullyZoomedOut({ getZoom: () => GLOBAL_MAP_ZOOM }), true);
    assert.equal(mapIsFullyZoomedOut({ getZoom: () => GLOBAL_MAP_ZOOM + 1 }), false);
    assert.equal(mapIsFullyZoomedOut(null), false);
});

function fakeElement(tagName = 'div') {
    const element = {
        nodeType: 1,
        tagName: String(tagName).toUpperCase(),
        hidden: false,
        dataset: {},
        className: '',
        textContent: '',
        children: [],
        firstElementChild: null,
        replaceCount: 0,
        append(...children) {
            this.children.push(...children);
            if (!this.firstElementChild) {
                this.firstElementChild = children.find((child) => child?.nodeType !== 3) || null;
            }
        },
        addEventListener() {},
        replaceChildren(...children) {
            this.replaceCount += 1;
            this.children = children;
            this.firstElementChild = children.find((child) => child?.nodeType !== 3) || null;
        },
    };
    element.classList = {
        add(...names) {
            const existing = new Set(element.className.split(/\s+/).filter(Boolean));
            for (const name of names) existing.add(name);
            element.className = [...existing].join(' ');
        },
        contains(name) {
            return element.className.split(/\s+/).includes(name);
        },
    };
    return element;
}

function nodeText(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.textContent || '';
    return `${node.textContent || ''}${(node.children || []).map(nodeText).join('')}`;
}

function elementsByTag(node, tagName) {
    if (!node) return [];
    const wanted = String(tagName).toUpperCase();
    const matches = node.tagName === wanted ? [node] : [];
    for (const child of node.children || []) matches.push(...elementsByTag(child, wanted));
    return matches;
}

test('empty-state rendering stays idempotent, restores Create Spot, and hides global CTA at world zoom', () => {
    const empty = fakeElement();
    const list = fakeElement();
    list.hidden = true;
    const map = {
        zoom: 14,
        getZoom() { return this.zoom; },
    };

    const documentObj = {
        body: { dataset: { createSpotUrl: '/create' } },
        getElementById(id) {
            if (id === 'empty-spots') return empty;
            if (id === 'spot-list') return list;
            return null;
        },
        createElement(tagName) {
            return fakeElement(tagName);
        },
        createTextNode(text) {
            return { nodeType: 3, textContent: text };
        },
    };
    const runtime = {
        document: documentObj,
        walletUserId: null,
        userLocation: null,
        demoSpot: null,
        completed: false,
        lastRealSpotCount: 0,
        map,
    };

    renderEmptyState(runtime);
    renderEmptyState(runtime);
    assert.equal(empty.replaceCount, 1);
    assert.match(nodeText(empty), /check out global spots\?/);
    assert.match(nodeText(empty), /be the first to make one\./i);
    assert.ok(elementsByTag(empty, 'a').some((link) => link.href === '/create' && link.textContent === 'make one'));

    runtime.walletUserId = 7;
    runtime.userLocation = { lat: 51.5, long: -0.1 };
    renderEmptyState(runtime);
    renderEmptyState(runtime);
    assert.equal(empty.replaceCount, 2);
    assert.match(nodeText(empty), /try a Demo Spot\?/);

    map.zoom = GLOBAL_MAP_ZOOM;
    renderEmptyState(runtime);
    renderEmptyState(runtime);
    assert.equal(empty.replaceCount, 3);
    assert.doesNotMatch(nodeText(empty), /check out global spots\?/);
    assert.match(nodeText(empty), /try a Demo Spot\?/);
    assert.match(nodeText(empty), /make one\./);

    map.zoom = GLOBAL_MAP_ZOOM + 1;
    renderEmptyState(runtime);
    assert.equal(empty.replaceCount, 4);
    assert.match(nodeText(empty), /check out global spots\?/);
});

test('Demo Spot map colour is handled by the ordinary marker renderer, not post-render restyling', () => {
    const findSpotsSource = readFileSync(new URL('../static/find_spots.js', import.meta.url), 'utf8');
    const demoSource = readFileSync(new URL('../static/find_spots_demo.js', import.meta.url), 'utf8');

    assert.match(findSpotsSource, /demo:\s*'#8f5bd7'/);
    assert.match(findSpotsSource, /if \(spot\.demo\) return MAP_COLOURS\.demo;/);
    assert.doesNotMatch(demoSource, /styleDemoLayers|resolvedDemoColour|demo-spot-created-toast/);
});

test('Demo Spot list and completion page use the purple/dark-mode presentation and return to the real Find Spots route', () => {
    const css = readFileSync(new URL('../static/ux_accessibility.css', import.meta.url), 'utf8');
    const success = readFileSync(new URL('../static/demo_claim_success.html', import.meta.url), 'utf8');

    assert.match(css, /\.spot-list-item\.is-claim-demo\s*\{[^}]*background:\s*#8f5bd7;/s);
    assert.match(css, /\.spot-claim-button\.is-demo\s*\{[^}]*color:\s*#8f5bd7\s*!important;/s);
    assert.doesNotMatch(css, /\.spot-list-item\.is-demo-spot\s*\{[^}]*box-shadow:/s);
    assert.match(css, /html\[data-theme="dark"\] body\.nq-style \.demo-success-card h2/);
    assert.match(css, /html\[data-theme="dark"\] body\.nq-style \.demo-success-card p/);

    assert.match(success, /<a class="back-link" href="\/spots"/);
    assert.match(success, /<a class="nq-button light-blue" href="\/spots">OK<\/a>/);
    assert.doesNotMatch(success, /href="\/find-spots"/);
    assert.doesNotMatch(success, />Home<\/a>/);
});
