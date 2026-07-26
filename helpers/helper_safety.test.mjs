import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { generateTestMnemonic } from './test_mnemonic.mjs';

const helper = resolve(import.meta.dirname, 'nimiq_helper.mjs');
const TEST_MNEMONIC = generateTestMnemonic();
const REQUEST = JSON.stringify({
  action: 'derive_spot_deposit_address',
  network: 'TestAlbatross',
  network_id: 5,
  key_index: 0,
  key_path: "m/44'/242'/0'/0'",
  key_version: 1,
});

function runHelper(extraEnvironment) {
  return spawnSync(process.execPath, [helper], {
    input: REQUEST,
    encoding: 'utf8',
    env: {
      ...process.env,
      NIMHUNT_NIMIQ_MNEMONIC: '',
      NIMHUNT_PRODUCTION: '',
      ...extraEnvironment,
    },
  });
}

test('development requires an explicitly supplied mnemonic', () => {
  const result = runHelper({
    NIMHUNT_DEPLOYMENT_MODE: 'development',
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /set NIMHUNT_NIMIQ_MNEMONIC/i);
});

test('public-testnet requires an explicitly supplied mnemonic', () => {
  const result = runHelper({
    NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /set NIMHUNT_NIMIQ_MNEMONIC/i);
});

test('helper rejects unknown deployment modes', () => {
  const result = runHelper({
    NIMHUNT_DEPLOYMENT_MODE: 'staging-ish',
    NIMHUNT_NIMIQ_MNEMONIC: TEST_MNEMONIC,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /must be development, public-testnet, or production/i);
});

test('helper rejects conflicting legacy and preferred deployment settings', () => {
  const result = runHelper({
    NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
    NIMHUNT_PRODUCTION: '1',
    NIMHUNT_NIMIQ_MNEMONIC: TEST_MNEMONIC,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /conflicts with NIMHUNT_PRODUCTION/i);
});

test('helper rejects a network ID that does not match the selected network', () => {
  const result = spawnSync(process.execPath, [helper], {
    input: JSON.stringify({
      action: 'derive_spot_deposit_address',
      network: 'TestAlbatross',
      network_id: 24,
      key_index: 0,
      key_path: "m/44'/242'/0'/0'",
      key_version: 1,
    }),
    encoding: 'utf8',
    env: {
      ...process.env,
      NIMHUNT_DEPLOYMENT_MODE: 'development',
      NIMHUNT_PRODUCTION: '',
      NIMHUNT_NIMIQ_MNEMONIC: TEST_MNEMONIC,
    },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /network_id must be 5 for TestAlbatross/i);
});

test('non-broadcast signer validation derives an address in public-testnet mode', () => {
  const result = spawnSync(process.execPath, [helper], {
    input: JSON.stringify({
      action: 'validate_signer_configuration',
      network: 'TestAlbatross',
      network_id: 5,
      key_index: 0,
      key_path: "m/44'/242'/0'/0'",
      key_version: 1,
    }),
    encoding: 'utf8',
    env: {
      ...process.env,
      NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
      NIMHUNT_PRODUCTION: '',
      NIMHUNT_NIMIQ_MNEMONIC: TEST_MNEMONIC,
    },
  });
  assert.equal(result.status, 0, result.stdout || result.stderr);
  const response = JSON.parse(result.stdout);
  assert.equal(response.ok, true);
  assert.equal(response.action, 'validate_signer_configuration');
  assert.equal(response.network, 'TestAlbatross');
  assert.match(response.address, /^NQ/);
});
