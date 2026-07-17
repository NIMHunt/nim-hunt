import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const helper = resolve(import.meta.dirname, 'nimiq_helper.mjs');
const PUBLIC_DEFAULT = 'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';
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
      NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC: '0',
      NIMHUNT_PRODUCTION: '',
      ...extraEnvironment,
    },
  });
}

test('public-testnet rejects the public default mnemonic flag', () => {
  const result = runHelper({
    NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
    NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC: '1',
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /public default test mnemonic/i);
});

test('public-testnet rejects the public default mnemonic when supplied directly', () => {
  const result = runHelper({
    NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
    NIMHUNT_NIMIQ_MNEMONIC: PUBLIC_DEFAULT,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /public default test mnemonic/i);
});
