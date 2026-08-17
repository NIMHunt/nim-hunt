import test from 'node:test';
import assert from 'node:assert/strict';
import {
    DEMO_DISTANCE_METRES,
    DEMO_RADIUS_METRES,
    DEMO_SPOT_ID,
    distanceMetres,
    makeDemoSpot,
    pointAtDistance,
    renderEmptyState,
    spotInSearchViewport,
} from '../static/find_spots_demo.js';

test('demo spot is placed 250 metres from the user with a 200 metre radius', () => {
    const origin = { lat: 51.5074, long: -0.1278 };
    const centre = pointAtDistance(origin.lat, origin.long, DEMO_DISTANCE_METRES, 1.25);
    const distance = distanceMetres(origin.lat, origin.long, centre.lat, centre.long);
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

function fakeElement() {
    const element = {
        hidden: false,
        dataset: {},
        className: '',
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

test('empty-state rendering is idempotent and cannot feed its MutationObserver forever', () => {
    const empty = fakeElement();
    const list = fakeElement();
    list.hidden = true;

    const documentObj = {
        getElementById(id) {
            if (id === 'empty-spots') return empty;
            if (id === 'spot-list') return list;
            return null;
        },
        createElement() {
            return fakeElement();
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
    };

    renderEmptyState(runtime);
    renderEmptyState(runtime);
    assert.equal(empty.replaceCount, 1);

    runtime.walletUserId = 7;
    runtime.userLocation = { lat: 51.5, long: -0.1 };
    renderEmptyState(runtime);
    renderEmptyState(runtime);
    assert.equal(empty.replaceCount, 2);
});
