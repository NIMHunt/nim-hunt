import { getPreferredLanguage } from './localisation.js';

// Small browser-side helpers shared by NimHunt page modules.
// Keep these helpers free of page-specific state so reusing them cannot change
// a page's behaviour.

const DEVICE_IDENTIFIER_PATTERN = /^[0-9a-fA-F]{64}$/;

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

export async function requestDeviceIdentifierHash(requestDeviceIdentifier, reason) {
    const identifier = await requestDeviceIdentifier({ reason });
    if (typeof identifier !== 'string' || !DEVICE_IDENTIFIER_PATTERN.test(identifier)) {
        throw new Error('Nimiq Pay returned an invalid device identifier.');
    }
    return identifier.toLowerCase();
}
