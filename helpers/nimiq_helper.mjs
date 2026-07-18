#!/usr/bin/env node
/*
 * NimHunt Nimiq helper.
 *
 * Reads one JSON object from stdin and writes one JSON object to stdout.
 * This helper is intentionally narrow: it derives NimHunt Spot deposit
 * addresses and signs/broadcasts simple NIM transfers from those deposit
 * addresses using @nimiq/core.
 */

import * as NimiqModule from '@nimiq/core';
import { encodeTransactionMemo } from './transaction_data.mjs';

// Some @nimiq/core builds expose a default export, while others expose only
// named exports. A namespace import works with both shapes and avoids startup
// failure when no default export is present.
const Nimiq = (NimiqModule.default && NimiqModule.default.Client) ? NimiqModule.default : NimiqModule;

const DEFAULT_TEST_MNEMONIC = 'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function ok(payload) {
  process.stdout.write(JSON.stringify({ ok: true, ...payload }) + '\n');
}

function fail(error) {
  const message = error && error.message ? error.message : String(error);
  process.stdout.write(JSON.stringify({ ok: false, message }) + '\n');
  process.exitCode = 1;
}

function env(name, fallback = undefined) {
  const value = process.env[name];
  return value === undefined || value === '' ? fallback : value;
}

function networkFromPayload(payload) {
  return String(payload.network || env('NIMHUNT_NIMIQ_NETWORK', 'TestAlbatross'));
}

function mnemonicForNetwork(network) {
  const mnemonic = env('NIMHUNT_NIMIQ_MNEMONIC');
  if (mnemonic) return mnemonic;

  const allowDefaultTestMnemonic = envEnabled('NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC');
  if (allowDefaultTestMnemonic && network !== 'MainAlbatross') {
    return DEFAULT_TEST_MNEMONIC;
  }

  throw new Error('Set NIMHUNT_NIMIQ_MNEMONIC before deriving or sending real Nimiq transactions. For TestAlbatross-only experiments, you may set NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC=1.');
}

const TRUE_VALUES = new Set(['1', 'true', 'yes', 'on']);
const FALSE_VALUES = new Set(['0', 'false', 'no', 'off']);
const NETWORK_IDS = { TestAlbatross: 5, MainAlbatross: 24, DevAlbatross: 6 };

function optionalEnvBoolean(name) {
  const raw = process.env[name];
  if (raw === undefined || String(raw).trim() === '') return null;
  const value = String(raw).trim().toLowerCase();
  if (TRUE_VALUES.has(value)) return true;
  if (FALSE_VALUES.has(value)) return false;
  throw new Error(`${name} must be one of: 1, 0, true, false, yes, no, on, off`);
}

function envEnabled(name) {
  return optionalEnvBoolean(name) === true;
}

function normalisedDeploymentMode() {
  const explicitMode = String(env('NIMHUNT_DEPLOYMENT_MODE', ''))
    .trim()
    .toLowerCase()
    .replaceAll('_', '-');
  const legacyProduction = optionalEnvBoolean('NIMHUNT_PRODUCTION');

  if (explicitMode) {
    if (!['development', 'public-testnet', 'production'].includes(explicitMode)) {
      throw new Error('NIMHUNT_DEPLOYMENT_MODE must be development, public-testnet, or production');
    }
    const modeIsProduction = explicitMode === 'production';
    if (legacyProduction !== null && legacyProduction !== modeIsProduction) {
      throw new Error(`NIMHUNT_DEPLOYMENT_MODE=${explicitMode} conflicts with NIMHUNT_PRODUCTION=${Number(legacyProduction)}`);
    }
    return explicitMode;
  }

  return legacyProduction ? 'production' : 'development';
}

function validateNetworkConfiguration(payload, network) {
  if (!(network in NETWORK_IDS)) throw new Error(`Unsupported Nimiq network: ${network}`);
  const expectedId = NETWORK_IDS[network];
  const configuredId = Number(payload.network_id ?? expectedId);
  if (!Number.isInteger(configuredId) || configuredId !== expectedId) {
    throw new Error(`network_id must be ${expectedId} for ${network}`);
  }

  const deploymentMode = normalisedDeploymentMode();
  if (deploymentMode === 'public-testnet' && network !== 'TestAlbatross') {
    throw new Error('public-testnet requires TestAlbatross with network ID 5');
  }
  if (deploymentMode === 'production' && network !== 'MainAlbatross') {
    throw new Error('production requires MainAlbatross with network ID 24');
  }
  return deploymentMode;
}

function ensureNotUnsafeDefault(network, payload = {}) {
  const deploymentMode = validateNetworkConfiguration(payload, network);
  const publicDeployment = deploymentMode === 'public-testnet' || deploymentMode === 'production';
  const configuredMnemonic = String(env('NIMHUNT_NIMIQ_MNEMONIC', '')).trim().replace(/\s+/g, ' ');
  const defaultEnabled = envEnabled('NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC');
  const explicitlyDefault = configuredMnemonic.toLowerCase() === DEFAULT_TEST_MNEMONIC;

  if (publicDeployment && (defaultEnabled || explicitlyDefault)) {
    throw new Error('Refusing to use the public default test mnemonic in a public NimHunt deployment. Configure a private deployment-specific mnemonic.');
  }
  if (network === 'MainAlbatross' && (defaultEnabled || explicitlyDefault)) {
    throw new Error('Refusing to use the public default test mnemonic on MainAlbatross. Set a private NIMHUNT_NIMIQ_MNEMONIC.');
  }
}

function normalisePath(path) {
  const clean = String(path || '').trim();
  if (!clean) throw new Error('deposit_key_path is required');
  if (!Nimiq.ExtendedPrivateKey.isValidPath(clean)) {
    throw new Error(`Invalid Nimiq HD derivation path: ${clean}`);
  }
  return clean;
}

function parseAddress(address, fieldName = 'address') {
  const clean = String(address || '').trim();
  if (!clean) throw new Error(`${fieldName} is required`);

  if (Nimiq.Address.fromUserFriendlyAddress) {
    return Nimiq.Address.fromUserFriendlyAddress(clean);
  }
  if (Nimiq.Address.fromString) {
    return Nimiq.Address.fromString(clean);
  }
  if (Nimiq.Address.fromAny) {
    return Nimiq.Address.fromAny(clean);
  }
  throw new Error('This @nimiq/core build does not expose a supported Address parser.');
}

function userFriendlyAddress(address) {
  if (address && typeof address.toUserFriendlyAddress === 'function') {
    return address.toUserFriendlyAddress();
  }
  return String(address);
}

function keyPairForPath(payload) {
  const network = networkFromPayload(payload);
  ensureNotUnsafeDefault(network, payload);

  const mnemonic = mnemonicForNetwork(network);
  const password = env('NIMHUNT_NIMIQ_MNEMONIC_PASSWORD', undefined);
  const keyPath = normalisePath(payload.deposit_key_path || payload.key_path);

  const root = Nimiq.MnemonicUtils.mnemonicToExtendedPrivateKey(mnemonic, password);
  const child = root.derivePath(keyPath);
  const keyPair = Nimiq.KeyPair.derive(child.privateKey);
  const address = userFriendlyAddress(keyPair.toAddress());

  return { keyPair, address, keyPath, network };
}

async function createClient(payload) {
  const network = networkFromPayload(payload);
  const config = new Nimiq.ClientConfiguration();
  config.network(network);

  const client = await Nimiq.Client.create(config.build());
  await client.waitForConsensusEstablished();
  return client;
}

async function closeClient(client) {
  for (const method of ['close', 'disconnect', 'free']) {
    if (client && typeof client[method] === 'function') {
      try { await client[method](); } catch (_) { /* best effort */ }
      return;
    }
  }
}

function transactionHash(tx, txDetails = null) {
  const candidates = [
    txDetails?.transactionHash,
    txDetails?.hash,
    txDetails?.txHash,
    typeof tx.hash === 'function' ? tx.hash() : undefined,
  ];
  for (const value of candidates) {
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return '';
}

async function deriveSpotDepositAddress(payload) {
  const { address, keyPath, network } = keyPairForPath({
    ...payload,
    deposit_key_path: payload.key_path || payload.deposit_key_path,
  });

  ok({
    action: 'derive_spot_deposit_address',
    network,
    address,
    key_index: Number(payload.key_index ?? payload.deposit_key_index ?? 0),
    key_path: keyPath,
    key_version: Number(payload.key_version ?? payload.deposit_key_version ?? 1),
  });
}

async function validateSignerConfiguration(payload) {
  const { address, keyPath, network } = keyPairForPath({
    ...payload,
    deposit_key_path: payload.key_path || payload.deposit_key_path,
  });
  ok({
    action: 'validate_signer_configuration',
    network,
    address,
    key_index: Number(payload.key_index ?? payload.deposit_key_index ?? 0),
    key_path: keyPath,
    key_version: Number(payload.key_version ?? payload.deposit_key_version ?? 1),
  });
}

async function sendLunaFromSpotDeposit(payload) {
  const amount = BigInt(payload.amount);
  if (amount <= 0n) throw new Error('amount must be positive');

  const fee = BigInt(payload.fee ?? env('NIMHUNT_NIMIQ_TRANSACTION_FEE', '0'));
  const { keyPair, address: derivedAddress, keyPath, network } = keyPairForPath(payload);

  const expectedFrom = String(payload.from_address || '').trim();
  if (expectedFrom && expectedFrom !== derivedAddress) {
    throw new Error(`Derived address ${derivedAddress} does not match expected from_address ${expectedFrom}`);
  }

  const recipient = parseAddress(payload.to_address, 'to_address');
  const client = await createClient({ ...payload, network });

  try {
    const height = await client.getHeadHeight();
    const fallbackNetworkIds = { TestAlbatross: 5, MainAlbatross: 24, DevAlbatross: 6 };
    const networkId = typeof client.getNetworkId === 'function'
      ? await client.getNetworkId()
      : Number(payload.network_id ?? fallbackNetworkIds[network] ?? 0);

    const data = encodeTransactionMemo(payload.memo);
    const tx = data.byteLength > 0
      ? Nimiq.TransactionBuilder.newBasicWithData(
          keyPair.toAddress(),
          recipient,
          data,
          amount,
          fee,
          height,
          Number(networkId),
        )
      : Nimiq.TransactionBuilder.newBasic(
          keyPair.toAddress(),
          recipient,
          amount,
          fee,
          height,
          Number(networkId),
        );
    tx.sign(keyPair);
    tx.verify(Number(networkId));

    const txDetails = await client.sendTransaction(tx);
    const hash = transactionHash(tx, txDetails);
    if (!hash) throw new Error('Transaction was sent but no transaction hash could be determined.');

    ok({
      action: 'send_luna_from_spot_deposit',
      network,
      tx_hash: hash,
      from_address: derivedAddress,
      to_address: userFriendlyAddress(recipient),
      amount: Number(amount),
      fee: Number(fee),
      memo: String(payload.memo || '').trim() || null,
      deposit_key_path: keyPath,
      validity_start_height: Number(height),
      network_id: Number(networkId),
      raw: txDetails ?? null,
    });
  } finally {
    await closeClient(client);
  }
}

async function main() {
  const raw = await readStdin();
  const payload = JSON.parse(raw || '{}');
  const action = String(payload.action || '').trim();

  if (action === 'derive_spot_deposit_address') {
    await deriveSpotDepositAddress(payload);
    return;
  }
  if (action === 'validate_signer_configuration') {
    await validateSignerConfiguration(payload);
    return;
  }

  if (action === 'send_luna_from_spot_deposit') {
    await sendLunaFromSpotDeposit(payload);
    return;
  }

  throw new Error(`Unsupported action: ${action || '(missing)'}`);
}

main().catch(fail);
