const NIMIQ_TRANSACTION_HASH_RE = /^[0-9a-f]{64}$/i;
const DEFAULT_MAX_HEAD_DIFFERENCE = 120;
export const NIMIQ_CONSENSUS_GRACE_PERIOD_MS = 15_000;
const DEFAULT_CONSENSUS_RETRY_DELAY_MS = 1_000;
const DEFAULT_CONSENSUS_DIAGNOSTIC_TIMEOUT_MS = 2_000;
const CONSENSUS_DIAGNOSTIC_TIMEOUT = Symbol('consensus-diagnostic-timeout');
const DEFAULT_CONSENSUS_ATTEMPTS = Math.ceil(
    NIMIQ_CONSENSUS_GRACE_PERIOD_MS / DEFAULT_CONSENSUS_RETRY_DELAY_MS,
) + 1;

function providerErrorMessage(value) {
    const error = value?.error;
    if (!error) return '';
    if (typeof error === 'string') return error.trim();
    if (typeof error !== 'object') return String(error);
    return String(error.message || error.reason || error.code || 'Nimiq Pay rejected the request.');
}

function throwProviderError(value, context = 'Nimiq Pay request failed') {
    const message = providerErrorMessage(value);
    if (message) throw new Error(`${context}: ${message}`);
    return value;
}

export function normaliseNimiqTransactionHash(value) {
    if (typeof value !== 'string') {
        throw new Error(
            'Nimiq Pay payment result did not include a transaction hash. '
            + 'The wallet may have completed the payment, so check Nimiq Pay before trying again.'
        );
    }
    const hash = value.trim();
    if (!NIMIQ_TRANSACTION_HASH_RE.test(hash)) {
        throw new Error(
            'Nimiq Pay payment result included an invalid transaction hash. '
            + 'The wallet may have completed the payment, so check Nimiq Pay before trying again.'
        );
    }
    return hash.toLowerCase();
}

function blockHeightOrNull(value) {
    if (providerErrorMessage(value)) return null;
    const height = Number(value);
    return Number.isSafeInteger(height) && height >= 0 ? height : null;
}

function requireBlockHeight(value, label) {
    const result = throwProviderError(value, `${label} blockchain-height check failed`);
    const height = blockHeightOrNull(result);
    if (height === null) {
        throw new Error(
            `${label} blockchain-height check failed: no valid block height was returned. `
            + 'No transaction was requested.'
        );
    }
    return height;
}

function requireAccounts(value) {
    const result = throwProviderError(value, 'Nimiq Pay funding-account request failed');
    if (!Array.isArray(result) || result.length === 0) {
        throw new Error('Nimiq Pay did not share a funding account. No transaction was requested.');
    }
    const accounts = result.map((account) => typeof account === 'string' ? account.trim() : '');
    if (accounts.some((account) => !account)) {
        throw new Error('Nimiq Pay returned an invalid funding account. No transaction was requested.');
    }
    return accounts;
}

function positiveIntegerOption(value, fallback) {
    const number = Number(value);
    return Number.isSafeInteger(number) && number > 0 ? number : fallback;
}

function nonNegativeIntegerOption(value, fallback) {
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 0 ? number : fallback;
}

function wait(delayMs) {
    if (delayMs <= 0) return Promise.resolve();
    return new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function readWithTimeout(readValue, timeoutMs) {
    let timer = null;
    try {
        return await Promise.race([
            Promise.resolve().then(readValue),
            new Promise((resolve) => {
                timer = setTimeout(() => resolve(CONSENSUS_DIAGNOSTIC_TIMEOUT), timeoutMs);
            }),
        ]);
    } finally {
        if (timer !== null) clearTimeout(timer);
    }
}

async function consensusFailureDiagnostics(provider, intent, options) {
    const timeoutMs = positiveIntegerOption(
        options?.consensusDiagnosticTimeoutMs,
        DEFAULT_CONSENSUS_DIAGNOSTIC_TIMEOUT_MS,
    );

    let walletHeight = null;
    try {
        const result = await readWithTimeout(() => provider.getBlockNumber(), timeoutMs);
        if (result !== CONSENSUS_DIAGNOSTIC_TIMEOUT) walletHeight = blockHeightOrNull(result);
    } catch (_err) {
        walletHeight = null;
    }

    const serverHeight = blockHeightOrNull(intent?.chain_height);
    const parts = [
        `Nimiq Pay block: ${walletHeight === null ? 'unavailable' : walletHeight}.`,
        `NimHunt block: ${serverHeight === null ? 'unavailable' : serverHeight}.`,
    ];
    if (walletHeight !== null && serverHeight !== null) {
        parts.push(`Block-height difference: ${Math.abs(walletHeight - serverHeight)}.`);
    }
    return parts.join(' ');
}

async function waitForConsensus(provider, intent, options) {
    const attempts = positiveIntegerOption(
        options?.consensusAttempts,
        DEFAULT_CONSENSUS_ATTEMPTS,
    );
    const retryDelayMs = nonNegativeIntegerOption(
        options?.consensusRetryDelayMs,
        DEFAULT_CONSENSUS_RETRY_DELAY_MS,
    );

    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const consensus = throwProviderError(
            await provider.isConsensusEstablished(),
            'Nimiq Pay consensus check failed',
        );
        if (consensus === true) return;
        if (consensus !== false) {
            throw new Error(
                'Nimiq Pay consensus check returned an invalid status. No transaction was requested.'
            );
        }
        if (attempt + 1 < attempts) await wait(retryDelayMs);
    }

    const waitedSeconds = Math.ceil(Math.max(0, attempts - 1) * retryDelayMs / 1000);
    const waitDescription = waitedSeconds > 0 ? ` after waiting about ${waitedSeconds} seconds` : '';
    const diagnostics = await consensusFailureDiagnostics(provider, intent, options);
    throw new Error(
        `Nimiq Pay is still reporting that it is syncing with the blockchain${waitDescription}. `
        + `${diagnostics} No transaction was requested.`
    );
}

export async function requestNimiqPayment(provider, intent, options = {}) {
    if (!provider
        || typeof provider.isConsensusEstablished !== 'function'
        || typeof provider.getBlockNumber !== 'function'
        || typeof provider.listAccounts !== 'function'
        || typeof provider.sendBasicTransactionWithData !== 'function') {
        throw new Error('Nimiq Pay provider setup failed before payment. No transaction was requested.');
    }

    await waitForConsensus(provider, intent, options);

    const walletHeight = requireBlockHeight(await provider.getBlockNumber(), 'Nimiq Pay');
    const serverHeightValue = intent?.chain_height;
    if (serverHeightValue !== null && serverHeightValue !== undefined) {
        const serverHeight = requireBlockHeight(serverHeightValue, 'NimHunt');
        const maxDifference = Number.isSafeInteger(Number(intent.max_chain_height_difference))
            ? Math.max(1, Number(intent.max_chain_height_difference))
            : DEFAULT_MAX_HEAD_DIFFERENCE;
        if (Math.abs(walletHeight - serverHeight) > maxDifference) {
            throw new Error(
                'Network check failed: Nimiq Pay and NimHunt appear to be connected to different '
                + 'or badly out-of-sync networks. No transaction was requested.'
            );
        }
    }

    const accounts = requireAccounts(await provider.listAccounts());
    const amount = Number(intent?.amount);
    if (!Number.isSafeInteger(amount) || amount <= 0) {
        throw new Error('NimHunt supplied an invalid deposit amount. No transaction was requested.');
    }

    const result = throwProviderError(await provider.sendBasicTransactionWithData({
        recipient: String(intent?.recipient || '').trim(),
        value: amount,
        data: String(intent?.transaction_description || ''),
    }), 'Nimiq Pay payment request was rejected');

    // The documented provider contract returns the hash directly as a string.
    // Never recurse through arbitrary result/data fields: signatures, request IDs
    // and device hashes can also be 64-character hexadecimal strings.
    const txHash = normaliseNimiqTransactionHash(result);
    return {
        txHash,
        fromAddress: accounts[0],
        walletHeight,
    };
}
