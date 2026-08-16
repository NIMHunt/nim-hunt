import { getPreferredLanguage } from './localisation.js';
import { createClaimAuthInteractionRetry } from './claim_auth_retry.js';

// Small browser-side helpers shared by NimHunt page modules.
// Keep these helpers free of page-specific state so reusing them cannot change
// a page's behaviour.

const DEVICE_IDENTIFIER_PATTERN = /^[0-9a-fA-F]{64}$/;
const MINI_APP_SDK_URL = 'https://esm.sh/@nimiq/mini-app-sdk';
let miniAppSdkPromise = null;
let claimSecurityPromise = null;
let claimSecurityDeviceId = null;
let claimSecurityInteractionRetry = null;

function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function withTimeout(promise, timeoutMs, message) {
    const timeout = Math.max(1, Number(timeoutMs || 1));
    return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => reject(new Error(message)), timeout);
        Promise.resolve(promise).then(
            (value) => {
                window.clearTimeout(timer);
                resolve(value);
            },
            (error) => {
                window.clearTimeout(timer);
                reject(error);
            },
        );
    });
}

export async function loadNimiqMiniAppSdk({ timeoutMs = 7000, retries = 1, retryDelayMs = 300 } = {}) {
    let lastError = null;
    for (let attempt = 0; attempt <= Math.max(0, Number(retries || 0)); attempt += 1) {
        try {
            if (!miniAppSdkPromise) miniAppSdkPromise = import(MINI_APP_SDK_URL);
            return await withTimeout(
                miniAppSdkPromise,
                timeoutMs,
                'Nimiq Pay did not finish preparing the MiniApp connection.',
            );
        } catch (error) {
            lastError = error;
            if (attempt < retries) await delay(retryDelayMs);
        }
    }
    throw lastError || new Error('Nimiq Pay could not be loaded.');
}

export function getLanguage() {
    // Nimiq Pay's selected language is authoritative for mini apps. When the
    // host does not expose one (for example, an ordinary desktop browser), the
    // interface deliberately falls back to English rather than the device locale.
    return getPreferredLanguage();
}

export function createNoticePresenter(
    {
        noticeBackdrop,
        noticeTitle,
        noticeBody,
        noticeLink,
        noticeOk,
    },
    {
        defaultLinkText = 'Read more',
        defaultButtonText = 'OK',
    } = {},
) {
    return function showNotice({
        title,
        body,
        href = null,
        linkText = defaultLinkText,
        buttonText = defaultButtonText,
    }) {
        if (!noticeBackdrop) return;

        noticeTitle.textContent = title;
        noticeBody.textContent = body;
        noticeOk.textContent = buttonText;

        if (href) {
            noticeLink.textContent = linkText;
            noticeLink.href = href;
            noticeLink.hidden = false;
        } else {
            noticeLink.hidden = true;
            noticeLink.removeAttribute('href');
        }

        noticeBackdrop.hidden = false;
    };
}

export function responseErrorText(data, fallback = 'Request failed.') {
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;

    if (Array.isArray(detail)) {
        const messages = detail
            .map((item) => item?.msg || item?.message || item?.detail)
            .filter(Boolean);
        if (messages.length > 0) return messages.join(' ');
    }

    if (typeof data?.message === 'string' && data.message.trim()) {
        return data.message;
    }

    return fallback;
}

function claimSecurityRequiredOnCurrentPage() {
    const path = String(window.location?.pathname || '');
    return path === '/spots' || path.startsWith('/claim/');
}

function claimSecurityRetrySupportedOnCurrentPage() {
    return String(window.location?.pathname || '') === '/spots';
}

function getClaimSecurityInteractionRetry() {
    if (!claimSecurityRetrySupportedOnCurrentPage()) return null;
    if (claimSecurityInteractionRetry) return claimSecurityInteractionRetry;
    if (typeof document === 'undefined') return null;

    claimSecurityInteractionRetry = createClaimAuthInteractionRetry({
        documentRef: document,
        retry: async (deviceId) => {
            await ensureClaimSecuritySession(deviceId);
            // find_spots.js intentionally caches its first identity bootstrap.
            // Rebuild that page state after a user-driven retry succeeds so the
            // newly authenticated user and claim statuses are immediately used.
            window.location.reload();
        },
    });
    return claimSecurityInteractionRetry;
}

function armClaimSecurityRetryOnInteraction(deviceId) {
    getClaimSecurityInteractionRetry()?.arm(deviceId);
}

function disarmClaimSecurityRetryOnInteraction() {
    claimSecurityInteractionRetry?.disarm();
}

async function securityJson(url, body, fallback) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
        const error = new Error(responseErrorText(data, fallback));
        error.data = data;
        error.status = response.status;
        throw error;
    }
    return data;
}

export async function ensureClaimSecuritySession(deviceIdHash) {
    const deviceId = String(deviceIdHash || '').trim().toLowerCase();
    if (!DEVICE_IDENTIFIER_PATTERN.test(deviceId)) {
        throw new Error('A valid Nimiq Pay device identifier is required for claim verification.');
    }

    if (claimSecurityPromise && claimSecurityDeviceId === deviceId) {
        return claimSecurityPromise;
    }

    claimSecurityDeviceId = deviceId;
    claimSecurityPromise = (async () => {
        const status = await securityJson(
            '/api/security/session',
            { device_id_hash: deviceId },
            'NimHunt could not check your claim-verification session.',
        );
        if (status.authenticated) return status;

        const challenge = await securityJson(
            '/api/security/challenge',
            { device_id_hash: deviceId },
            'NimHunt could not prepare wallet verification.',
        );

        const sdk = await loadNimiqMiniAppSdk({ timeoutMs: 7000, retries: 1 });
        const nimiq = await withTimeout(
            Promise.resolve().then(() => sdk.init()),
            7000,
            'Nimiq Pay did not finish preparing wallet verification.',
        );
        const signed = await withTimeout(
            Promise.resolve().then(() => nimiq.sign(challenge.message)),
            30000,
            'Nimiq Pay did not complete wallet verification in time.',
        );
        if (
            !signed
            || typeof signed.publicKey !== 'string'
            || typeof signed.signature !== 'string'
        ) {
            throw new Error('Nimiq Pay returned an invalid wallet-verification signature.');
        }

        return securityJson(
            '/api/security/verify',
            {
                device_id_hash: deviceId,
                challenge_id: challenge.challenge_id,
                public_key: signed.publicKey,
                signature: signed.signature,
            },
            'NimHunt could not verify your Nimiq account.',
        );
    })();

    try {
        const result = await claimSecurityPromise;
        disarmClaimSecurityRetryOnInteraction();
        return result;
    } catch (error) {
        claimSecurityPromise = null;
        claimSecurityDeviceId = null;
        // /spots performs an eager identity check during page setup. If the user
        // declines that signature or a transient error occurs, its page-level
        // identity promise is already resolved. Arm one user-driven retry on the
        // next Claim/Report interaction instead of prompting on background map
        // refreshes or requiring a manual page reload.
        armClaimSecurityRetryOnInteraction(deviceId);
        throw error;
    }
}

export async function requestDeviceIdentifierHash(
    requestDeviceIdentifier,
    reason,
    { timeoutMs = 7000, retries = 0, retryDelayMs = 300 } = {},
) {
    let lastError = null;
    for (let attempt = 0; attempt <= Math.max(0, Number(retries || 0)); attempt += 1) {
        try {
            const identifier = await withTimeout(
                Promise.resolve().then(() => requestDeviceIdentifier({ reason })),
                timeoutMs,
                'Nimiq Pay did not return a device identifier in time.',
            );
            if (typeof identifier !== 'string' || !DEVICE_IDENTIFIER_PATTERN.test(identifier)) {
                throw new Error('Nimiq Pay returned an invalid device identifier.');
            }
            const cleanIdentifier = identifier.toLowerCase();

            // Device IDs remain useful for continuity, but they are client data
            // and must not be accepted as proof of identity for a payout. On
            // claim-capable pages, bind the device to a server-verified Nimiq
            // signature before returning it to the page module.
            if (claimSecurityRequiredOnCurrentPage()) {
                await ensureClaimSecuritySession(cleanIdentifier);
            }

            return cleanIdentifier;
        } catch (error) {
            lastError = error;
            if (attempt < retries) await delay(retryDelayMs);
        }
    }
    throw lastError || new Error('Nimiq Pay could not identify this device.');
}
