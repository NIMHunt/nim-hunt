import test from 'node:test';
import assert from 'node:assert/strict';
import {
  NIMIQ_CONSENSUS_GRACE_PERIOD_MS,
  requestNimiqPayment,
} from '../static/nimiq_payment.js';

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

test('production consensus grace period allows up to fifteen seconds for wallet sync', () => {
  assert.equal(NIMIQ_CONSENSUS_GRACE_PERIOD_MS, 15_000);
});

test('deposit checks the current height but lets Nimiq Pay choose its validity start height', async () => {
  const nimiq = provider();
  const payment = await requestNimiqPayment(nimiq, INTENT);
  assert.equal(payment.txHash, HASH);
  assert.equal(payment.fromAddress, 'NQ FUNDING');
  assert.equal(payment.walletHeight, 5002);
  assert.equal(Object.hasOwn(nimiq.calls[3][1], 'validityStartHeight'), false);
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
});

test('wrapped result data is never mistaken for a transaction hash', async () => {
  const nimiq = provider({
    async sendBasicTransactionWithData() { return { data: HASH }; },
  });
  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /did not include a transaction hash.*check Nimiq Pay before trying again/i,
  );
});

test('provider payment errors identify the failed stage and are never recorded', async () => {
  const nimiq = provider({
    async sendBasicTransactionWithData() { return { error: { message: 'Broadcast rejected' } }; },
  });
  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /Nimiq Pay payment request was rejected: Broadcast rejected/,
  );
});

test('non-string account responses stop before requesting a payment', async () => {
  const nimiq = provider({
    async listAccounts() { this.calls.push('accounts'); return [{ address: 'NQ FUNDING' }]; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /invalid funding account.*No transaction was requested/i);
  assert.deepEqual(nimiq.calls, ['consensus', 'height', 'accounts']);
});

test('network/head mismatch stops before account sharing and payment', async () => {
  const nimiq = provider({
    async getBlockNumber() { this.calls.push('height'); return 900000; },
  });
  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT),
    /Network check failed:.*different.*out-of-sync networks.*No transaction was requested/is,
  );
  assert.deepEqual(nimiq.calls, ['consensus', 'height']);
});

test('temporary wallet consensus loss is retried before requesting a payment', async () => {
  let consensusChecks = 0;
  const nimiq = provider({
    async isConsensusEstablished() {
      this.calls.push('consensus');
      consensusChecks += 1;
      return consensusChecks >= 3;
    },
  });

  const payment = await requestNimiqPayment(nimiq, INTENT, {
    consensusAttempts: 3,
    consensusRetryDelayMs: 0,
  });

  assert.equal(payment.txHash, HASH);
  assert.deepEqual(nimiq.calls.slice(0, 5), ['consensus', 'consensus', 'consensus', 'height', 'accounts']);
  assert.equal(nimiq.calls.filter(call => Array.isArray(call) && call[0] === 'send').length, 1);
});

test('payment remains blocked when wallet consensus never recovers', async () => {
  const nimiq = provider({
    async isConsensusEstablished() { this.calls.push('consensus'); return false; },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT, {
      consensusAttempts: 3,
      consensusRetryDelayMs: 0,
    }),
    /still syncing with the blockchain.*No transaction was requested.*Keep Nimiq Pay open/i,
  );
  assert.deepEqual(nimiq.calls, ['consensus', 'consensus', 'consensus']);
});

test('invalid wallet consensus responses fail closed before chain or account access', async () => {
  const nimiq = provider({
    async isConsensusEstablished() { this.calls.push('consensus'); return undefined; },
  });

  await assert.rejects(
    () => requestNimiqPayment(nimiq, INTENT, { consensusRetryDelayMs: 0 }),
    /consensus check returned an invalid status.*No transaction was requested/i,
  );
  assert.deepEqual(nimiq.calls, ['consensus']);
});
