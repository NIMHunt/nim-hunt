const NIMIQ_TRANSACTION_HASH_RE = /^[0-9a-f]{64}$/i;
const DEFAULT_MAX_HEAD_DIFFERENCE = 120;
const DEFAULT_CONSENSUS_ATTEMPTS = 5;
const DEFAULT_CONSENSUS_RETRY_DELAY_MS = 750;

function providerErrorMessage(value) {
    const error = value?.error;
    if (!error) return '';
    if (typeof error === 'string') return error.trim();
    if (typeof error !== 'object') return String(error);
    return String(error.message || error.reason || error.code || 'Nimiq Pay rejected the request.');
}

function throwProviderError(value) {
    const message = providerErrorMessage(value);
    if (message) throw new Error(message);
    return value;
}

export function normaliseNimiqTransactionHash(value) {
    if (typeof value !== 'string') {
        throw new Error('Nimiq Pay did not return a transaction hash.');
    }
    const hash = value.trim();
    if (!NIMIQ_TRANSACTION_HASH_RE.test(hash)) {
        throw new Error('Nimiq Pay returned an invalid transaction hash. The deposit was not recorded.');
    }
    return hash.toLowerCase();
}

function requireBlockHeight(value, label) {
    const result = throwProviderError(value);
    const height = Number(result);
    if (!Number.isSafeInteger(height) || height < 0) {
        throw new Error(`${label} did not return a valid block height.`);
    }
    return height;
}

function requireAccounts(value) {
    const result = throwProviderError(value);
    if (!Array.isArray(result) || result.length === 0) {
        throw new Error('Nimiq Pay did not share a funding account.');
    }
    const accounts = result.map((account) => typeof account === 'string' ? account.trim() : '');
    if (accounts.some((account) => !account)) {
        throw new Error('Nimiq Pay returned an invalid funding account.');
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

async function waitForConsensus(provider, options) {
    const attempts = positiveIntegerOption(
        options?.consensusAttempts,
        DEFAULT_CONSENSUS_ATTEMPTS,
    );
    const retryDelayMs = nonNegativeIntegerOption(
        options?.consensusRetryDelayMs,
        DEFAULT_CONSENSUS_RETRY_DELAY_MS,
    );

    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const consensus = throwProviderError(await provider.isConsensusEstablished());
        if (consensus === true) return;
        if (consensus !== false) {
            throw new Error('Nimiq Pay returned an invalid blockchain consensus status.');
        }
        if (attempt + 1 < attempts) await wait(retryDelayMs);
    }

    throw new Error('Nimiq Pay has not established blockchain consensus yet. Please try again shortly.');
}

export async function requestNimiqPayment(provider, intent, options = {}) {
    if (!provider
        || typeof provider.isConsensusEstablished !== 'function'
        || typeof provider.getBlockNumber !== 'function'
        || typeof provider.listAccounts !== 'function'
        || typeof provider.sendBasicTransactionWithData !== 'function') {
        throw new Error('The Nimiq Pay provider is unavailable or incomplete.');
    }

    await waitForConsensus(provider, options);

    const walletHeight = requireBlockHeight(await provider.getBlockNumber(), 'Nimiq Pay');
    const serverHeightValue = intent?.chain_height;
    if (serverHeightValue !== null && serverHeightValue !== undefined) {
        const serverHeight = requireBlockHeight(serverHeightValue, 'NimHunt');
        const maxDifference = Number.isSafeInteger(Number(intent.max_chain_height_difference))
            ? Math.max(1, Number(intent.max_chain_height_difference))
            : DEFAULT_MAX_HEAD_DIFFERENCE;
        if (Math.abs(walletHeight - serverHeight) > maxDifference) {
            throw new Error(
                'Nimiq Pay and NimHunt appear to be connected to different or badly out-of-sync networks. '
                + 'No transaction was requested.'
            );
        }
    }

    const accounts = requireAccounts(await provider.listAccounts());
    const amount = Number(intent?.amount);
    if (!Number.isSafeInteger(amount) || amount <= 0) {
        throw new Error('NimHunt supplied an invalid deposit amount.');
    }

    const result = throwProviderError(await provider.sendBasicTransactionWithData({
        recipient: String(intent?.recipient || '').trim(),
        value: amount,
        data: String(intent?.transaction_description || ''),
    }));

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
