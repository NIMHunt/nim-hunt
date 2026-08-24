import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import * as NimiqModule from '@nimiq/core';

const Nimiq = (NimiqModule.default && NimiqModule.default.PublicKey)
  ? NimiqModule.default
  : NimiqModule;
const helper = resolve(import.meta.dirname, 'verify_nimiq_message.mjs');

function signedPayload(message) {
  const privateKey = Nimiq.PrivateKey.generate();
  const publicKey = Nimiq.PublicKey.derive(privateKey);
  const prefix = '\x16Nimiq Signed Message:\n';
  const data = Nimiq.BufferUtils.fromUtf8(`${prefix}${message.length}${message}`);
  const hash = Nimiq.Hash.computeSha256(data);
  const hashBytes = typeof hash.serialize === 'function' ? hash.serialize() : hash;
  const signature = Nimiq.Signature.create(privateKey, publicKey, hashBytes);
  return {
    message,
    public_key: publicKey.toHex(),
    signature: signature.toHex(),
    expectedAddress: publicKey.toAddress().toUserFriendlyAddress(),
  };
}

function runHelper(payload) {
  return spawnSync(process.execPath, [helper], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
  });
}

test('verifies a Nimiq signed-message challenge and derives its signer address', () => {
  const payload = signedPayload(
    'NimHunt claim authentication\nDevice: ' + 'a'.repeat(64) + '\nNonce: ' + 'b'.repeat(64) + '\nIssued: 1700000000',
  );
  const result = runHelper(payload);
  assert.equal(result.status, 0, result.stdout || result.stderr);
  const response = JSON.parse(result.stdout);
  assert.equal(response.ok, true);
  assert.equal(response.address, payload.expectedAddress);
  assert.equal(response.public_key, payload.public_key.toLowerCase());
});

test('rejects a valid signature when the challenge text is changed', () => {
  const payload = signedPayload('NimHunt claim authentication\nNonce: original');
  payload.message = 'NimHunt claim authentication\nNonce: tampered';
  const result = runHelper(payload);
  assert.notEqual(result.status, 0);
  const response = JSON.parse(result.stdout);
  assert.equal(response.ok, false);
  assert.match(response.message, /invalid/i);
});

test('rejects malformed public keys and signatures before verification', () => {
  const result = runHelper({
    message: 'hello',
    public_key: 'aa',
    signature: 'bb',
  });
  assert.notEqual(result.status, 0);
  const response = JSON.parse(result.stdout);
  assert.equal(response.ok, false);
  assert.match(response.message, /32 bytes/i);
});
