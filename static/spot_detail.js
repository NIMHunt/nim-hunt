import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { getCommonText, getReportReasonOptions, makeSpotDetailText } from './interface_text.js?v=special-user-badge-v1-20260727';
import { formatNimFromLuna } from './nim_format.js';
import {
    appendBulletLine,
    createUserDisplayName,
    appendDetailDescription,
    appendSpotRequirementIcons,
    appendSpotTitleWithLock,
    buildClaimCodeCopyButton as buildSharedClaimCodeCopyButton,
    buildSpotLinkControl,
    durationText,
    highestTimeUnitText,
    spotScheduleTooltip,
    unixToText,
} from './spot_ui.js?v=chevron-cache-compat-v2-20260813';
import {
    createNoticePresenter,
    getLanguage,
    requestDeviceIdentifierHash,
    responseErrorText as sharedResponseErrorText,
} from './browser_utils.js?v=qol-v1-20260717';
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

const COMMON_TEXT = getCommonText();
const REPORT_REASON_OPTIONS = getReportReasonOptions();
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
    statusTimerId: null,
};

const showNotice = createNoticePresenter(els, {
    defaultLinkText: COMMON_TEXT.notice.readMore,
    defaultButtonText: COMMON_TEXT.notice.ok,
});

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
    return sharedResponseErrorText(data, fallback);
}

async function requestWalletDeviceId() {
    try {
        state.deviceIdHash = await requestDeviceIdentifierHash(
            requestDeviceIdentifier,
            TEXT.nimiqPay.deviceIdReason,
        );
        state.walletAvailable = true;
        return true;
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
    const status = String(spot?.status_label || '').toLowerCase();
    if (status === 'cancelled') return 'Cancelled';
    if (['ended', 'finished', 'completed'].includes(status)) return 'Finished';
    return status === 'upcoming' ? 'Upcoming' : 'Active';
}

function spotStatusClass(spot) {
    const status = String(spot?.status_label || '').toLowerCase();
    if (status === 'cancelled') return 'is-cancelled';
    if (['ended', 'finished', 'completed'].includes(status)) return 'is-finished';
    return status === 'upcoming' ? 'is-upcoming' : 'is-active';
}

function liveSpotStatus(spot) {
    const existing = String(spot?.status_label || '').toLowerCase();
    if (['cancelled', 'ended', 'finished', 'completed'].includes(existing)) return existing;
    const now = Math.floor(Date.now() / 1000);
    const startsAt = Number(spot?.starts_at || 0);
    const endsAt = Number(spot?.ends_at || 0);
    if (endsAt > 0 && now >= endsAt) return 'finished';
    if (startsAt > 0 && now < startsAt) return 'upcoming';
    return 'active';
}

function stopSpotStatusTimer() {
    if (state.statusTimerId) window.clearTimeout(state.statusTimerId);
    state.statusTimerId = null;
}

function updateLiveSpotStatus() {
    const spot = state.spot;
    if (!spot) return;
    spot.status_label = liveSpotStatus(spot);
    document.querySelectorAll('.spot-badge').forEach((badge) => {
        badge.className = `spot-badge ${spotStatusClass(spot)}`;
        badge.textContent = spotStatusText(spot);
    });
    document.querySelectorAll('.spot-time-summary').forEach((span) => {
        span.textContent = spotScheduleSummary(spot);
        span.title = spotScheduleTooltip(spot);
        span.setAttribute('aria-label', `${span.textContent}. ${span.title}`);
    });
    scheduleSpotStatusTransition();
}

function scheduleSpotStatusTransition() {
    stopSpotStatusTimer();
    const spot = state.spot;
    if (!spot) return;
    const now = Math.floor(Date.now() / 1000);
    const candidates = [Number(spot.starts_at || 0), Number(spot.ends_at || 0)]
        .filter((timestamp) => timestamp > now);
    if (!candidates.length) return;
    const delay = Math.min(2147483000, Math.max(250, (Math.min(...candidates) - now) * 1000 + 150));
    state.statusTimerId = window.setTimeout(updateLiveSpotStatus, delay);
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

function buildClaimCodeCopyButton(code) {
    return buildSharedClaimCodeCopyButton(code, TEXT.ownerClaimCodes);
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

function metreBoundsAround(lat, long, radiusMetres) {
        const radius = Math.max(1, Number(radiusMetres || 25));
        const latNum = Number(lat);
        const longNum = Number(long);
        const metresPerDegreeLat = 111320;
        const cosLat = Math.max(0.01, Math.abs(Math.cos(latNum * Math.PI / 180)));
        const latDelta = radius / metresPerDegreeLat;
        const longDelta = radius / (metresPerDegreeLat * cosLat);

        return window.L.latLngBounds(
            [latNum - latDelta, longNum - longDelta],
            [latNum + latDelta, longNum + longDelta]
        );
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
    appendSpotRequirementIcons(title, spot, { interactive: false });

    const text = document.createElement('span');
    text.textContent = spot.title || 'NimHunt Spot';
    title.append(text);

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
        touchZoom: false,
        scrollWheelZoom: false,
        doubleClickZoom: false,
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

    const radiusBounds = metreBoundsAround(centre[0], centre[1], spot.radius).pad(0.18);
    function applyRadiusZoomFloor() {
        map.invalidateSize(false);
        const minZoom = Math.max(0, Math.min(19, map.getBoundsZoom(radiusBounds, false)));
        map.setMinZoom(minZoom);
        if (map.getZoom() < minZoom) {
  map.setView(centre, minZoom, { animate: false });
        } else {
  map.panTo(centre, { animate: false });
        }
    }

    applyRadiusZoomFloor();
    window.setTimeout(applyRadiusZoomFloor, 0);
}

function buildRewardAmountLine(amountText) {
    const fragment = document.createDocumentFragment();
    const amount = document.createElement('strong');
    amount.textContent = amountText;
    fragment.append(amount, document.createTextNode(' Per Claim'));
    return fragment;
}

function buildSpotDetail(spot) {
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail';

    const map = buildSpotMapShell();
    detail.append(map);

    const existingClaims = Number(spot.claim_count || 0);
    const maxClaims = Number(spot.max_total_claims || 1);
    const availableClaims = Math.max(0, maxClaims - existingClaims);
    const maxClaimsPerUser = Number(spot.max_claims_per_user ?? 1);
    const duration = durationText(spot.claim_duration);
    const creator = spot.creator_display_name || 'unknown creator';

    appendDetailDescription(detail, spot.description);

    const lines = document.createElement('ul');
    lines.className = 'spot-detail-lines';
    appendBulletLine(lines, buildRewardAmountLine(nimPerClaimText(spot)));
    appendBulletLine(lines, `${availableClaims} ${availableClaims === 1 ? 'Claim' : 'Claims'} Remaining`);

    if (maxClaimsPerUser !== 1) {
        appendBulletLine(
            lines,
            maxClaimsPerUser <= 0 ? 'Unlimited claims per user' : `${maxClaimsPerUser} claims per user`
        );
    }

    appendBulletLine(lines, scheduleTextSpan(spot));

    if (duration) {
        appendBulletLine(lines, `Must remain on Spot for ${duration}`);
    }

    appendBulletLine(lines, buildSpotLinkControl(spot));
    appendBulletLine(
        lines,
        'Created by ',
        createUserDisplayName(creator, { isSpecial: Boolean(spot.creator_is_special) }),
    );
    lines.append(buildOwnerClaimCodesLine());
    detail.append(lines);
    detail.append(buildReportControl(spot));

    return { detail, map };
}

function renderSpot(spot) {
    spot.status_label = liveSpotStatus(spot);
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
    appendSpotTitleWithLock(title, spot);

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
    scheduleSpotStatusTransition();
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

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') updateLiveSpotStatus();
    else stopSpotStatusTimer();
});
window.addEventListener('pageshow', updateLiveSpotStatus);
window.addEventListener('beforeunload', stopSpotStatusTimer);

state.language = getLanguage();

try {
    renderSpot(JSON.parse(els.data.textContent || '{}'));
    identifyReportUser();
    maybeLaunchPublishCelebration();
} catch (err) {
    console.error(err);
    els.fallback.textContent = 'Could not load this spot.';
}
