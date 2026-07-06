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

  const allowDefaultTestMnemonic = env('NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC', '0') === '1';
  if (allowDefaultTestMnemonic && network !== 'MainAlbatross') {
    return DEFAULT_TEST_MNEMONIC;
  }

  throw new Error('Set NIMHUNT_NIMIQ_MNEMONIC before deriving or sending real Nimiq transactions. For TestAlbatross-only experiments, you may set NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC=1.');
}

function ensureNotUnsafeDefault(network) {
  if (network === 'MainAlbatross' && env('NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC', '0') === '1' && !env('NIMHUNT_NIMIQ_MNEMONIC')) {
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
  ensureNotUnsafeDefault(network);

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
    const networkId = typeof client.getNetworkId === 'function'
      ? await client.getNetworkId()
      : Number(payload.network_id ?? 6);

    const tx = Nimiq.TransactionBuilder.newBasic(
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

  if (action === 'send_luna_from_spot_deposit') {
    await sendLunaFromSpotDeposit(payload);
    return;
  }

  throw new Error(`Unsupported action: ${action || '(missing)'}`);
}

main().catch(fail);
