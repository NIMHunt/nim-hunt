import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createPendingDepositStore,
  LEGACY_PENDING_DEPOSIT_STORAGE_KEY,
  OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY,
  PENDING_DEPOSIT_STORAGE_KEY,
  recoverPendingDepositQueue,
} from '../static/pending_deposit_store.js';

class MemoryStorage {
  constructor(initial = {}) {
    this.values = new Map(Object.entries(initial));
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

function record(spotId, byte, overrides = {}) {
  return {
    spotId,
    txHash: byte.repeat(64),
    fromAddress: `NQ ${spotId} FUNDING`,
    amount: 100000 + spotId,
    createdAt: 1000 + spotId,
    ...overrides,
  };
}

test('two rapid deposits are stored independently and cleared independently', () => {
  const storage = new MemoryStorage();
  const store = createPendingDepositStore(storage);
  const first = record(1, 'a');
  const second = record(2, 'b');

  assert.equal(store.save(first), true);
  assert.equal(store.save(second), true);
  assert.deepEqual(store.load(), [first, second]);

  assert.equal(store.remove(second), true);
  assert.deepEqual(store.load(), [first]);
  assert.match(storage.getItem(PENDING_DEPOSIT_STORAGE_KEY), /"spotId":1/);
});

test('the previous single-record slot migrates without losing an unresolved deposit', () => {
  const legacy = record(3, 'c');
  const storage = new MemoryStorage({
    [LEGACY_PENDING_DEPOSIT_STORAGE_KEY]: JSON.stringify(legacy),
    [OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY]: JSON.stringify(record(9, 'f')),
  });
  const store = createPendingDepositStore(storage);

  assert.deepEqual(store.load(), [legacy]);
  assert.equal(storage.getItem(LEGACY_PENDING_DEPOSIT_STORAGE_KEY), null);
  assert.equal(storage.getItem(OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY), null);
  assert.deepEqual(JSON.parse(storage.getItem(PENDING_DEPOSIT_STORAGE_KEY)), [legacy]);
});

test('recovery submits sequentially, removes successes and retains failures', async () => {
  const storage = new MemoryStorage();
  const store = createPendingDepositStore(storage);
  const first = record(1, 'a');
  const second = record(2, 'b');
  const third = record(3, 'c');
  store.save(first);
  store.save(second);
  store.save(third);

  const calls = [];
  const result = await recoverPendingDepositQueue({
    store,
    async submit(item) {
      calls.push(item.spotId);
      if (item.spotId === 2) throw new Error('temporary recording failure');
    },
  });

  assert.deepEqual(calls, [1, 2, 3]);
  assert.equal(result.attemptedCount, 3);
  assert.equal(result.recoveredCount, 2);
  assert.equal(result.failures.length, 1);
  assert.equal(result.failures[0].record.spotId, 2);
  assert.deepEqual(store.load(), [second]);
});

test('a duplicate transaction hash cannot be reassigned to another Spot', () => {
  const storage = new MemoryStorage();
  const store = createPendingDepositStore(storage);
  const first = record(1, 'd');
  const conflict = record(2, 'e', { txHash: first.txHash });

  assert.equal(store.save(first), true);
  assert.equal(store.save(conflict), false);
  assert.deepEqual(store.load(), [first]);
});

test('disabled session storage leaves the immediate deposit path usable', () => {
  const storage = {
    getItem() { throw new Error('disabled'); },
    setItem() { throw new Error('disabled'); },
    removeItem() { throw new Error('disabled'); },
  };
  const store = createPendingDepositStore(storage);

  assert.equal(store.save(record(1, 'a')), false);
  assert.deepEqual(store.load(), []);
  assert.equal(store.remove(record(1, 'a')), true);
});
