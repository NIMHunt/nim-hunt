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
        const listener = this.listeners.get('click');
        if (!listener) return;
        listener.handler({
            target: {
                closest: () => (matches ? {} : null),
            },
        });
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

    documentRef.click({ matches: false });
    await flush();
    assert.deepEqual(calls, []);
    assert.equal(retry.isArmed(), true);

    documentRef.click({ matches: true });
    await flush();
    assert.deepEqual(calls, ['abc123']);
    assert.equal(retry.isArmed(), false);
});

test('failed retry re-arms for the next explicit interaction', async () => {
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
    documentRef.click();
    await flush();
    assert.equal(attempts, 1);
    assert.equal(retry.isArmed(), true);

    documentRef.click();
    await flush();
    assert.equal(attempts, 2);
    assert.equal(retry.isArmed(), false);
});

test('an in-flight retry cannot be duplicated by extra clicks', async () => {
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
    documentRef.click();
    await flush();
    documentRef.click();
    await flush();
    assert.equal(attempts, 1);
    assert.equal(retry.isInFlight(), true);

    release();
    await flush();
    assert.equal(retry.isInFlight(), false);
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
    documentRef.click();
    await flush();

    assert.equal(attempts, 0);
    assert.equal(retry.isArmed(), false);
});

test('browser utils arms interaction retry and reloads after successful re-authentication', async () => {
    const source = await readFile(new URL('./browser_utils.js', import.meta.url), 'utf8');

    assert.match(source, /armClaimSecurityRetryOnInteraction\(deviceId\)/);
    assert.match(source, /if \(claimSecurityErrorIsRetryable\(error\)\)/);
    assert.match(source, /await ensureClaimSecuritySession\(deviceId\)/);
    assert.match(source, /window\.location\.reload\(\)/);
    assert.match(source, /'device_wallet_mismatch'/);
});
