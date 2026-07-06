import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { COMMON_TEXT, REPORT_REASON_OPTIONS, makeSpotDetailText } from './interface_text.js?v=remove-help-pages-v1-20260705';
import { formatNimFromLuna } from './nim_format.js';
const els = {
    data: document.getElementById('spot-data'),
    list: document.getElementById('spot-detail-list'),
    fallback: document.getElementById('spot-detail-fallback'),

    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),

    reportBackdrop: document.getElementById('report-backdrop'),
    reportForm: document.getElementById('report-form'),
    reportTitle: document.getElementById('report-title'),
    reportSpotName: document.getElementById('report-spot-name'),
    reportReason: document.getElementById('report-reason-select'),
    reportDetails: document.getElementById('report-details-input'),
    reportDetailsLimit: document.getElementById('report-details-limit'),
    reportCaptchaQuestion: document.getElementById('report-captcha-question'),
    reportCaptchaInput: document.getElementById('report-captcha-input'),
    reportConfirm: document.getElementById('report-confirm'),
    reportCancel: document.getElementById('report-cancel'),
    reportError: document.getElementById('report-error'),
};

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';
const REPORT_DETAILS_MAX = Number.parseInt(document.body.dataset.reportDetailsMax || '300', 10);

const TEXT = makeSpotDetailText({
    appName: APP_NAME,
    nimiqPayUrl: NIMIQ_PAY_URL,
});

const state = {
    spot: null,
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    reportIdentityReady: false,
    reportIdentityPromise: null,
    reportControls: [],
    reportSubmitting: false,
    captchaA: 0,
    captchaB: 0,
    claimCodesLine: null,
    claimCodesToggle: null,
    claimCodesPanel: null,
    claimCodesLoaded: false,
    claimCodesLoading: false,
};

function showNotice({ title, body, href = null, linkText = COMMON_TEXT.notice.readMore, buttonText = COMMON_TEXT.notice.ok }) {
    if (!els.noticeBackdrop) return;

    els.noticeTitle.textContent = title;
    els.noticeBody.textContent = body;
    els.noticeOk.textContent = buttonText;

    if (href) {
        els.noticeLink.textContent = linkText;
        els.noticeLink.href = href;
        els.noticeLink.hidden = false;
    } else {
        els.noticeLink.hidden = true;
        els.noticeLink.removeAttribute('href');
    }

    els.noticeBackdrop.hidden = false;
}

function launchPublishConfetti() {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;

    const confetti = document.createElement('div');
    confetti.className = 'nh-confetti';
    confetti.setAttribute('aria-hidden', 'true');

    const pieceCount = 34;
    for (let index = 0; index < pieceCount; index += 1) {
        const piece = document.createElement('span');
        const angle = ((Math.PI * 2) * index / pieceCount) + ((Math.random() - 0.5) * 0.58);
        const distance = 24 + Math.random() * 36;
        const drift = (Math.random() - 0.5) * 12;

        piece.style.setProperty('--tx', `${Math.cos(angle) * distance}vmin`);
        piece.style.setProperty('--ty', `${Math.sin(angle) * distance}vmin`);
        piece.style.setProperty('--drift', `${drift}vmin`);
        piece.style.setProperty('--delay', `${Math.random() * 70}ms`);
        piece.style.setProperty('--duration', `${780 + Math.random() * 360}ms`);
        piece.style.setProperty('--rotation', `${Math.round(Math.random() * 720 - 360)}deg`);
        piece.style.setProperty('--size', `${7 + Math.random() * 7}px`);
        piece.className = index % 3 === 0 ? 'is-gold' : (index % 3 === 1 ? 'is-green' : 'is-blue');
        confetti.append(piece);
    }

    document.body.append(confetti);
    window.setTimeout(() => confetti.remove(), 1250);
}

function publishCelebrationRequested() {
    return new URLSearchParams(window.location.search).get('published') === '1';
}

function clearPublishCelebrationParam() {
    const url = new URL(window.location.href);
    if (!url.searchParams.has('published')) return;
    url.searchParams.delete('published');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
}

function maybeLaunchPublishCelebration() {
    if (!publishCelebrationRequested()) return;
    clearPublishCelebrationParam();

    window.requestAnimationFrame(() => {
        window.setTimeout(launchPublishConfetti, 160);
    });
}

function ensureReportTooltip() {
    let tooltip = document.getElementById('report-lock-tooltip');
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'report-lock-tooltip';
        tooltip.className = 'lock-tooltip report-lock-tooltip';
        tooltip.setAttribute('role', 'tooltip');
        tooltip.hidden = true;
        document.body.append(tooltip);
    }
    return tooltip;
}

function hideReportTooltip() {
    const tooltip = document.getElementById('report-lock-tooltip');
    if (!tooltip) return;
    tooltip.hidden = true;
    tooltip.textContent = '';
    tooltip.removeAttribute('data-placement');
}

function showReportTooltip(target, text) {
    if (!target || !text) return;
    const tooltip = ensureReportTooltip();
    tooltip.textContent = text;
    tooltip.hidden = false;
    tooltip.dataset.placement = 'top';

    window.requestAnimationFrame(() => {
        const gap = 12;
        const edgePadding = 12;
        const targetRect = target.getBoundingClientRect();
        const tooltipRect = tooltip.getBoundingClientRect();

        let placement = 'top';
        let top = targetRect.top - tooltipRect.height - gap;
        if (top < edgePadding) {
            placement = 'bottom';
            top = targetRect.bottom + gap;
        }

        let left = targetRect.left + targetRect.width / 2 - tooltipRect.width / 2;
        left = Math.max(edgePadding, Math.min(left, window.innerWidth - tooltipRect.width - edgePadding));

        tooltip.style.left = `${Math.round(left)}px`;
        tooltip.style.top = `${Math.round(top)}px`;
        tooltip.dataset.placement = placement;
    });
}

function ensureReportConfirmTooltipTarget() {
    if (!els.reportConfirm) return null;
    const existing = els.reportConfirm.closest('.report-confirm-tooltip-target');
    if (existing) return existing;

    const wrap = document.createElement('span');
    wrap.className = 'report-confirm-tooltip-target';
    els.reportConfirm.before(wrap);
    wrap.append(els.reportConfirm);

    wrap.addEventListener('mouseenter', () => {
        if (reportBlockedByMissingDevice()) {
            showReportTooltip(wrap, TEXT.report.noDeviceTooltip);
        }
    });
    wrap.addEventListener('focusin', () => {
        if (reportBlockedByMissingDevice()) {
            showReportTooltip(wrap, TEXT.report.noDeviceTooltip);
        }
    });
    wrap.addEventListener('mouseleave', hideReportTooltip);
    wrap.addEventListener('focusout', hideReportTooltip);
    wrap.addEventListener('touchstart', () => {
        if (!reportBlockedByMissingDevice()) return;
        showReportTooltip(wrap, TEXT.report.noDeviceTooltip);
        window.setTimeout(hideReportTooltip, 1800);
    }, { passive: true });

    return wrap;
}

function reportBlockedByMissingDevice() {
    return !state.user;
}

function syncReportConfirmTooltipState() {
    const wrap = ensureReportConfirmTooltipTarget();
    if (!wrap || !els.reportConfirm) return;
    wrap.classList.toggle('is-tooltip-locked', reportBlockedByMissingDevice() && els.reportConfirm.disabled);
}

function setReportError(message) {
    if (!els.reportError) return;
    if (message) {
        els.reportError.textContent = message;
        els.reportError.hidden = false;
    } else {
        els.reportError.textContent = '';
        els.reportError.hidden = true;
    }
}


function currentReportDetailsInput(candidate = null) {
    if (candidate?.id === 'report-details-input') return candidate;
    return document.getElementById('report-details-input') || els.reportDetails;
}

function currentReportDetailsLimit() {
    return document.getElementById('report-details-limit') || els.reportDetailsLimit;
}

function updateReportDetailsLimit(candidate = null) {
    const limitEl = currentReportDetailsLimit();
    if (!limitEl) return;

    const input = currentReportDetailsInput(candidate);
    const maxLength = Number(input?.maxLength || REPORT_DETAILS_MAX);
    const sensibleMax = Number.isFinite(maxLength) && maxLength > 0 ? maxLength : REPORT_DETAILS_MAX;
    const used = Number(input?.value?.length || 0);
    const remaining = Math.max(0, sensibleMax - used);

    limitEl.textContent = TEXT.report.detailsLimit(remaining);
    limitEl.setAttribute('aria-live', 'polite');
}
function responseErrorText(data, fallback = TEXT.report.failed.body) {
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
        const messages = detail.map((item) => item?.msg || item?.message || item?.detail).filter(Boolean);
        if (messages.length > 0) return messages.join(' ');
    }
    if (typeof data?.message === 'string' && data.message.trim()) return data.message;
    return fallback;
}

function getLanguage() {
    const payLanguage = window.nimiqPay?.language;
    if (typeof payLanguage === 'string' && payLanguage.length > 0) return payLanguage;

    const browserLanguage = navigator.language || navigator.userLanguage;
    if (typeof browserLanguage === 'string' && browserLanguage.length > 0) {
        return browserLanguage.split('-')[0];
    }

    return 'en';
}

async function requestWalletDeviceId() {
    try {
        const id = await requestDeviceIdentifier({
            reason: TEXT.nimiqPay.deviceIdReason,
        });

        if (typeof id === 'string' && /^[0-9a-fA-F]{64}$/.test(id)) {
            state.walletAvailable = true;
            state.deviceIdHash = id.toLowerCase();
            return true;
        }

        throw new Error('Nimiq Pay returned an invalid device identifier.');
    } catch (err) {
        state.walletAvailable = false;
        state.deviceIdHash = null;
        return false;
    }
}

async function identifyReportUser() {
    if (state.reportIdentityPromise) return state.reportIdentityPromise;

    state.reportIdentityPromise = (async () => {
        if (!state.walletAvailable || !state.deviceIdHash) {
            await requestWalletDeviceId();
        }

        try {
            const data = await fetchJson('/api/home/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(authPayload()),
            });
            state.user = data.user || null;
            if (data.test_user) state.walletAvailable = true;
        } catch (err) {
            state.user = null;
        } finally {
            state.reportIdentityReady = true;
            updateReportControlVisibility();
            updateReportConfirmState();
            maybeLoadOwnerClaimCodes();
        }

        return state.user;
    })();

    return state.reportIdentityPromise;
}

function currentUserOwnsSpot(spot) {
    const userId = Number(state.user?.id);
    const creatorId = Number(spot?.created_by);
    return Number.isFinite(userId) && Number.isFinite(creatorId) && userId === creatorId;
}

function updateReportControlVisibility() {
    for (const { line, spot } of state.reportControls) {
        line.hidden = !state.reportIdentityReady || currentUserOwnsSpot(spot);
    }
}

function authPayload() {
    return {
        device_id_hash: state.deviceIdHash,
        wallet_available: state.walletAvailable,
        language: state.language,
        location_available: false,
        lat: null,
        long: null,
        accuracy: null,
    };
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
        const err = new Error(responseErrorText(data));
        err.data = data;
        err.status = response.status;
        throw err;
    }
    return data;
}

function resetCaptcha() {
    state.captchaA = Math.floor(Math.random() * 9) + 1;
    state.captchaB = Math.floor(Math.random() * 9) + 1;
    if (els.reportCaptchaQuestion) {
        els.reportCaptchaQuestion.textContent = TEXT.report.captchaQuestion({ a: state.captchaA, b: state.captchaB });
    }
    if (els.reportCaptchaInput) {
        els.reportCaptchaInput.value = '';
    }
}

function captchaPassed() {
    const answer = Number.parseInt(String(els.reportCaptchaInput?.value || '').trim(), 10);
    return Number.isFinite(answer) && answer === state.captchaA + state.captchaB;
}

function updateReportConfirmState() {
    if (!els.reportConfirm || state.reportSubmitting) return;
    const reasonSelected = Boolean(els.reportReason?.value);
    const deviceReady = Boolean(state.user);
    els.reportConfirm.disabled = !(deviceReady && reasonSelected && captchaPassed());
    syncReportConfirmTooltipState();
}

function populateReportReasons() {
    if (!els.reportReason) return;
    els.reportReason.replaceChildren();

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = TEXT.report.reasonPlaceholder;
    els.reportReason.append(placeholder);

    for (const reason of REPORT_REASON_OPTIONS) {
        const option = document.createElement('option');
        option.value = String(reason.value);
        option.textContent = reason.label;
        els.reportReason.append(option);
    }
}

function showReportModal(spot) {
    if (!els.reportBackdrop) return;

    state.spot = spot;
    state.reportSubmitting = false;
    populateReportReasons();
    resetCaptcha();
    setReportError(null);

    if (els.reportTitle) els.reportTitle.textContent = TEXT.report.title;
    if (els.reportSpotName) els.reportSpotName.textContent = TEXT.report.spotName(spot.title);
    if (els.reportDetails) {
        els.reportDetails.value = '';
        els.reportDetails.maxLength = REPORT_DETAILS_MAX;
        els.reportDetails.placeholder = TEXT.report.detailsPlaceholder;
    }
    updateReportDetailsLimit();
    window.requestAnimationFrame(updateReportDetailsLimit);
    if (els.reportCaptchaInput) els.reportCaptchaInput.placeholder = TEXT.report.captchaPlaceholder;
    if (els.reportConfirm) {
        els.reportConfirm.textContent = TEXT.report.confirm;
        els.reportConfirm.disabled = true;
    }
    updateReportConfirmState();
    if (els.reportCancel) els.reportCancel.textContent = TEXT.report.cancel;

    els.reportBackdrop.hidden = false;
    requestAnimationFrame(() => els.reportReason?.focus());
}

function hideReportModal() {
    if (!els.reportBackdrop) return;
    hideReportTooltip();
    els.reportBackdrop.hidden = true;
    state.reportSubmitting = false;
    setReportError(null);
}

async function submitReport(event) {
    event.preventDefault();
    if (state.reportSubmitting || !state.spot) return;

    if (!els.reportReason?.value || !captchaPassed()) {
        setReportError(TEXT.report.incomplete);
        updateReportConfirmState();
        return;
    }

    await identifyReportUser();
    if (!state.user) {
        hideReportModal();
        showNotice(TEXT.report.walletUnavailable);
        return;
    }

    state.reportSubmitting = true;
    els.reportConfirm.disabled = true;
    els.reportConfirm.textContent = TEXT.report.confirming;
    setReportError(null);

    try {
        await fetchJson(`/api/spot/${state.spot.id}/report`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...authPayload(),
                reason: Number.parseInt(els.reportReason.value, 10),
                details: els.reportDetails?.value || '',
                captcha_a: state.captchaA,
                captcha_b: state.captchaB,
                captcha_answer: Number.parseInt(String(els.reportCaptchaInput?.value || '0'), 10),
            }),
        });

        hideReportModal();
        showNotice(TEXT.report.submitted);
    } catch (err) {
        const data = err?.data || {};
        if (data.code === 'wallet_unavailable') {
            hideReportModal();
            showNotice(TEXT.report.walletUnavailable);
            return;
        }
        if (data.code === 'already_reported') {
            hideReportModal();
            showNotice(TEXT.report.alreadyReported);
            return;
        }
        if (data.code === 'own_spot') {
            hideReportModal();
            updateReportControlVisibility();
            return;
        }

        state.reportSubmitting = false;
        els.reportConfirm.textContent = TEXT.report.confirm;
        updateReportConfirmState();
        setReportError(err?.message || TEXT.report.failed.body);
        resetCaptcha();
        updateReportConfirmState();
    }
}

async function openReportFlow(spot) {
    await identifyReportUser();

    if (currentUserOwnsSpot(spot)) {
        updateReportControlVisibility();
        return;
    }

    if (!state.user) {
        showReportModal(spot);
        return;
    }

    try {
        const data = await fetchJson(`/api/spot/${spot.id}/report-status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });

        state.user = data.user || state.user;
        updateReportControlVisibility();
        updateReportConfirmState();

        if (data.is_owner) return;
        if (data.already_reported) {
            showNotice(TEXT.report.alreadyReported);
            return;
        }

        showReportModal(spot);
    } catch (err) {
        const data = err?.data || {};
        if (data.code === 'wallet_unavailable') {
            showNotice(TEXT.report.walletUnavailable);
            return;
        }
        showNotice({
            ...TEXT.report.failed,
            body: err?.message || TEXT.report.failed.body,
        });
    }
}

function buildReportControl(spot) {
    const line = document.createElement('p');
    line.className = 'spot-report-line';
    line.hidden = !state.reportIdentityReady || currentUserOwnsSpot(spot);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'spot-report-button';
    button.textContent = TEXT.report.open;
    button.addEventListener('click', () => openReportFlow(spot));

    line.append(button);
    state.reportControls.push({ line, spot });
    return line;
}

function metresToText(value) {
    if (value === null || value === undefined) return null;
    if (value < 1000) return `${Math.round(value)} m away`;
    return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} km away`;
}

function unixToText(value) {
    if (!value) return null;

    const date = new Date(Number(value) * 1000);
    const now = new Date();
    const dateDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const nowDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const daysFromToday = Math.round((dateDay - nowDay) / 86400000);
    const timeText = date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    });

    if (daysFromToday === 0) return `today, ${timeText}`;
    if (daysFromToday === 1) return `tomorrow, ${timeText}`;

    const dateText = date.toLocaleDateString([], {
        day: 'numeric',
        month: 'short',
    });
    return `${dateText}, ${timeText}`;
}


function highestTimeUnitText(seconds, suffix = '') {
    const value = Math.max(0, Math.floor(Number(seconds || 0)));
    if (value <= 60) return `Less than 1 Minute${suffix ? ` ${suffix}` : ''}`;
    const units = [
        ['Week', 7 * 24 * 60 * 60],
        ['Day', 24 * 60 * 60],
        ['Hour', 60 * 60],
        ['Minute', 60],
    ];
    for (const [name, size] of units) {
        if (value >= size) {
            const count = Math.floor(value / size);
            return `${count} ${name}${count === 1 ? '' : 's'}${suffix ? ` ${suffix}` : ''}`;
        }
    }
    return `Less than 1 Minute${suffix ? ` ${suffix}` : ''}`;
}

function spotScheduleTooltip(spot) {
    const starts = unixToText(spot?.starts_at) || 'now';
    const ends = unixToText(spot?.ends_at) || 'no end time';
    return `Active ${starts} until ${ends}`;
}

function spotScheduleSummary(spot) {
    const now = Math.floor(Date.now() / 1000);
    const status = String(spot?.status_label || '').toLowerCase();
    const startsAt = Number(spot?.starts_at || 0);
    const endsAt = Number(spot?.ends_at || 0);
    if (status === 'active' && endsAt > 0) return highestTimeUnitText(Math.max(0, endsAt - now), 'Remaining');
    if (status === 'upcoming' && startsAt > 0) return highestTimeUnitText(Math.max(0, startsAt - now), 'Until Start');
    if ((status === 'ended' || status === 'completed' || endsAt <= now) && endsAt > 0) return `Ended ${unixToText(endsAt) || 'recently'}`;
    return spotScheduleTooltip(spot);
}

function scheduleTextSpan(spot) {
    const span = document.createElement('span');
    span.className = 'spot-time-summary';
    span.textContent = spotScheduleSummary(spot);
    span.title = spotScheduleTooltip(spot);
    span.setAttribute('aria-label', `${span.textContent}. ${span.title}`);
    return span;
}

function spotTypeText(spot) {
    return spot.is_prizedraw ? 'Prizedraw' : 'Spot';
}

function spotStatusText(spot) {
    if (spot.status_label === 'cancelled') return 'Cancelled';
    return spot.status_label === 'upcoming' ? 'Upcoming' : 'Active';
}

function spotStatusClass(spot) {
    if (spot.status_label === 'cancelled') return 'is-cancelled';
    return spot.status_label === 'upcoming' ? 'is-upcoming' : 'is-active';
}

function spotPlaceText(spot) {
    return spot.city || spot.country || 'Unknown area';
}

function spotMetaText(spot) {
    const place = spotPlaceText(spot);
    const distance = metresToText(spot.distance_m);
    return distance ? `${place} - ${distance}` : place;
}

function nimFromLunaText(value) {
    return formatNimFromLuna(value);
}

function nimPerClaimText(spot) {
    const totalValue = Number(spot.total_value || 0);
    const divisor = spot.is_prizedraw
        ? Number(spot.prize_count || 1)
        : Number(spot.max_total_claims || 1);

    return nimFromLunaText(totalValue / Math.max(1, divisor));
}

function durationText(seconds) {
    const value = Number(seconds || 0);
    if (value <= 0) return null;
    if (value < 60) return `${value} sec`;
    if (value < 3600) return `${Math.round(value / 60)} min`;
    if (value < 86400) return `${(value / 3600).toFixed(value % 3600 === 0 ? 0 : 1)} hr`;
    return `${(value / 86400).toFixed(value % 86400 === 0 ? 0 : 1)} days`;
}

function publicSpotUrl(spot) {
    return new URL(spot.href, window.location.origin).toString();
}

function appendParts(el, parts) {
    for (const part of parts) {
        if (part === null || part === undefined || part === '') continue;
        if (part instanceof Node) {
            el.append(part);
        } else {
            el.append(document.createTextNode(String(part)));
        }
    }
}

function appendDetailDescription(container, text) {
    const description = document.createElement('p');
    description.className = 'spot-detail-description';
    description.textContent = text || 'No description provided.';
    container.append(description);
}

function appendBulletLine(list, ...parts) {
    const hasContent = parts.some((part) => {
        if (part instanceof Node) return true;
        return part !== null && part !== undefined && String(part) !== '';
    });
    if (!hasContent) return;

    const line = document.createElement('li');
    line.className = 'spot-detail-line';
    appendParts(line, parts);
    list.append(line);
}

async function copyText(text) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.append(textarea);
    textarea.select();
    document.execCommand('copy');
    textarea.remove();
}

const NIMIQ_ICON_SVG_NS = 'http://www.w3.org/2000/svg';
const NIMIQ_ICON_SPRITE_PATH = '/static/nimiq-style.icons.svg';

function createNimiqInlineIcon(iconName) {
    const safeIconName = String(iconName || '').trim();
    const svg = document.createElementNS(NIMIQ_ICON_SVG_NS, 'svg');
    svg.classList.add('nq-icon', safeIconName, 'nh-inline-nimiq-icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');

    const use = document.createElementNS(NIMIQ_ICON_SVG_NS, 'use');
    const href = `${NIMIQ_ICON_SPRITE_PATH}#${safeIconName}`;
    use.setAttribute('href', href);
    use.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', href);
    svg.append(use);

    return svg;
}

function setCopyButtonIcon(button, iconName) {
    if (!button) return;
    button.replaceChildren(createNimiqInlineIcon(iconName));
}

function buildSpotLinkControl(spot) {
    const wrap = document.createElement('span');
    wrap.className = 'spot-detail-link-row';

    const link = document.createElement('a');
    link.href = spot.href;
    link.className = 'spot-link-anchor';
    link.textContent = spot.link || spot.href;

    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'spot-copy-button';
    copyButton.setAttribute('aria-label', 'Copy spot link');

    setCopyButtonIcon(copyButton, 'nq-copy');

    copyButton.addEventListener('click', async () => {
        try {
            await copyText(publicSpotUrl(spot));
            copyButton.classList.add('is-copied');
            setCopyButtonIcon(copyButton, 'nq-checkmark-small');
            window.setTimeout(() => {
                copyButton.classList.remove('is-copied');
                setCopyButtonIcon(copyButton, 'nq-copy');
            }, 900);
        } catch (err) {
            console.error(err);
        }
    });

    wrap.append(link, copyButton);
    return wrap;
}

function setPasswordCopyButtonIcon(button, iconName) {
    setCopyButtonIcon(button, iconName);
}

function buildClaimCodeCopyButton(code) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'spot-copy-button spot-password-copy-button';
    button.setAttribute('aria-label', TEXT.ownerClaimCodes.copy);
    setPasswordCopyButtonIcon(button, 'nq-copy');

    button.addEventListener('click', async () => {
        try {
            await copyText(code);
            button.classList.add('is-copied');
            button.setAttribute('aria-label', TEXT.ownerClaimCodes.copied);
            setPasswordCopyButtonIcon(button, 'nq-checkmark-small');
            window.setTimeout(() => {
                button.classList.remove('is-copied');
                button.setAttribute('aria-label', TEXT.ownerClaimCodes.copy);
                setPasswordCopyButtonIcon(button, 'nq-copy');
            }, 900);
        } catch (err) {
            console.error(err);
        }
    });

    return button;
}

function buildOwnerClaimCodesLine() {
    const line = document.createElement('li');
    line.className = 'spot-detail-line spot-passwords-line';
    line.hidden = true;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'spot-passwords-toggle disclosure-toggle';
    toggle.textContent = TEXT.ownerClaimCodes.title(0);
    toggle.setAttribute('aria-expanded', 'false');

    const panel = document.createElement('div');
    panel.className = 'spot-passwords-panel';
    panel.hidden = true;

    toggle.addEventListener('click', () => {
        const expanded = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        panel.hidden = expanded;
    });

    line.append(toggle, panel);
    state.claimCodesLine = line;
    state.claimCodesToggle = toggle;
    state.claimCodesPanel = panel;
    return line;
}

function renderOwnerClaimCodes(codes) {
    if (!state.claimCodesLine || !state.claimCodesToggle || !state.claimCodesPanel) return;

    if (!Array.isArray(codes) || codes.length <= 0) {
        state.claimCodesLine.hidden = true;
        return;
    }

    const rows = document.createElement('div');
    rows.className = 'spot-passwords-list';

    for (const item of codes) {
        const row = document.createElement('div');
        row.className = 'spot-password-row';
        row.classList.toggle('is-used', Boolean(item.used));

        const left = document.createElement('span');
        left.className = 'spot-password-left';

        const code = document.createElement('span');
        code.className = 'spot-password-code';
        code.textContent = item.code || '';
        left.append(code);

        if (!item.used && item.code) {
            left.append(buildClaimCodeCopyButton(item.code));
        }

        const right = document.createElement('span');
        right.className = 'spot-password-status';
        right.textContent = item.used
            ? (item.recipient_display_name || `User ${item.recipient_id || ''}`.trim())
            : TEXT.ownerClaimCodes.unused;

        row.append(left, right);
        rows.append(row);
    }

    state.claimCodesToggle.textContent = TEXT.ownerClaimCodes.title(codes.length);
    state.claimCodesPanel.replaceChildren(rows);
    state.claimCodesLine.hidden = false;
}

function hideOwnerClaimCodes() {
    if (state.claimCodesLine) state.claimCodesLine.hidden = true;
}

async function maybeLoadOwnerClaimCodes() {
    if (!state.spot || state.claimCodesLoaded || state.claimCodesLoading) return;
    if (!state.reportIdentityReady || !currentUserOwnsSpot(state.spot)) {
        hideOwnerClaimCodes();
        return;
    }

    state.claimCodesLoading = true;
    try {
        const data = await fetchJson(`/api/spot/${state.spot.id}/claim-codes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });
        state.claimCodesLoaded = true;
        renderOwnerClaimCodes(data.claim_codes || []);
    } catch (err) {
        console.error(err);
        hideOwnerClaimCodes();
    } finally {
        state.claimCodesLoading = false;
    }
}


function badgeColourForSpot(spot) {
    const probe = document.createElement('span');
    probe.className = `spot-badge ${spotStatusClass(spot)}`;
    probe.textContent = '•';
    probe.setAttribute('aria-hidden', 'true');
    probe.style.position = 'fixed';
    probe.style.left = '-9999px';
    probe.style.top = '-9999px';
    probe.style.pointerEvents = 'none';
    document.body.append(probe);

    const style = window.getComputedStyle(probe);
    const colour = style.backgroundColor || style.color || '#21bca5';
    probe.remove();
    return colour;
}

function validSpotCoordinate(spot) {
    const lat = Number(spot.lat);
    const long = Number(spot.long);
    return Number.isFinite(lat) && Number.isFinite(long);
}

function buildSpotMapShell() {
    const map = document.createElement('div');
    map.className = 'spot-detail-map';
    map.setAttribute('aria-label', 'Spot map');
    return map;
}

function makeSpotMapPopup(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    title.textContent = spot.title || 'NimHunt Spot';

    const wrap = document.createElement('div');
    wrap.className = 'nh-spot-popup-content is-clickable';
    wrap.setAttribute('role', 'link');
    wrap.tabIndex = 0;
    wrap.append(title);

    const openSpot = () => {
        if (spot.href) window.location.href = spot.href;
    };

    wrap.addEventListener('click', openSpot);
    wrap.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        openSpot();
    });

    return wrap;
}

function bindSpotMapPopup(layer, spot) {
    layer.bindPopup(makeSpotMapPopup(spot), {
        closeButton: false,
        className: 'nh-spot-popup',
    });
}

function renderLockedSpotMap(mapEl, spot) {
    if (!mapEl || !window.L || !validSpotCoordinate(spot)) return;

    const centre = [Number(spot.lat), Number(spot.long)];
    const colour = badgeColourForSpot(spot);

    const map = window.L.map(mapEl, {
        zoomControl: true,
        attributionControl: true,
        dragging: false,
        touchZoom: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
        boxZoom: false,
        keyboard: false,
        tap: false,
    }).setView(centre, 15);

    window.L.tileLayer(MAP_TILE_URL, {
        maxZoom: 19,
        attribution: MAP_TILE_ATTRIBUTION,
    }).addTo(map);

    const radiusCircle = window.L.circle(centre, {
        radius: Math.max(1, Number(spot.radius || 25)),
        color: colour,
        opacity: 0.9,
        fillColor: colour,
        fillOpacity: 0.18,
        weight: 2.5,
    }).addTo(map);

    const marker = window.L.circleMarker(centre, {
        radius: 7,
        color: '#ffffff',
        fillColor: colour,
        fillOpacity: 1,
        opacity: 1,
        weight: 2,
    }).addTo(map);

    bindSpotMapPopup(radiusCircle, spot);
    bindSpotMapPopup(marker, spot);

    let recentring = false;
    function keepCentred() {
        if (recentring) return;

        const current = map.getCenter();
        const drifted = Math.abs(current.lat - centre[0]) > 0.000001 || Math.abs(current.lng - centre[1]) > 0.000001;
        if (!drifted) return;

        recentring = true;
        map.setView(centre, map.getZoom(), { animate: false });
        recentring = false;
    }

    map.on('moveend zoomend', keepCentred);
    window.setTimeout(() => {
        map.invalidateSize(false);
        keepCentred();
    }, 0);
}

function buildSpotDetail(spot) {
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail';

    const map = buildSpotMapShell();
    detail.append(map);

    const existingClaims = Number(spot.claim_count || 0);
    const maxClaims = Number(spot.max_total_claims || 1);
    const availableClaims = Math.max(0, maxClaims - existingClaims);
    const claimWord = availableClaims === 1 ? 'claim' : 'claims';
    const maxClaimsPerUser = Number(spot.max_claims_per_user ?? 1);
    const duration = durationText(spot.claim_duration);
    const creator = spot.creator_display_name || 'unknown creator';

    appendDetailDescription(detail, spot.description);

    const lines = document.createElement('ul');
    lines.className = 'spot-detail-lines';
    appendBulletLine(lines, `${nimPerClaimText(spot)} Per Claim (${availableClaims} ${claimWord} available)`);

    if (maxClaimsPerUser !== 1) {
        appendBulletLine(
            lines,
            maxClaimsPerUser <= 0 ? 'Unlimited claims per user' : `${maxClaimsPerUser} claims per user`
        );
    }

    appendBulletLine(lines, scheduleTextSpan(spot));

    if (duration) {
        appendBulletLine(lines, `Requires a claim duration of ${duration}`);
    }

    appendBulletLine(lines, buildSpotLinkControl(spot));
    appendBulletLine(lines, `Created by ${creator}`);
    lines.append(buildOwnerClaimCodesLine());
    detail.append(lines);
    detail.append(buildReportControl(spot));

    return { detail, map };
}

function renderSpot(spot) {
    state.spot = spot;
    state.reportControls = [];
    state.claimCodesLine = null;
    state.claimCodesToggle = null;
    state.claimCodesPanel = null;
    state.claimCodesLoaded = false;
    state.claimCodesLoading = false;

    const item = document.createElement('li');
    item.className = 'spot-list-item is-expanded';

    const summary = document.createElement('div');
    summary.className = 'spot-list-toggle spot-detail-static-summary';

    const topRow = document.createElement('span');
    topRow.className = 'spot-list-row spot-list-top-row';

    const title = document.createElement('span');
    title.className = 'spot-list-title';
    title.textContent = spot.title || 'NimHunt Spot';

    const statusBadge = document.createElement('span');
    statusBadge.className = `spot-badge ${spotStatusClass(spot)}`;
    statusBadge.textContent = spotStatusText(spot);

    const actions = document.createElement('span');
    actions.className = 'spot-list-actions';
    actions.append(statusBadge);

    topRow.append(title, actions);

    const bottomRow = document.createElement('span');
    bottomRow.className = 'spot-list-row spot-list-bottom-row';

    const meta = document.createElement('span');
    meta.className = 'spot-list-meta';
    meta.textContent = spotMetaText(spot);

    bottomRow.append(meta);
    summary.append(topRow, bottomRow);

    const { detail, map } = buildSpotDetail(spot);
    detail.hidden = false;

    item.append(summary, detail);
    els.list.replaceChildren(item);
    els.fallback.hidden = true;

    requestAnimationFrame(() => renderLockedSpotMap(map, spot));
}

if (els.noticeOk) {
    els.noticeOk.addEventListener('click', () => {
        els.noticeBackdrop.hidden = true;
    });
}

if (els.reportCancel) {
    els.reportCancel.addEventListener('click', hideReportModal);
}

if (els.reportForm) {
    els.reportForm.addEventListener('submit', submitReport);
}
ensureReportConfirmTooltipTarget();

document.addEventListener('input', (event) => {
    if (event.target?.id === 'report-details-input') updateReportDetailsLimit(event.target);
});

document.addEventListener('change', (event) => {
    if (event.target?.id === 'report-details-input') updateReportDetailsLimit(event.target);
});

document.addEventListener('keyup', (event) => {
    if (event.target?.id === 'report-details-input') updateReportDetailsLimit(event.target);
});

document.addEventListener('paste', (event) => {
    if (event.target?.id === 'report-details-input') {
        window.setTimeout(() => updateReportDetailsLimit(event.target), 0);
    }
});
window.addEventListener('scroll', hideReportTooltip, { passive: true });
window.addEventListener('resize', hideReportTooltip);

for (const input of [els.reportReason, els.reportCaptchaInput]) {
    input?.addEventListener('input', () => {
        setReportError(null);
        updateReportConfirmState();
    });
    input?.addEventListener('change', () => {
        setReportError(null);
        updateReportConfirmState();
    });
}

state.language = getLanguage();

try {
    renderSpot(JSON.parse(els.data.textContent || '{}'));
    identifyReportUser();
    maybeLaunchPublishCelebration();
} catch (err) {
    console.error(err);
    els.fallback.textContent = 'Could not load this spot.';
}
