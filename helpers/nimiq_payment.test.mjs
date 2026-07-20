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
    async isConsensusEstablished() { calls.push('consensus'); return true; },
    async getBlockNumber() { calls.push('height'); return 5002; },
    async listAccounts() { calls.push('accounts'); return ['NQ FUNDING']; },
    async sendBasicTransactionWithData(request) { calls.push(['send', request]); return HASH; },
    ...overrides,
  };
}

test('deposit uses the documented direct hash response and current validity height', async () => {
  const nimiq = provider();
  const payment = await requestNimiqPayment(nimiq, INTENT);
  assert.equal(payment.txHash, HASH);
  assert.equal(payment.fromAddress, 'NQ FUNDING');
  assert.equal(nimiq.calls[3][1].validityStartHeight, 5002);
  assert.equal(nimiq.calls.filter(call => Array.isArray(call) && call[0] === 'send').length, 1);
});

test('wrapped result data is never mistaken for a transaction hash', async () => {
  const nimiq = provider({
    async sendBasicTransactionWithData() { return { data: HASH }; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /did not return a transaction hash/);
});

test('provider error objects are surfaced and never recorded', async () => {
  const nimiq = provider({
    async sendBasicTransactionWithData() { return { error: { message: 'Broadcast rejected' } }; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /Broadcast rejected/);
});

test('network/head mismatch stops before account sharing and payment', async () => {
  const nimiq = provider({
    async getBlockNumber() { this.calls.push('height'); return 900000; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /different or badly out-of-sync networks/);
  assert.deepEqual(nimiq.calls, ['consensus', 'height']);
});

test('payment is blocked while wallet consensus is unavailable', async () => {
  const nimiq = provider({
    async isConsensusEstablished() { this.calls.push('consensus'); return false; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /consensus/);
  assert.deepEqual(nimiq.calls, ['consensus']);
});
