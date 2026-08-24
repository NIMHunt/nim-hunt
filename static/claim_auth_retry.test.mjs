import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { createClaimAuthInteractionRetry } from './claim_auth_retry.js';

class FakeDocument {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, handler, capture) {
        this.listeners.set(type, { handler, capture });
    }

    removeEventListener(type, handler, capture) {
        const current = this.listeners.get(type);
        if (current?.handler === handler && current?.capture === capture) {
            this.listeners.delete(type);
        }
    }

    click({ matches = true } = {}) {
        const event = {
            defaultPrevented: false,
            propagationStopped: false,
            immediatePropagationStopped: false,
            preventDefault() {
                this.defaultPrevented = true;
            },
            stopPropagation() {
                this.propagationStopped = true;
            },
            stopImmediatePropagation() {
                this.immediatePropagationStopped = true;
                this.propagationStopped = true;
            },
            target: {
                closest: () => (matches ? {} : null),
            },
        };
        const listener = this.listeners.get('click');
        if (listener) listener.handler(event);
        return event;
    }
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('retry waits for a matching claim/report interaction', async () => {
    const documentRef = new FakeDocument();
    const calls = [];
    const retry = createClaimAuthInteractionRetry({
        documentRef,
        retry: async (deviceId) => calls.push(deviceId),
    });

    retry.arm('ABC123');
    assert.equal(retry.isArmed(), true);

    const unrelated = documentRef.click({ matches: false });
    await flush();
    assert.deepEqual(calls, []);
    assert.equal(retry.isArmed(), true);
    assert.equal(unrelated.defaultPrevented, false);

    const matching = documentRef.click({ matches: true });
    await flush();
    assert.deepEqual(calls, ['abc123']);
    assert.equal(retry.isArmed(), false);
    assert.equal(matching.defaultPrevented, true);
    assert.equal(matching.immediatePropagationStopped, true);
});

test('failed retry re-arms for the next explicit interaction without starting the page action', async () => {
    const documentRef = new FakeDocument();
    let attempts = 0;
    const retry = createClaimAuthInteractionRetry({
        documentRef,
        retry: async () => {
            attempts += 1;
            if (attempts === 1) throw new Error('declined');
        },
    });

    retry.arm('device');
    const first = documentRef.click();
    await flush();
    assert.equal(attempts, 1);
    assert.equal(retry.isArmed(), true);
    assert.equal(first.defaultPrevented, true);
    assert.equal(first.immediatePropagationStopped, true);

    const second = documentRef.click();
    await flush();
    assert.equal(attempts, 2);
    assert.equal(retry.isArmed(), false);
    assert.equal(second.defaultPrevented, true);
    assert.equal(second.immediatePropagationStopped, true);
});

test('an in-flight retry cannot be duplicated and keeps swallowing claim/report clicks', async () => {
    const documentRef = new FakeDocument();
    let attempts = 0;
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    const retry = createClaimAuthInteractionRetry({
        documentRef,
        retry: async () => {
            attempts += 1;
            await pending;
        },
    });

    retry.arm('device');
    const first = documentRef.click();
    await flush();
    const second = documentRef.click();
    await flush();
    assert.equal(attempts, 1);
    assert.equal(retry.isInFlight(), true);
    assert.equal(first.defaultPrevented, true);
    assert.equal(second.defaultPrevented, true);
    assert.equal(second.immediatePropagationStopped, true);

    release();
    await flush();
    assert.equal(retry.isInFlight(), false);
});

test('claim-detail body retries do not consume ordinary page interactions', async () => {
    const documentRef = new FakeDocument();
    let attempts = 0;
    const retry = createClaimAuthInteractionRetry({
        documentRef,
        selector: 'body',
        retry: async () => { attempts += 1; },
    });

    retry.arm('device');
    const click = documentRef.click();
    await flush();

    assert.equal(attempts, 1);
    assert.equal(click.defaultPrevented, false);
    assert.equal(click.immediatePropagationStopped, false);
});

test('disarm removes the pending interaction retry', async () => {
    const documentRef = new FakeDocument();
    let attempts = 0;
    const retry = createClaimAuthInteractionRetry({
        documentRef,
        retry: async () => { attempts += 1; },
    });

    retry.arm('device');
    retry.disarm();
    const click = documentRef.click();
    await flush();

    assert.equal(attempts, 0);
    assert.equal(retry.isArmed(), false);
    assert.equal(click.defaultPrevented, false);
});

test('browser utils arms interaction retry and reloads after successful re-authentication', async () => {
    const source = await readFile(new URL('./browser_utils.js', import.meta.url), 'utf8');

    assert.match(source, /armClaimSecurityRetryOnInteraction\(deviceId\)/);
    assert.match(source, /if \(claimSecurityErrorIsRetryable\(error\)\)/);
    assert.match(source, /await ensureClaimSecuritySession\(deviceId\)/);
    assert.match(source, /window\.location\.reload\(\)/);
    assert.match(source, /'device_wallet_mismatch'/);
});

test('claim-detail pages can recover both signature and device-lookup failures', async () => {
    const source = await readFile(new URL('./browser_utils.js', import.meta.url), 'utf8');

    assert.match(
        source,
        /return path === '\/spots' \|\| path\.startsWith\('\/claim\/'\);/,
    );
    assert.match(source, /return path\.startsWith\('\/claim\/'\) \? 'body' : undefined;/);
    assert.match(source, /CLAIM_SECURITY_RELOAD_RETRY/);
    assert.match(source, /!claimSecurityInteractionRetry\?\.isArmed\(\)/);
    assert.match(source, /armClaimSecurityRetryOnInteraction\(CLAIM_SECURITY_RELOAD_RETRY\)/);
});
