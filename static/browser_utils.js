import { getPreferredLanguage } from './localisation.js';

// Small browser-side helpers shared by NimHunt page modules.
// Keep these helpers free of page-specific state so reusing them cannot change
// a page's behaviour.

const DEVICE_IDENTIFIER_PATTERN = /^[0-9a-fA-F]{64}$/;
const MINI_APP_SDK_URL = 'https://esm.sh/@nimiq/mini-app-sdk';
let miniAppSdkPromise = null;

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
            return identifier.toLowerCase();
        } catch (error) {
            lastError = error;
            if (attempt < retries) await delay(retryDelayMs);
        }
    }
    throw lastError || new Error('Nimiq Pay could not identify this device.');
}
