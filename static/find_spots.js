import { init, requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { REPORT_REASON_OPTIONS, makeSpotDetailText } from './interface_text.js?v=claim-polish-v2-20260704';
import {
    appendBulletLine,
    appendDetailDescription,
    appendSpotTitleWithLock,
    buildSpotLinkControl,
    createOwnerClaimCodesControl,
    durationText,
    highestTimeUnitText,
    metresToText,
    spotScheduleTooltip,
    unixToText,
} from './spot_ui.js?v=refactor-v1-20260716';
import { createCaptchaController } from './simple_captcha.js?v=claim-polish-v2-20260704';
import { formatNimFromLuna } from './nim_format.js';
import {
    createNoticePresenter,
    getLanguage,
    requestDeviceIdentifierHash,
    responseErrorText as sharedResponseErrorText,
} from './browser_utils.js?v=refactor-v1-20260716';
const state = {
    map: null,
    spotLayer: null,
    userMarker: null,
    userLat: null,
    userLong: null,
    userAccuracy: null,
    realLat: null,
    realLong: null,
    realAccuracy: null,
    hasUserLocation: false,
    testLocationMode: false,
    lastSpots: [],
    expandedSpotIds: new Set(),
    fetchController: null,
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    reportIdentityReady: false,
    reportIdentityPromise: null,
    reportControls: [],
    reportSpot: null,
    reportSubmitting: false,
    captchaA: 0,
    captchaB: 0,
    ownerClaimCodeControls: [],
    claimStatusBySpotId: new Map(),
    claimSpot: null,
    claimSubmitting: false,
    claimCaptcha: null,
};

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';
const MAX_MAP_INIT_SPOTS = Number.parseInt(document.body.dataset.maxMapInitSpots || '10', 10);
const MAX_MAP_ZOOM_OUT = Number.parseInt(document.body.dataset.maxMapZoomOut || '11', 10);
const CREATE_SPOT_URL = document.body.dataset.createSpotUrl || '/create';
const CLAIM_CAPTCHA_MIN = Number.parseInt(document.body.dataset.claimCaptchaMin || '1', 10);
const CLAIM_CAPTCHA_MAX = Number.parseInt(document.body.dataset.claimCaptchaMax || '9', 10);

const MAP_COLOURS = {
    standard: '#21bca5',
    prizedraw: '#ffc435',
    muted: '#8c90a8',
};

const REPORT_TEXT = makeSpotDetailText({
    appName: APP_NAME,
    nimiqPayUrl: document.body.dataset.nimiqPayUrl || 'https://nimpay.app',
});
const REPORT_DETAILS_MAX = Number.parseInt(document.body.dataset.reportDetailsMax || '300', 10);

const UI_COPY = {
    notices: {
        locationUnavailable: {
            title: 'Location unavailable',
            body: `${APP_NAME} could not read your location. You can still move the map manually. Distances are hidden until location is available.`,
        },
        mapSetupFailed: {
            title: 'Map setup failed',
            body: 'The spot map could not be loaded. Reload the page and try again.',
        },
        spotLoadFailed: {
            title: 'Could not load spots',
            body: 'The visible spot list could not be refreshed. Move the map or reload the page.',
        },
    },
    status: {
        listTitle: 'Visible Spots',
        listTitleWithCount: (n) => `Visible Spots (${n})`,
        emptyBeforeLink: 'No spots meet your criteria. Be the first to ',
        emptyLink: 'make one',
        emptyAfterLink: '.',
        ctaBeforeLink: "Not found what you're looking for? Try ",
        ctaLink: 'making one',
        ctaAfterLink: '.',
    },
};

const els = {
    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),

    map: document.getElementById('spot-map'),
    filterActive: document.getElementById('filter-active'),
    filterUpcoming: document.getElementById('filter-upcoming'),
    filterPrizedraws: document.getElementById('filter-prizedraws'),
    filterTestLocation: document.getElementById('filter-test-location'),
    filterToggles: document.querySelectorAll('.filter-toggle'),
    listTitle: document.getElementById('visible-spots-title'),
    list: document.getElementById('spot-list'),
    empty: document.getElementById('empty-spots'),

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

    claimBackdrop: document.getElementById('claim-backdrop'),
    claimForm: document.getElementById('claim-form'),
    claimTitle: document.getElementById('claim-title'),
    claimSpotName: document.getElementById('claim-spot-name'),
    claimSummary: document.getElementById('claim-summary'),
    claimPasswordField: document.getElementById('claim-password-field'),
    claimPasswordLabel: document.getElementById('claim-password-label'),
    claimPassword: document.getElementById('claim-password-input'),
    claimCaptchaLabel: document.getElementById('claim-captcha-label'),
    claimCaptchaQuestion: document.getElementById('claim-captcha-question'),
    claimCaptchaInput: document.getElementById('claim-captcha-input'),
    claimConfirm: document.getElementById('claim-confirm'),
    claimCancel: document.getElementById('claim-cancel'),
    claimError: document.getElementById('claim-error'),
};


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

    limitEl.textContent = REPORT_TEXT.report.detailsLimit(remaining);
    limitEl.setAttribute('aria-live', 'polite');
}
const showNotice = createNoticePresenter(els);

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
            showReportTooltip(wrap, REPORT_TEXT.report.noDeviceTooltip);
        }
    });
    wrap.addEventListener('focusin', () => {
        if (reportBlockedByMissingDevice()) {
            showReportTooltip(wrap, REPORT_TEXT.report.noDeviceTooltip);
        }
    });
    wrap.addEventListener('mouseleave', hideReportTooltip);
    wrap.addEventListener('focusout', hideReportTooltip);
    wrap.addEventListener('touchstart', () => {
        if (!reportBlockedByMissingDevice()) return;
        showReportTooltip(wrap, REPORT_TEXT.report.noDeviceTooltip);
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

function requestLocation() {
    return new Promise((resolve) => {
        if (!navigator.geolocation) {
            resolve(null);
            return;
        }

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                resolve({
                    lat: pos.coords.latitude,
                    long: pos.coords.longitude,
                    accuracy: pos.coords.accuracy,
                });
            },
            () => resolve(null),
            {
                enableHighAccuracy: true,
                timeout: 8000,
                maximumAge: 60000,
            }
        );
    });
}

function getFilterParams() {
    return {
        includeActive: els.filterActive.checked,
        includeUpcoming: els.filterUpcoming.checked,
        includePrizedraws: els.filterPrizedraws.checked,
    };
}

function setBoolParam(params, name, value) {
    params.set(name, value ? 'true' : 'false');
}

function addFilterParams(params) {
    const filters = getFilterParams();
    setBoolParam(params, 'include_active', filters.includeActive);
    setBoolParam(params, 'include_upcoming', filters.includeUpcoming);
    setBoolParam(params, 'include_prizedraws', filters.includePrizedraws);
    return filters;
}

function addAllVisibleMapParams(params) {
    // The map asks the server for every visible spot, then the client decides
    // whether each spot should be coloured normally or shown in greyscale.
    setBoolParam(params, 'include_active', true);
    setBoolParam(params, 'include_upcoming', true);
    setBoolParam(params, 'include_prizedraws', true);
}

function spotMatchesFilters(spot, filters = getFilterParams()) {
    if (spot.is_prizedraw && !filters.includePrizedraws) return false;
    if (spot.status_label === 'upcoming') return filters.includeUpcoming;
    return filters.includeActive;
}

function enforceActiveUpcomingPair() {
    if (!els.filterActive.checked && !els.filterUpcoming.checked) {
        els.filterActive.checked = true;
        els.filterUpcoming.checked = true;
    }
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error('Request failed.');
    return response.json();
}

function updateFilterToggleState() {
    for (const toggle of els.filterToggles) {
        const input = toggle.querySelector('input[type="checkbox"]');
        const selected = Boolean(input?.checked);
        toggle.classList.toggle('is-selected', selected);
        toggle.classList.toggle('is-off', !selected);
        toggle.setAttribute('aria-pressed', selected ? 'true' : 'false');
    }
}

function getDistanceOrigin() {
    if (!state.hasUserLocation) return null;
    return { lat: state.userLat, long: state.userLong };
}

function spotScheduleSummary(spot) {
    const now = Math.floor(Date.now() / 1000);
    const status = String(spot?.status_label || '').toLowerCase();
    const startsAt = Number(spot?.starts_at || 0);
    const endsAt = Number(spot?.ends_at || 0);

    if (status === 'active' && endsAt > 0) {
        return highestTimeUnitText(Math.max(0, endsAt - now), 'Remaining');
    }
    if (status === 'upcoming' && startsAt > 0) {
        return highestTimeUnitText(Math.max(0, startsAt - now), 'Until Start');
    }
    if ((status === 'ended' || status === 'completed' || endsAt <= now) && endsAt > 0) {
        return `Ended ${unixToText(endsAt) || 'recently'}`;
    }
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

function buildListMessage(hasSpots) {
    const link = document.createElement('a');
    link.href = CREATE_SPOT_URL;
    link.className = 'welcome-link';

    if (hasSpots) {
        link.textContent = UI_COPY.status.ctaLink;
        els.empty.replaceChildren(
            document.createTextNode(UI_COPY.status.ctaBeforeLink),
            link,
            document.createTextNode(UI_COPY.status.ctaAfterLink)
        );
        return;
    }

    link.textContent = UI_COPY.status.emptyLink;
    els.empty.replaceChildren(
        document.createTextNode(UI_COPY.status.emptyBeforeLink),
        link,
        document.createTextNode(UI_COPY.status.emptyAfterLink)
    );
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

function spotListMetaText(spot) {
    const place = spotPlaceText(spot);
    if (spot.distance_m === null || spot.distance_m === undefined) return place;
    return `${place} - ${metresToText(spot.distance_m)}`;
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

function reportResponseErrorText(data, fallback = REPORT_TEXT.report.failed.body) {
    return sharedResponseErrorText(data, fallback);
}

async function fetchJsonWithBody(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
        const err = new Error(reportResponseErrorText(data));
        err.data = data;
        err.status = response.status;
        throw err;
    }
    return data;
}

async function requestWalletDeviceIdForReport() {
    try {
        state.deviceIdHash = await requestDeviceIdentifierHash(
            requestDeviceIdentifier,
            REPORT_TEXT.nimiqPay.deviceIdReason,
        );
        state.walletAvailable = true;
        return true;
    } catch (err) {
        state.walletAvailable = false;
        state.deviceIdHash = null;
        return false;
    }
}

function reportAuthPayload() {
    return {
        device_id_hash: state.deviceIdHash,
        wallet_available: state.walletAvailable,
        language: state.language,
        location_available: state.hasUserLocation,
        lat: state.hasUserLocation ? state.userLat : null,
        long: state.hasUserLocation ? state.userLong : null,
        accuracy: state.userAccuracy,
    };
}

async function identifyReportUser() {
    if (state.reportIdentityPromise) return state.reportIdentityPromise;

    state.reportIdentityPromise = (async () => {
        if (!state.walletAvailable || !state.deviceIdHash) {
            await requestWalletDeviceIdForReport();
        }

        try {
            const data = await fetchJsonWithBody('/api/home/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(reportAuthPayload()),
            });
            state.user = data.user || null;
            if (data.test_user) state.walletAvailable = true;
        } catch (err) {
            state.user = null;
        } finally {
            state.reportIdentityReady = true;
            updateReportControlVisibility();
            updateReportConfirmState();
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
    updateOwnerClaimCodeControls();
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

function resetCaptcha() {
    state.captchaA = Math.floor(Math.random() * 9) + 1;
    state.captchaB = Math.floor(Math.random() * 9) + 1;
    if (els.reportCaptchaQuestion) {
        els.reportCaptchaQuestion.textContent = REPORT_TEXT.report.captchaQuestion({ a: state.captchaA, b: state.captchaB });
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
    placeholder.textContent = REPORT_TEXT.report.reasonPlaceholder;
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

    state.reportSpot = spot;
    state.reportSubmitting = false;
    populateReportReasons();
    resetCaptcha();
    setReportError(null);

    if (els.reportTitle) els.reportTitle.textContent = REPORT_TEXT.report.title;
    if (els.reportSpotName) els.reportSpotName.textContent = REPORT_TEXT.report.spotName(spot.title);
    if (els.reportDetails) {
        els.reportDetails.value = '';
        els.reportDetails.maxLength = REPORT_DETAILS_MAX;
        els.reportDetails.placeholder = REPORT_TEXT.report.detailsPlaceholder;
    }
    updateReportDetailsLimit();
    window.requestAnimationFrame(updateReportDetailsLimit);
    if (els.reportCaptchaInput) els.reportCaptchaInput.placeholder = REPORT_TEXT.report.captchaPlaceholder;
    if (els.reportConfirm) {
        els.reportConfirm.textContent = REPORT_TEXT.report.confirm;
        els.reportConfirm.disabled = true;
    }
    updateReportConfirmState();
    if (els.reportCancel) els.reportCancel.textContent = REPORT_TEXT.report.cancel;

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
        const data = await fetchJsonWithBody(`/api/spot/${spot.id}/report-status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reportAuthPayload()),
        });

        state.user = data.user || state.user;
        updateReportControlVisibility();
        updateReportConfirmState();

        if (data.is_owner) return;
        if (data.already_reported) {
            showNotice(REPORT_TEXT.report.alreadyReported);
            return;
        }

        showReportModal(spot);
    } catch (err) {
        const data = err?.data || {};
        if (data.code === 'wallet_unavailable') {
            showNotice(REPORT_TEXT.report.walletUnavailable);
            return;
        }
        showNotice({
            ...REPORT_TEXT.report.failed,
            body: err?.message || REPORT_TEXT.report.failed.body,
        });
    }
}

async function submitReport(event) {
    event.preventDefault();
    if (state.reportSubmitting || !state.reportSpot) return;

    if (!els.reportReason?.value || !captchaPassed()) {
        setReportError(REPORT_TEXT.report.incomplete);
        updateReportConfirmState();
        return;
    }

    await identifyReportUser();
    if (!state.user) {
        hideReportModal();
        showNotice(REPORT_TEXT.report.walletUnavailable);
        return;
    }

    state.reportSubmitting = true;
    els.reportConfirm.disabled = true;
    els.reportConfirm.textContent = REPORT_TEXT.report.confirming;
    setReportError(null);

    try {
        await fetchJsonWithBody(`/api/spot/${state.reportSpot.id}/report`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...reportAuthPayload(),
                reason: Number.parseInt(els.reportReason.value, 10),
                details: els.reportDetails?.value || '',
                captcha_a: state.captchaA,
                captcha_b: state.captchaB,
                captcha_answer: Number.parseInt(String(els.reportCaptchaInput?.value || '0'), 10),
            }),
        });

        hideReportModal();
        showNotice(REPORT_TEXT.report.submitted);
    } catch (err) {
        const data = err?.data || {};
        if (data.code === 'wallet_unavailable') {
            hideReportModal();
            showNotice(REPORT_TEXT.report.walletUnavailable);
            return;
        }
        if (data.code === 'already_reported') {
            hideReportModal();
            showNotice(REPORT_TEXT.report.alreadyReported);
            return;
        }
        if (data.code === 'own_spot') {
            hideReportModal();
            updateReportControlVisibility();
            return;
        }

        state.reportSubmitting = false;
        els.reportConfirm.textContent = REPORT_TEXT.report.confirm;
        updateReportConfirmState();
        setReportError(err?.message || REPORT_TEXT.report.failed.body);
        resetCaptcha();
        updateReportConfirmState();
    }
}


function spotCanShowOwnerClaimCodes(spot) {
    return state.reportIdentityReady
        && currentUserOwnsSpot(spot)
        && Number(spot?.claim_code_count || 0) > 0;
}

async function loadOwnerClaimCodesForControl(entry) {
    if (!entry || entry.loaded || entry.loading) return;
    const { spot, control } = entry;
    if (!spotCanShowOwnerClaimCodes(spot)) {
        control.hide();
        return;
    }

    entry.loading = true;
    control.setLoading();
    try {
        const data = await fetchJsonWithBody(`/api/spot/${spot.id}/claim-codes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reportAuthPayload()),
        });
        entry.loaded = true;
        control.render(data.claim_codes || []);
    } catch (err) {
        console.error(err);
        control.setFailed();
    } finally {
        entry.loading = false;
    }
}

function updateOwnerClaimCodeControls() {
    for (const entry of state.ownerClaimCodeControls) {
        if (!spotCanShowOwnerClaimCodes(entry.spot)) {
            entry.control.hide();
            continue;
        }
        loadOwnerClaimCodesForControl(entry);
    }
}

function buildOwnerClaimCodesLineForSpot(spot) {
    const control = createOwnerClaimCodesControl();
    const entry = { spot, control, loaded: false, loading: false };
    state.ownerClaimCodeControls.push(entry);
    if (spotCanShowOwnerClaimCodes(spot)) loadOwnerClaimCodesForControl(entry);
    return control.line;
}

function claimAuthPayload() {
    return {
        device_id_hash: state.deviceIdHash,
        wallet_available: state.walletAvailable,
        language: state.language,
        location_available: state.hasUserLocation,
        lat: state.hasUserLocation ? state.userLat : null,
        long: state.hasUserLocation ? state.userLong : null,
        accuracy: state.userAccuracy,
    };
}

async function requestClaimPayoutAddress() {
    try {
        const nimiq = await init();
        const accounts = await nimiq.listAccounts();
        if (Array.isArray(accounts) && accounts.length > 0 && typeof accounts[0] === 'string') {
            return accounts[0];
        }
    } catch (err) {
        console.warn('Could not read Nimiq payout address before claim.', err);
    }
    return null;
}

function spotWithinRadius(spot) {
    const distance = Number(spot.distance_m);
    const radius = Number(spot.radius || 0);
    return Number.isFinite(distance) && Number.isFinite(radius) && distance <= radius;
}

function claimStatusForSpot(spot) {
    const stored = state.claimStatusBySpotId.get(Number(spot.id));
    if (stored) return stored;

    const inRange = spotWithinRadius(spot);
    const ownSpot = currentUserOwnsSpot(spot);
    const active = spot.status_label === 'active';
    const participantCount = Number(spot.success_claim_count || 0)
        + (spot.is_prizedraw ? Number(spot.pending_claim_count || 0) : 0);
    const maxParticipants = Number(spot.max_total_claims || 0);
    const capacityFull = maxParticipants > 0 && participantCount >= maxParticipants;
    let reason = 'outside_radius';
    let message = 'Move inside the spot radius to claim.';

    if (ownSpot) {
        reason = 'own_spot';
        message = 'You cannot claim your own spot.';
    } else if (!state.user) {
        reason = 'user_unknown';
        message = `Open ${APP_NAME} in Nimiq Pay to identify this device.`;
    } else if (!active) {
        reason = 'not_active';
        message = 'This spot is not active right now.';
    } else if (capacityFull) {
        reason = 'capacity_full';
        message = 'This spot has no remaining claim capacity.';
    } else if (!state.hasUserLocation) {
        reason = 'location_unknown';
        message = 'Your location is unknown.';
    } else if (inRange) {
        reason = 'unknown';
        message = 'This Spot cannot be claimed right now.';
    }

    return {
        allowed: false,
        action: 'unavailable',
        kind: 'unavailable',
        reason,
        user_ok: Boolean(state.user),
        own_spot: ownSpot,
        location_known: state.hasUserLocation,
        within_radius: inRange,
        capacity_ok: !capacityFull,
        user_limit_ok: true,
        requires_password: Boolean(spot.use_password),
        requires_duration: Number(spot.claim_duration || 0) > 0,
        is_prizedraw: Boolean(spot.is_prizedraw),
        reward_amount: Number(spot.total_value || 0) / Math.max(1, Number(spot.is_prizedraw ? spot.prize_count || 1 : spot.max_total_claims || 1)),
        participant_count: participantCount,
        max_participants: maxParticipants,
        prize_count: Number(spot.prize_count || 1),
        message,
    };
}

function shouldShowClaimAction(spot) {
    if (spot.status_label !== 'active') return false;

    const status = claimStatusForSpot(spot);
    if (status.allowed || status.within_radius) return true;

    const reason = String(status.reason || '').toLowerCase();
    if (status.own_spot || status.capacity_ok === false || status.user_limit_ok === false) return true;

    return new Set([
        'own_spot',
        'user_not_allowed',
        'capacity_full',
        'user_limit_reached',
        'claim_code_unavailable',
        'already_claimed',
        'already_entered',
        'cancellation_pending',
    ]).has(reason);
}

function claimActionText(spot) {
    const action = claimStatusForSpot(spot).action || 'unavailable';
    // Keep the underlying text simple; .nq-label performs the visual uppercase.
    return REPORT_TEXT.claim?.actions?.[action] || action;
}

function claimUnavailableMessage(status, spot) {
    const reason = String(status?.reason || '').trim();
    const message = String(status?.message || '').trim();

    if (status?.own_spot || reason === 'own_spot' || currentUserOwnsSpot(spot)) return 'You cannot claim your own spot.';
    if (status?.user_ok === false && reason === 'user_not_allowed') return 'This device account cannot claim spots.';
    if (status?.capacity_ok === false || reason === 'capacity_full') return 'This spot has no remaining claim capacity.';
    if (status?.user_limit_ok === false || reason === 'user_limit_reached') return 'You have already reached your claim limit for this spot.';
    if (reason === 'claim_code_unavailable') return 'There are no unused claim codes left for this spot.';
    if (reason === 'already_claimed' || reason === 'already_entered') return message || 'You have already used your available claim for this spot.';
    if (reason === 'cancellation_pending') return 'This spot is being cancelled and can no longer be claimed.';
    if (!state.user || reason === 'user_unknown') return `Open ${APP_NAME} in Nimiq Pay to identify this device.`;
    if (!state.hasUserLocation || reason === 'location_unknown') return 'Your location is unknown.';
    if (reason === 'not_active') return 'This spot is not active right now.';
    if (reason === 'outside_radius' || status?.within_radius === false) return 'Move inside the spot radius to claim.';
    if (message && message !== 'This spot cannot be claimed right now.') return message;

    return REPORT_TEXT.claim?.unavailableTooltip || 'This Spot cannot be claimed right now.';
}

function attachUnavailableClaimTooltip(target, status, spot) {
    if (!target) return;
    const show = (event = null) => {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        showReportTooltip(target, claimUnavailableMessage(status, spot));
    };
    target.addEventListener('mouseenter', show);
    target.addEventListener('focusin', show);
    target.addEventListener('mouseleave', hideReportTooltip);
    target.addEventListener('focusout', hideReportTooltip);
    target.addEventListener('touchstart', (event) => {
        show(event);
        window.setTimeout(hideReportTooltip, 1800);
    }, { passive: false });
}

function eventStartedOnInteractiveElement(event) {
    const target = event?.target;
    return Boolean(target?.closest?.('a, button, input, textarea, select, .spot-copy-button, .spot-report-button'));
}

async function refreshClaimStatusesForSpots(spots) {
    state.claimStatusBySpotId = new Map();
    if (!Array.isArray(spots) || spots.length <= 0) return;

    await identifyReportUser();
    if (!state.user) return;

    try {
        const data = await fetchJsonWithBody('/api/spots/claim-status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...claimAuthPayload(),
                spot_ids: spots.map((spot) => Number(spot.id)).filter(Number.isFinite),
            }),
        });

        state.user = data.user || state.user;
        const statuses = data.statuses || {};
        for (const [spotId, status] of Object.entries(statuses)) {
            state.claimStatusBySpotId.set(Number(spotId), status);
        }
    } catch (err) {
        console.error(err);
    }
}

function setClaimError(message) {
    if (!els.claimError) return;
    els.claimError.textContent = message || '';
    els.claimError.hidden = !message;
}

function ensureClaimCaptcha() {
    if (state.claimCaptcha || !els.claimCaptchaQuestion || !els.claimCaptchaInput) return state.claimCaptcha;
    state.claimCaptcha = createCaptchaController({
        questionEl: els.claimCaptchaQuestion,
        inputEl: els.claimCaptchaInput,
        min: CLAIM_CAPTCHA_MIN,
        max: CLAIM_CAPTCHA_MAX,
        questionText: REPORT_TEXT.claim?.captchaQuestion || (({ a, b }) => `What is ${a} + ${b}?`),
        onChange: updateClaimConfirmState,
    });
    return state.claimCaptcha;
}

function updateClaimConfirmState() {
    if (!els.claimConfirm || state.claimSubmitting || !state.claimSpot) return;
    const status = claimStatusForSpot(state.claimSpot);
    const needsPassword = Boolean(status.requires_password || state.claimSpot.use_password);
    const passwordReady = !needsPassword || Boolean(String(els.claimPassword?.value || '').trim());
    const captchaReady = !needsPassword || Boolean(state.claimCaptcha?.passed());
    els.claimConfirm.disabled = !(status.allowed && passwordReady && captchaReady);
}

function claimSummaryLine(text) {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
}

function populateClaimSummary(spot) {
    const status = claimStatusForSpot(spot);
    const claimText = REPORT_TEXT.claim || {};
    const rewardText = nimFromLunaText(status.reward_amount ?? 0);
    const rows = [];

    if (spot.is_prizedraw) {
        rows.push(claimSummaryLine(claimText.prizeValue ? claimText.prizeValue(rewardText) : `Prize value: ${rewardText}`));
        rows.push(claimSummaryLine(claimText.participants ? claimText.participants({ current: Number(status.participant_count || 0), max: Number(status.max_participants || 0) }) : `Participants: ${Number(status.participant_count || 0)}`));
        rows.push(claimSummaryLine(claimText.prizes ? claimText.prizes({ count: Number(status.prize_count || spot.prize_count || 1) }) : `${Number(status.prize_count || spot.prize_count || 1)} prizes available`));
    } else {
        rows.push(claimSummaryLine(claimText.reward ? claimText.reward(rewardText) : `Reward: ${rewardText}`));
    }

    const duration = durationText(spot.claim_duration);
    if (duration) rows.push(claimSummaryLine(claimText.durationRequired ? claimText.durationRequired(duration) : `You must remain within the area for ${duration}.`));
    if (status.requires_password || spot.use_password) rows.push(claimSummaryLine(claimText.passwordRequired || 'A password is required.'));

    els.claimSummary.replaceChildren(...rows);
}

function showClaimModal(spot) {
    if (!els.claimBackdrop) return;
    state.claimSpot = spot;
    state.claimSubmitting = false;
    setClaimError(null);

    const status = claimStatusForSpot(spot);
    const claimText = REPORT_TEXT.claim || {};
    const needsPassword = Boolean(status.requires_password || spot.use_password);

    els.claimTitle.textContent = claimText.title || 'Claim Spot';
    els.claimSpotName.textContent = spot.title || 'NimHunt Spot';
    populateClaimSummary(spot);

    els.claimPasswordField.hidden = !needsPassword;
    if (els.claimPasswordLabel) els.claimPasswordLabel.textContent = claimText.passwordLabel || 'Password';
    if (els.claimPassword) {
        els.claimPassword.value = '';
        els.claimPassword.placeholder = claimText.passwordPlaceholder || 'Enter password';
    }
    if (els.claimCaptchaLabel) els.claimCaptchaLabel.textContent = claimText.captchaLabel || 'Captcha';
    if (els.claimCaptchaInput) els.claimCaptchaInput.placeholder = claimText.captchaPlaceholder || 'Answer';
    if (needsPassword) ensureClaimCaptcha()?.reset();

    const actionText = claimActionText(spot);
    els.claimConfirm.textContent = actionText;
    els.claimConfirm.classList.toggle('green', status.kind === 'standard');
    els.claimConfirm.classList.toggle('light-blue', status.kind === 'conditional');
    els.claimConfirm.classList.toggle('gold', status.kind === 'prizedraw');
    if (els.claimCancel) els.claimCancel.textContent = claimText.cancel || 'Cancel';
    updateClaimConfirmState();
    els.claimBackdrop.hidden = false;
    requestAnimationFrame(() => (needsPassword ? els.claimPassword?.focus() : els.claimConfirm?.focus()));
}

function hideClaimModal() {
    if (!els.claimBackdrop) return;
    els.claimBackdrop.hidden = true;
    state.claimSpot = null;
    state.claimSubmitting = false;
    setClaimError(null);
}

async function postClaimForSpot(spot, { claimCode = null, captchaPayload = {} } = {}) {
    const payoutAddress = await requestClaimPayoutAddress();
    return fetchJsonWithBody(`/api/spot/${spot.id}/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ...claimAuthPayload(),
            payout_address: payoutAddress,
            claim_code: claimCode,
            ...captchaPayload,
        }),
    });
}

function redirectToClaim(data) {
    const url = new URL(data.claim_url || `/claim/${data.claim?.id || ''}`, window.location.origin);
    url.searchParams.set('from', 'find-spots');
    if (data.success_now) url.searchParams.set('claimed', '1');
    window.location.href = `${url.pathname}${url.search}`;
}

async function submitImmediateClaim(spot, button) {
    if (state.claimSubmitting) return;

    state.claimSubmitting = true;
    if (button) {
        button.disabled = true;
        button.textContent = REPORT_TEXT.claim?.confirming || 'Confirming…';
    }

    try {
        const data = await postClaimForSpot(spot);
        redirectToClaim(data);
    } catch (err) {
        state.claimSubmitting = false;
        if (button) {
            button.disabled = false;
            button.textContent = claimActionText(spot);
        }
        showReportTooltip(button, err?.message || REPORT_TEXT.claim?.failed?.body || 'The claim could not be created.');
        window.setTimeout(hideReportTooltip, 2200);
    }
}

function claimNeedsConfirmationCard(spot) {
    const status = claimStatusForSpot(spot);
    return Boolean(
        status.requires_password
        || spot.use_password
        || status.requires_duration
        || spot.is_prizedraw
    );
}

async function submitClaim(event) {
    event.preventDefault();
    if (state.claimSubmitting || !state.claimSpot) return;

    const spot = state.claimSpot;
    const status = claimStatusForSpot(spot);
    const needsPassword = Boolean(status.requires_password || spot.use_password);
    if (needsPassword && (!String(els.claimPassword?.value || '').trim() || !state.claimCaptcha?.passed())) {
        setClaimError(REPORT_TEXT.claim?.passwordIncomplete || 'Enter the password and complete the captcha.');
        updateClaimConfirmState();
        return;
    }

    state.claimSubmitting = true;
    els.claimConfirm.disabled = true;
    els.claimConfirm.textContent = REPORT_TEXT.claim?.confirming || 'Confirming…';
    setClaimError(null);

    try {
        const data = await postClaimForSpot(spot, {
            claimCode: needsPassword ? String(els.claimPassword?.value || '').trim() : null,
            captchaPayload: needsPassword && state.claimCaptcha ? state.claimCaptcha.payload() : {},
        });
        redirectToClaim(data);
    } catch (err) {
        const data = err?.data || {};
        state.claimSubmitting = false;
        els.claimConfirm.textContent = claimActionText(spot);
        updateClaimConfirmState();
        setClaimError(err?.message || REPORT_TEXT.claim?.failed?.body || 'The claim could not be created.');
        if (needsPassword && (data.code === 'invalid_claim_code' || data.code === 'captcha_failed')) {
            if (els.claimPassword) els.claimPassword.value = '';
            state.claimCaptcha?.reset();
            requestAnimationFrame(() => els.claimPassword?.focus());
        }
    }
}

function buildClaimAction(spot) {
    const status = claimStatusForSpot(spot);
    const kind = status.kind || 'unavailable';
    const action = document.createElement('button');
    action.type = 'button';
    action.className = `spot-claim-button nq-button-pill nq-label is-${kind}`;
    action.textContent = claimActionText(spot);

    if (!status.allowed) {
        action.classList.add('is-unavailable');
        action.setAttribute('aria-disabled', 'true');
        attachUnavailableClaimTooltip(action, status, spot);
        action.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            showReportTooltip(action, claimUnavailableMessage(status, spot));
        });
        return action;
    }

    const open = (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (claimNeedsConfirmationCard(spot)) {
            showClaimModal(spot);
            return;
        }
        submitImmediateClaim(spot, action);
    };
    action.addEventListener('click', open);
    action.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        open(event);
    });
    return action;
}

function buildReportControl(spot) {
    const line = document.createElement('p');
    line.className = 'spot-report-line';
    line.hidden = !state.reportIdentityReady || currentUserOwnsSpot(spot);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'spot-report-button';
    button.textContent = REPORT_TEXT.report.open;
    button.addEventListener('click', () => openReportFlow(spot));

    line.append(button);
    state.reportControls.push({ line, spot });
    return line;
}

function buildSpotDetail(spot) {
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail';

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
    if (Number(spot.claim_code_count || 0) > 0) {
        lines.append(buildOwnerClaimCodesLineForSpot(spot));
    }
    detail.append(lines);
    detail.append(buildReportControl(spot));

    return detail;
}

function setListItemExpanded(item, summary, detail, spotId, expanded) {
    item.classList.toggle('is-expanded', expanded);
    summary.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    detail.hidden = !expanded;

    if (expanded) {
        state.expandedSpotIds.add(spotId);
    } else {
        state.expandedSpotIds.delete(spotId);
    }
}

function renderList(spots) {
    const hasSpots = spots.length > 0;

    els.list.replaceChildren();
    els.list.hidden = !hasSpots;
    els.empty.hidden = false;
    els.listTitle.textContent = UI_COPY.status.listTitleWithCount(spots.length);
    buildListMessage(hasSpots);
    state.reportControls = [];
    state.ownerClaimCodeControls = [];

    if (!hasSpots) return;

    for (const spot of spots) {
        const spotId = Number(spot.id);
        const item = document.createElement('li');
        item.className = 'spot-list-item';

        const summary = document.createElement('div');
        summary.className = 'spot-list-toggle';
        summary.setAttribute('role', 'button');
        summary.tabIndex = 0;
        summary.setAttribute('aria-expanded', 'false');

        const topRow = document.createElement('span');
        topRow.className = 'spot-list-row spot-list-top-row';

        const title = document.createElement('span');
        title.className = 'spot-list-title';
        appendSpotTitleWithLock(title, spot);

        const chevron = document.createElement('span');
        chevron.className = 'spot-list-chevron';
        chevron.textContent = '⌄';
        chevron.setAttribute('aria-hidden', 'true');

        const actions = document.createElement('span');
        actions.className = 'spot-list-actions';
        const showClaim = shouldShowClaimAction(spot);
        if (showClaim) {
            const claimStatus = claimStatusForSpot(spot);
            const claimKind = claimStatus.kind || 'unavailable';
            item.classList.add('is-claim-nearby', `is-claim-${claimKind}`);
            summary.classList.add('has-claim-action');
            actions.append(buildClaimAction(spot), chevron);
        } else {
            const statusBadge = document.createElement('span');
            statusBadge.className = `spot-badge ${spotStatusClass(spot)}`;
            statusBadge.textContent = spotStatusText(spot);
            actions.append(statusBadge, chevron);
        }

        topRow.append(title, actions);

        const bottomRow = document.createElement('span');
        bottomRow.className = 'spot-list-row spot-list-bottom-row';

        const meta = document.createElement('span');
        meta.className = 'spot-list-meta';
        meta.textContent = spotListMetaText(spot);

        bottomRow.append(meta);
        summary.append(topRow, bottomRow);

        const detail = buildSpotDetail(spot);
        const initiallyExpanded = state.expandedSpotIds.has(spotId);
        setListItemExpanded(item, summary, detail, spotId, initiallyExpanded);

        const toggleExpanded = () => {
            const expanded = summary.getAttribute('aria-expanded') !== 'true';
            setListItemExpanded(item, summary, detail, spotId, expanded);
        };

        summary.addEventListener('click', (event) => {
            if (eventStartedOnInteractiveElement(event)) return;
            toggleExpanded();
        });
        summary.addEventListener('keydown', (event) => {
            if (eventStartedOnInteractiveElement(event)) return;
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            toggleExpanded();
        });

        item.append(summary, detail);
        els.list.append(item);
    }

    updateReportControlVisibility();
}

function markerColour(spot) {
    return spot.is_prizedraw ? MAP_COLOURS.prizedraw : MAP_COLOURS.standard;
}

function renderMapSpots(spots) {
    const filters = getFilterParams();
    const radiusCircles = [];
    const dots = [];

    state.spotLayer.clearLayers();

    for (const spot of spots) {
        const matchesFilters = spotMatchesFilters(spot, filters);
        const colour = matchesFilters ? markerColour(spot) : MAP_COLOURS.muted;
        const openSpot = () => {
            window.location.href = spot.href;
        };

        if (matchesFilters) {
            const radiusCircle = L.circle([spot.lat, spot.long], {
                radius: spot.radius,
                color: colour,
                opacity: 0.95,
                fillColor: colour,
                fillOpacity: 0.22,
                weight: 2.5,
            });
            radiusCircle.on('click', openSpot);
            radiusCircles.push(radiusCircle);
        }

        const dot = L.circleMarker([spot.lat, spot.long], {
            radius: 6,
            color: '#ffffff',
            fillColor: colour,
            fillOpacity: matchesFilters ? 1 : 0.68,
            weight: 2,
        });
        dot.on('click', openSpot);
        dots.push(dot);
    }

    // Draw all radii first and all dots second. That keeps the translucent
    // radius overlays visible without letting them wash over the spot dots.
    for (const radiusCircle of radiusCircles) {
        radiusCircle.addTo(state.spotLayer);
    }
    for (const dot of dots) {
        dot.addTo(state.spotLayer);
    }
}

async function fetchInitialSpots() {
    const params = new URLSearchParams();
    addAllVisibleMapParams(params);

    if (state.hasUserLocation) {
        params.set('lat', String(state.userLat));
        params.set('long', String(state.userLong));
    }

    return fetchJson(`/api/spots/initial?${params.toString()}`);
}

function fitInitialMap(spots) {
    if (state.hasUserLocation) {
        state.map.setView([state.userLat, state.userLong], 14, { animate: false });
        return;
    }

    const points = [];
    for (const spot of spots.slice(0, MAX_MAP_INIT_SPOTS)) {
        points.push([spot.lat, spot.long]);
    }

    if (points.length >= 2) {
        state.map.fitBounds(points, { padding: [34, 34], animate: false });
        if (state.map.getZoom() < MAX_MAP_ZOOM_OUT) {
            state.map.setZoom(MAX_MAP_ZOOM_OUT, { animate: false });
        }
        return;
    }

    if (points.length === 1) {
        state.map.setView(points[0], 14, { animate: false });
    }
}

async function refreshVisibleSpots() {
    if (!state.map) return;

    if (state.fetchController) {
        state.fetchController.abort();
    }

    const params = new URLSearchParams();
    addAllVisibleMapParams(params);

    const bounds = state.map.getBounds();
    const origin = getDistanceOrigin();
    params.set('min_lat', String(bounds.getSouth()));
    params.set('max_lat', String(bounds.getNorth()));
    params.set('min_long', String(bounds.getWest()));
    params.set('max_long', String(bounds.getEast()));
    if (origin) {
        params.set('distance_lat', String(origin.lat));
        params.set('distance_long', String(origin.long));
    }
    params.set('limit', '150');

    state.fetchController = new AbortController();
    try {
        const data = await fetchJson(`/api/spots/search?${params.toString()}`, {
            signal: state.fetchController.signal,
        });
        const spots = data.spots || [];
        const listSpots = spots.filter((spot) => spotMatchesFilters(spot));

        state.lastSpots = spots;
        await refreshClaimStatusesForSpots(listSpots);
        renderMapSpots(spots);
        renderList(listSpots);
    } catch (err) {
        if (err.name === 'AbortError') return;
        console.error(err);
        showNotice(UI_COPY.notices.spotLoadFailed);
    }
}


function setRecordedLocation({ lat, long, accuracy = null, isReal = false }) {
    state.userLat = Number(lat);
    state.userLong = Number(long);
    state.userAccuracy = accuracy === null || accuracy === undefined ? null : Number(accuracy);
    state.hasUserLocation = Number.isFinite(state.userLat) && Number.isFinite(state.userLong);

    if (isReal) {
        state.realLat = state.userLat;
        state.realLong = state.userLong;
        state.realAccuracy = state.userAccuracy;
    }

    updateUserMarker();
}

function updateUserMarker() {
    if (!state.map) return;
    if (!state.hasUserLocation) {
        state.userMarker?.remove();
        state.userMarker = null;
        return;
    }

    const position = [state.userLat, state.userLong];
    if (!state.userMarker) {
        state.userMarker = L.circleMarker(position, {
            radius: 5,
            color: '#1f2348',
            fillColor: '#1f2348',
            fillOpacity: 0.92,
            weight: 2,
        }).addTo(state.map);
        return;
    }

    state.userMarker.setLatLng(position);
}

function syncTestLocationFromMapCentre() {
    if (!state.map || !state.testLocationMode) return;
    const centre = state.map.getCenter();
    setRecordedLocation({ lat: centre.lat, long: centre.lng, accuracy: null });
}

function setMapInteractionEnabled(enabled) {
    if (!state.map) return;
    const method = enabled ? 'enable' : 'disable';
    state.map.dragging?.[method]?.();
    state.map.keyboard?.[method]?.();
    state.map.boxZoom?.[method]?.();
}

async function handleMapMoved() {
    if (state.testLocationMode) syncTestLocationFromMapCentre();
    await refreshVisibleSpots();
}

async function toggleTestLocationMode() {
    state.testLocationMode = Boolean(els.filterTestLocation?.checked);

    if (state.testLocationMode) {
        setMapInteractionEnabled(true);
        syncTestLocationFromMapCentre();
    } else if (Number.isFinite(state.realLat) && Number.isFinite(state.realLong)) {
        setRecordedLocation({ lat: state.realLat, long: state.realLong, accuracy: state.realAccuracy });
        state.map?.setView([state.realLat, state.realLong], state.map.getZoom(), { animate: false });
        setMapInteractionEnabled(false);
    } else {
        state.hasUserLocation = false;
        state.userLat = null;
        state.userLong = null;
        state.userAccuracy = null;
        updateUserMarker();
        setMapInteractionEnabled(true);
    }

    updateFilterToggleState();
    await refreshVisibleSpots();
}

function setupMap() {
    if (!window.L) throw new Error('Leaflet did not load.');

    const start = state.hasUserLocation
        ? [state.userLat, state.userLong]
        : [55.8642, -4.2518];

    state.map = L.map(els.map, {
        zoomControl: false,
        attributionControl: true,
        dragging: !state.hasUserLocation,
        keyboard: !state.hasUserLocation,
        boxZoom: !state.hasUserLocation,
    }).setView(start, state.hasUserLocation ? 14 : 11);

    L.control.zoom({
        position: 'topright',
    }).addTo(state.map);

    L.tileLayer(MAP_TILE_URL, {
        maxZoom: 19,
        attribution: MAP_TILE_ATTRIBUTION,
    }).addTo(state.map);

    state.spotLayer = L.layerGroup().addTo(state.map);

    updateUserMarker();
    state.map.on('moveend zoomend', handleMapMoved);
}

async function initFindSpots() {
    state.language = getLanguage();
    identifyReportUser();
    els.listTitle.textContent = UI_COPY.status.listTitle;

    const location = await requestLocation();
    if (location) {
        setRecordedLocation({ lat: location.lat, long: location.long, accuracy: location.accuracy, isReal: true });
    } else {
        showNotice(UI_COPY.notices.locationUnavailable);
    }

    setupMap();

    const initial = await fetchInitialSpots();
    fitInitialMap(initial.spots || []);

    await refreshVisibleSpots();
}

els.noticeOk.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

if (els.reportCancel) {
    els.reportCancel.addEventListener('click', hideReportModal);
}

if (els.reportForm) {
    els.reportForm.addEventListener('submit', submitReport);
}

if (els.claimCancel) {
    els.claimCancel.addEventListener('click', hideClaimModal);
}

if (els.claimForm) {
    els.claimForm.addEventListener('submit', submitClaim);
}

for (const input of [els.claimPassword, els.claimCaptchaInput]) {
    input?.addEventListener('input', () => {
        setClaimError(null);
        updateClaimConfirmState();
    });
    input?.addEventListener('change', () => {
        setClaimError(null);
        updateClaimConfirmState();
    });
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

for (const input of [els.filterActive, els.filterUpcoming, els.filterPrizedraws]) {
    input.addEventListener('change', () => {
        enforceActiveUpcomingPair();
        updateFilterToggleState();
        refreshVisibleSpots();
    });
}

els.filterTestLocation?.addEventListener('change', () => {
    toggleTestLocationMode();
});

updateFilterToggleState();

initFindSpots().catch((err) => {
    console.error(err);
    showNotice({
        ...UI_COPY.notices.mapSetupFailed,
        body: err?.message || UI_COPY.notices.mapSetupFailed.body,
    });
});
