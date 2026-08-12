import test from 'node:test';
import assert from 'node:assert/strict';
import { requestNimiqPayment } from '../static/nimiq_payment.js';

const HASH = 'ab'.repeat(32);
const INTENT = {
  recipient: 'NQ TEST RECIPIENT',
  amount: 100000,
  transaction_description: 'Funding Test',
  chain_height: 5000,
  max_chain_height_difference: 120,
};

function provider(overrides = {}) {
  const calls = [];
  return {
    calls,
    // Deliberately false: the payment path must not use this stale status as a gate.
    async isConsensusEstablished() { calls.push('consensus'); return false; },
    async getBlockNumber() { calls.push('height'); return 5002; },
    async listAccounts() { calls.push('accounts'); return ['NQ FUNDING']; },
    async sendBasicTransactionWithData(request) { calls.push(['send', request]); return HASH; },
    ...overrides,
  };
}

test('deposit uses the current height without polling the stale consensus flag', async () => {
  const nimiq = provider();
  const payment = await requestNimiqPayment(nimiq, INTENT);

  assert.equal(payment.txHash, HASH);
  assert.equal(payment.fromAddress, 'NQ FUNDING');
  assert.equal(payment.walletHeight, 5002);
  assert.deepEqual(nimiq.calls.slice(0, 2), ['height', 'accounts']);
  assert.equal(nimiq.calls.includes('consensus'), false);
  assert.equal(Object.hasOwn(nimiq.calls[2][1], 'validityStartHeight'), false);
  assert.equal(nimiq.calls.filter(call => Array.isArray(call) && call[0] === 'send').length, 1);
});

test('provider no longer needs an isConsensusEstablished method to request payment', async () => {
  const nimiq = provider();
  delete nimiq.isConsensusEstablished;

  const payment = await requestNimiqPayment(nimiq, INTENT);

  assert.equal(payment.txHash, HASH);
  assert.deepEqual(nimiq.calls.slice(0, 2), ['height', 'accounts']);
  assert.equal(nimiq.calls.filter(call => Array.isArray(call) && call[0] === 'send').length, 1);
});

test('rapid consecutive deposits never pin a stale wallet validity window', async () => {
  let sendCount = 0;
  const nimiq = provider({
    async sendBasicTransactionWithData(request) {
      this.calls.push(['send', request]);
      assert.equal(Object.hasOwn(request, 'validityStartHeight'), false);
      sendCount += 1;
      return (sendCount === 1 ? 'ab' : 'cd').repeat(32);
    },
  });

  const first = await requestNimiqPayment(nimiq, INTENT);
  const second = await requestNimiqPayment(nimiq, INTENT);

  assert.equal(first.txHash, 'ab'.repeat(32));
  assert.equal(second.txHash, 'cd'.repeat(32));
  assert.equal(sendCount, 2);
  assert.equal(nimiq.calls.includes('consensus'), false);
});

test('wrapped result data is never mistaken for a transaction hash', async () => {
  let sendCount = 0;
  const nimiq = provider({
    async sendBasicTransactionWithData() {
      sendCount += 1;
      return { data: HASH };
    },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /did not include a transaction hash.*check Nimiq Pay before trying again/i,
  );
  assert.equal(sendCount, 1);
});

test('provider payment errors identify the failed stage and are never retried', async () => {
  let sendCount = 0;
  const nimiq = provider({
    async sendBasicTransactionWithData() {
      sendCount += 1;
      return { error: { message: 'Broadcast rejected' } };
    },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /Nimiq Pay payment request was rejected: Broadcast rejected/,
  );
  assert.equal(sendCount, 1);
});

test('non-string account responses stop before requesting a payment', async () => {
  const nimiq = provider({
    async listAccounts() { this.calls.push('accounts'); return [{ address: 'NQ FUNDING' }]; },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /invalid funding account.*No transaction was requested/i,
  );
  assert.deepEqual(nimiq.calls, ['height', 'accounts']);
});

test('network/head mismatch stops before account sharing and payment', async () => {
  const nimiq = provider({
    async getBlockNumber() { this.calls.push('height'); return 900000; },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /Network check failed:.*different.*out-of-sync networks.*No transaction was requested/is,
  );
  assert.deepEqual(nimiq.calls, ['height']);
});

test('wallet height provider errors stop before account sharing and payment', async () => {
  const nimiq = provider({
    async getBlockNumber() {
      this.calls.push('height');
      return { error: { message: 'No peers available' } };
    },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /Nimiq Pay blockchain-height check failed: No peers available/i,
  );
  assert.deepEqual(nimiq.calls, ['height']);
});

test('invalid wallet heights stop before account sharing and payment', async () => {
  const nimiq = provider({
    async getBlockNumber() { this.calls.push('height'); return undefined; },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /Nimiq Pay blockchain-height check failed: no valid block height was returned.*No transaction was requested/i,
  );
  assert.deepEqual(nimiq.calls, ['height']);
});
