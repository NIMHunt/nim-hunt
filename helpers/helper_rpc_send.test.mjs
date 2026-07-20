import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import http from 'node:http';
import { resolve } from 'node:path';

const helper = resolve(import.meta.dirname, 'nimiq_helper.mjs');
const MNEMONIC = 'legal winner thank year wave sausage worth useful legal winner thank yellow';
const BASE_ENV = {
  ...process.env,
  NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
  NIMHUNT_PRODUCTION: '',
  NIMHUNT_NIMIQ_MNEMONIC: MNEMONIC,
  NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC: '0',
};

function derive(index) {
  const result = spawnSync(process.execPath, [helper], {
    input: JSON.stringify({
      action: 'derive_spot_deposit_address',
      network: 'TestAlbatross',
      network_id: 5,
      key_index: index,
      key_path: `m/44'/242'/${index}'/0'`,
      key_version: 1,
    }),
    encoding: 'utf8',
    env: BASE_ENV,
  });
  assert.equal(result.status, 0, result.stdout || result.stderr);
  return JSON.parse(result.stdout).address;
}

function runHelper(payload) {
  return new Promise((resolveResult) => {
    const child = spawn(process.execPath, [helper], { env: BASE_ENV });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('close', status => resolveResult({ status, stdout, stderr }));
    child.stdin.end(JSON.stringify(payload));
  });
}

test('send helper signs locally and broadcasts through configured JSON-RPC', async () => {
  const sender = derive(0);
  const recipient = derive(1);
  const expectedHash = 'ab'.repeat(32);
  const calls = [];
  const server = http.createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    const rpc = JSON.parse(body);
    calls.push(rpc);
    response.setHeader('content-type', 'application/json');
    if (rpc.method === 'getLatestBlock') {
      response.end(JSON.stringify({ jsonrpc: '2.0', id: rpc.id, result: { data: { number: 123456 } } }));
      return;
    }
    if (rpc.method === 'sendRawTransaction') {
      assert.match(rpc.params[0], /^[0-9a-f]+$/i);
      response.end(JSON.stringify({ jsonrpc: '2.0', id: rpc.id, result: { data: expectedHash } }));
      return;
    }
    response.statusCode = 400;
    response.end(JSON.stringify({ error: { message: 'unexpected method' } }));
  });
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));

  try {
    const address = server.address();
    const result = await runHelper({
      action: 'send_luna_from_spot_deposit',
      network: 'TestAlbatross',
      network_id: 5,
      rpc_url: `http://127.0.0.1:${address.port}`,
      from_address: sender,
      to_address: recipient,
      amount: 100000,
      fee: 0,
      memo: 'NimHunt RPC test',
      deposit_key_index: 0,
      deposit_key_path: "m/44'/242'/0'/0'",
      deposit_key_version: 1,
    });
    assert.equal(result.status, 0, result.stdout || result.stderr);
    const response = JSON.parse(result.stdout);
    assert.equal(response.ok, true);
    assert.equal(response.tx_hash, expectedHash);
    assert.equal(response.broadcast_transport, 'json-rpc');
    assert.ok(response.raw);
    assert.deepEqual(calls.map(call => call.method), ['getLatestBlock', 'sendRawTransaction']);
  } finally {
    await new Promise(resolveClose => server.close(resolveClose));
  }
});
