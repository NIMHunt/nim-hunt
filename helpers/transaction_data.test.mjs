import assert from 'node:assert/strict';
import test from 'node:test';

import * as NimiqModule from '@nimiq/core';
import { encodeTransactionMemo } from './transaction_data.mjs';

const Nimiq = (NimiqModule.default && NimiqModule.default.Client)
  ? NimiqModule.default
  : NimiqModule;

const TEST_MNEMONIC = 'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';

test('memo encoding is trimmed, UTF-8, and bounded', () => {
  assert.deepEqual(encodeTransactionMemo(null), new Uint8Array());
  assert.equal(new TextDecoder().decode(encodeTransactionMemo('  Claim: Test  ')), 'Claim: Test');
  assert.throws(() => encodeTransactionMemo('x'.repeat(65)), /64 UTF-8 bytes/);
});

test('@nimiq/core builds a signed basic transaction with description data', () => {
  const root = Nimiq.MnemonicUtils.mnemonicToExtendedPrivateKey(TEST_MNEMONIC);
  const sender = Nimiq.KeyPair.derive(root.derivePath("m/44'/242'/0'/0'").privateKey);
  const recipient = Nimiq.KeyPair.derive(root.derivePath("m/44'/242'/1'/0'").privateKey);
  const data = encodeTransactionMemo('Claim: Test Spot');

  const transaction = Nimiq.TransactionBuilder.newBasicWithData(
    sender.toAddress(),
    recipient.toAddress(),
    data,
    1000n,
    0n,
    1,
    6,
  );
  transaction.sign(sender);
  assert.doesNotThrow(() => transaction.verify(6));
});
