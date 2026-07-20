/*
 * Deposit recording repair.
 *
 * The blockchain payment and the HTTP call that records its hash are separate
 * operations. A failed/lost HTTP response must be retried with the same hash;
 * it must never send a second Nimiq transaction.
 */

const DEPOSIT_SUBMITTED_PATTERN = /^\/api\/my-spots\/(\d+)\/deposit-submitted$/;
const RETRYABLE_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);
const RECORDED_CONFLICT_CODES = new Set(['deposit_pending', 'deposit_covered']);
const STORAGE_KEY = 'nimhunt.pendingDepositSubmission.v1';
const MAX_STORED_AGE_MS = 24 * 60 * 60 * 1000;
const MAX_RECOVERY_ATTEMPTS = 3;

let installed = false;
let downstreamFetch = null;
let providerPatchPromise = null;

function requestUrl(input) {
    try {
        return new URL(input instanceof Request ? input.url : String(input), window.location.origin);
    } catch (_err) {
        return null;
    }
}

function requestMethod(input, init = {}) {
    return String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
}

function extractAddress(value, seen = new Set()) {
    if (typeof value === 'string') return value.trim();
    if (!value || typeof value !== 'object' || seen.has(value)) return '';
    seen.add(value);

    if (Array.isArray(value)) {
        for (const item of value) {
            const address = extractAddress(item, seen);
            if (address) return address;
        }
        return '';
    }

    for (const key of ['address', 'accountAddress', 'account_address', 'account', 'result', 'data']) {
        if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
        const address = extractAddress(value[key], seen);
        if (address) return address;
    }
    return '';
}

function providerErrorMessage(value) {
    const error = value?.error;
    if (!error) return '';
    if (typeof error === 'string') return error.trim();
    if (typeof error !== 'object') return String(error);
    return String(error.message || error.reason || error.code || 'Nimiq Pay rejected the request.');
}

function accountList(value) {
    const source = Array.isArray(value)
        ? value
        : value?.accounts ?? value?.result ?? value?.data;
    if (!Array.isArray(source)) return [];
    return source.map((account) => extractAddress(account)).filter(Boolean);
}

function extractTransactionHash(value, seen = new Set()) {
    if (typeof value === 'string') return value.trim();
    if (!value || typeof value !== 'object' || seen.has(value)) return '';
    seen.add(value);

    for (const key of [
        'txHash',
        'tx_hash',
        'hash',
        'transactionHash',
        'transaction_hash',
        'result',
        'transaction',
        'data',
    ]) {
        if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
        const hash = extractTransactionHash(value[key], seen);
        if (hash) return hash;
    }
    return '';
}

function replaceProviderMethod(provider, name, replacement) {
    try {
        provider[name] = replacement;
        return provider[name] === replacement;
    } catch (_err) {
        return false;
    }
}

async function patchNimiqProvider() {
    if (providerPatchPromise) return providerPatchPromise;

    providerPatchPromise = (async () => {
        try {
            // Use the same module specifier as my_spots.js so both imports share
            // the SDK's provider singleton inside the Nimiq Pay WebView.
            const { init } = await import('https://esm.sh/@nimiq/mini-app-sdk');
            const provider = await init({ timeout: 10_000 });
            if (!provider || provider.__nimhuntDepositResponsePatch) return;

            const originalListAccounts = typeof provider.listAccounts === 'function'
                ? provider.listAccounts.bind(provider)
                : null;
            if (originalListAccounts) {
                replaceProviderMethod(provider, 'listAccounts', async (...args) => {
                    const result = await originalListAccounts(...args);
                    const error = providerErrorMessage(result);
                    if (error) throw new Error(error);
                    const accounts = accountList(result);
                    return accounts.length > 0 ? accounts : result;
                });
            }

            const originalSend = typeof provider.sendBasicTransactionWithData === 'function'
                ? provider.sendBasicTransactionWithData.bind(provider)
                : null;
            if (originalSend) {
                replaceProviderMethod(provider, 'sendBasicTransactionWithData', async (...args) => {
                    const result = await originalSend(...args);
                    const error = providerErrorMessage(result);
                    if (error) throw new Error(error);
                    const txHash = extractTransactionHash(result);
                    return txHash || result;
                });
            }

            try {
                Object.defineProperty(provider, '__nimhuntDepositResponsePatch', {
                    value: true,
                    configurable: false,
                });
            } catch (_err) {
                // A frozen provider can still have had writable methods replaced.
            }
        } catch (err) {
            console.warn('Could not prepare the Nimiq Pay deposit provider.', err);
            providerPatchPromise = null;
        }
    })();

    return providerPatchPromise;
}

function parsedJsonBody(init = {}) {
    if (typeof init.body !== 'string' || !init.body.trim()) return null;
    try {
        const data = JSON.parse(init.body);
        return data && typeof data === 'object' ? data : null;
    } catch (_err) {
        return null;
    }
}

function prepareDepositSubmission(input, init = {}) {
    const url = requestUrl(input);
    if (!url || url.origin !== window.location.origin) return null;
    if (requestMethod(input, init) !== 'POST') return null;

    const match = DEPOSIT_SUBMITTED_PATTERN.exec(url.pathname);
    if (!match) return null;

    const body = parsedJsonBody(init);
    if (!body || !String(body.tx_hash || '').trim()) return null;

    const normalised = {
        ...body,
        from_address: extractAddress(body.from_address) || null,
    };
    return {
        url: `${url.pathname}${url.search}${url.hash}`,
        spotId: Number.parseInt(match[1], 10),
        body: normalised,
        init: {
            ...init,
            method: 'POST',
            headers: {
                ...(init.headers || {}),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(normalised),
        },
    };
}

function savePendingSubmission(submission, attempts = 0) {
    try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
            url: submission.url,
            spotId: submission.spotId,
            body: submission.body,
            attempts,
            createdAt: Date.now(),
        }));
    } catch (_err) {
        // Private WebViews may disable sessionStorage. The live retry still works.
    }
}

function clearPendingSubmission() {
    try {
        sessionStorage.removeItem(STORAGE_KEY);
    } catch (_err) {
        // Nothing else is required.
    }
}

function readPendingSubmission() {
    try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data?.url || !data?.body?.tx_hash) return null;
        if ((Date.now() - Number(data.createdAt || 0)) > MAX_STORED_AGE_MS) {
            clearPendingSubmission();
            return null;
        }
        if (Number(data.attempts || 0) >= MAX_RECOVERY_ATTEMPTS) return null;
        return data;
    } catch (_err) {
        clearPendingSubmission();
        return null;
    }
}

function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function conflictMeansAlreadyRecorded(response) {
    if (response.status !== 409) return false;
    const data = await response.clone().json().catch(() => ({}));
    return RECORDED_CONFLICT_CODES.has(String(data?.code || ''));
}

async function successfulOrRecoveredResponse(response) {
    if (response.ok) return response;
    if (!await conflictMeansAlreadyRecorded(response)) return response;

    const headers = new Headers(response.headers);
    headers.delete('content-length');
    headers.set('content-type', 'application/json');
    return new Response(JSON.stringify({
        ok: true,
        recovered: true,
        message: 'This deposit is already being tracked.',
    }), {
        status: 200,
        headers,
    });
}

async function submitRecording(input, submission) {
    savePendingSubmission(submission);

    let response;
    try {
        response = await downstreamFetch(input, submission.init);
    } catch (_firstError) {
        await wait(650);
        response = await downstreamFetch(input, submission.init);
    }

    response = await successfulOrRecoveredResponse(response);
    if (!response.ok && RETRYABLE_STATUSES.has(response.status)) {
        await wait(650);
        response = await downstreamFetch(input, submission.init);
        response = await successfulOrRecoveredResponse(response);
    }

    if (response.ok) clearPendingSubmission();
    return response;
}

function putNoticeAboveDepositCard() {
    const noticeBackdrop = document.getElementById('notice-backdrop');
    const depositBackdrop = document.getElementById('deposit-spot-backdrop');
    if (!noticeBackdrop || noticeBackdrop.hidden || !depositBackdrop || depositBackdrop.hidden) return;

    // The former flow left both full-screen backdrops open. The later notice was
    // underneath the deposit card, so the user could only reveal it by pressing
    // Cancel. Close the obsolete card as soon as a notice is presented.
    depositBackdrop.hidden = true;
}

function observeNoticeLayering() {
    const noticeBackdrop = document.getElementById('notice-backdrop');
    if (!noticeBackdrop) return;

    const observer = new MutationObserver(putNoticeAboveDepositCard);
    observer.observe(noticeBackdrop, { attributes: true, attributeFilter: ['hidden'] });
    putNoticeAboveDepositCard();
}

async function recoverStoredSubmission() {
    const stored = readPendingSubmission();
    if (!stored || !downstreamFetch) return;

    const attempts = Number(stored.attempts || 0) + 1;
    savePendingSubmission(stored, attempts);
    try {
        let response = await downstreamFetch(stored.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...stored.body,
                from_address: extractAddress(stored.body.from_address) || null,
            }),
        });
        response = await successfulOrRecoveredResponse(response);
        if (!response.ok) return;

        clearPendingSubmission();
        // Refresh only the data-bearing page. This does not reopen Nimiq Pay and
        // therefore cannot create a second blockchain payment.
        if (window.location.pathname === '/my-spots') window.location.reload();
    } catch (_err) {
        // Keep the stored hash for a later page load, up to the attempt cap.
    }
}

export function installDepositFlowRepair() {
    if (installed || window.location.pathname !== '/my-spots') return;
    installed = true;
    downstreamFetch = window.fetch.bind(window);

    window.fetch = function depositAwareFetch(input, init = {}) {
        const submission = prepareDepositSubmission(input, init);
        if (!submission) return downstreamFetch(input, init);
        return submitRecording(input, submission);
    };

    patchNimiqProvider();
    observeNoticeLayering();
    window.setTimeout(recoverStoredSubmission, 0);
}
