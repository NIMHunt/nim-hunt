import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { makeHomeText } from './interface_text.js?v=qol-v1-20260717';
import {
    createNoticePresenter,
    getLanguage,
    requestDeviceIdentifierHash,
    responseErrorText as sharedResponseErrorText,
} from './browser_utils.js?v=qol-v1-20260717';

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    banned: false,
    displayNameInput: null,
    displayNameMeasurer: null,
    displayNameSaving: false,
};

const APP_NAME = document.body.dataset.appName || document.title || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const DISPLAY_NAME_MIN_LENGTH = Number.parseInt(document.body.dataset.displayNameMin || '3', 10);
const DISPLAY_NAME_MAX_LENGTH = Number.parseInt(document.body.dataset.displayNameMax || '18', 10);

// All human-facing homepage text lives here.
// A notice only shows "Read more" when its copy includes an href.
const UI_COPY = makeHomeText({
    appName: APP_NAME,
    displayNameMin: DISPLAY_NAME_MIN_LENGTH,
    displayNameMax: DISPLAY_NAME_MAX_LENGTH,
});
const els = {
    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),

    lockTooltip: document.getElementById('lock-tooltip'),

    connectionLine: document.getElementById('connection-line'),
    welcomeLine: document.getElementById('welcome-line'),
    displayNameEditor: document.getElementById('display-name-editor'),
    displayNameError: document.getElementById('display-name-error'),
    displayNameSave: document.getElementById('display-name-save'),
    displayNameCancel: document.getElementById('display-name-cancel'),

    findSpotsButton: document.getElementById('find-spots-button'),
    mySpotsButton: document.getElementById('my-spots-button'),
    myClaimsButton: document.getElementById('my-claims-button'),

    debugToggle: document.getElementById('debug-toggle'),
    metrics: document.getElementById('home-metrics'),
    activeSpotsMetric: document.getElementById('home-active-spots'),
    dailyUsersMetric: document.getElementById('home-daily-users'),
    debugPanel: document.getElementById('debug-panel'),
    debugWallet: document.getElementById('debug-wallet'),
    debugLocation: document.getElementById('debug-location'),
    debugLanguage: document.getElementById('debug-language'),
    debugUser: document.getElementById('debug-user'),
};

function hideDisplayNameEditor() {
    state.displayNameInput = null;
    state.displayNameSaving = false;
    if (!els.displayNameEditor || !els.displayNameSave || !els.displayNameCancel) return;
    els.displayNameEditor.hidden = true;
    els.displayNameSave.disabled = false;
    els.displayNameSave.classList.remove('is-disabled-by-validation');
    els.displayNameSave.textContent = UI_COPY.profile.save;
    els.displayNameCancel.disabled = false;
    setDisplayNameError(null);
}

function setDisplayNameError(message) {
    if (message) {
        els.displayNameError.textContent = message;
        els.displayNameError.hidden = false;
    } else {
        els.displayNameError.textContent = '';
        els.displayNameError.hidden = true;
    }
}

function setDisplayNameSaveValid(valid) {
    if (!els.displayNameSave || state.displayNameSaving) return;
    els.displayNameSave.disabled = !valid;
    els.displayNameSave.classList.toggle('is-disabled-by-validation', !valid);
}

function currentDisplayName() {
    if (!state.user) return '';
    return state.user.display_name || UI_COPY.status.userFallback(state.user.id);
}

function validateDisplayName(value) {
    const displayName = String(value || '').trim();
    if (displayName.length < DISPLAY_NAME_MIN_LENGTH || displayName.length > DISPLAY_NAME_MAX_LENGTH) {
        return { ok: false, displayName, message: UI_COPY.profile.invalidLength() };
    }
    return { ok: true, displayName, message: '' };
}

function getDisplayNameMeasurer() {
    if (state.displayNameMeasurer) return state.displayNameMeasurer;

    const measurer = document.createElement('span');
    measurer.className = 'display-name-measurer';
    document.body.append(measurer);
    state.displayNameMeasurer = measurer;
    return measurer;
}

function syncDisplayNameInputWidth(input) {
    const measurer = getDisplayNameMeasurer();
    const inputStyle = window.getComputedStyle(input);
    measurer.style.font = inputStyle.font;
    measurer.style.fontWeight = inputStyle.fontWeight;
    measurer.style.letterSpacing = inputStyle.letterSpacing;
    measurer.textContent = input.value || ' ';
    input.style.width = `${Math.ceil(measurer.getBoundingClientRect().width + 8)}px`;
}

function setDisplayNameInputState(input) {
    const check = validateDisplayName(input.value);
    input.classList.toggle('is-invalid', !check.ok);
    syncDisplayNameInputWidth(input);
    return check;
}

function makeDisplayNamePayload(displayName) {
    const payload = { display_name: displayName };

    if (state.deviceIdHash) {
        payload.device_id_hash = state.deviceIdHash;
    }

    return payload;
}

function responseErrorText(data, fallback = UI_COPY.profile.saveFailed) {
    const detail = data?.detail;
    if (Array.isArray(detail) && detail.length === 0) {
        return UI_COPY.profile.invalidResponse;
    }
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        return detail.msg || detail.message || detail.detail || UI_COPY.profile.invalidResponse;
    }
    return sharedResponseErrorText(data, fallback);
}

function createWelcomeEditIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.style.display = 'block';
    svg.style.width = '1em';
    svg.style.height = '1em';

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute(
        'd',
        'M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25Zm17.71-10.04a.996.996 0 0 0 0-1.41l-2.34-2.34a.996.996 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z'
    );
    svg.append(path);
    return svg;
}

function launchWelcomeConfetti() {
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

function scheduleWelcomeConfetti() {
    window.requestAnimationFrame(() => {
        window.setTimeout(launchWelcomeConfetti, 160);
    });
}

function renderUserWelcome() {
    hideDisplayNameEditor();

    const displayName = currentDisplayName();
    const editButton = document.createElement('button');
    editButton.type = 'button';
    editButton.className = 'welcome-edit-button';
    editButton.append(createWelcomeEditIcon());
    editButton.setAttribute('aria-label', UI_COPY.profile.editLabel);
    editButton.addEventListener('click', startDisplayNameEdit);

    els.welcomeLine.replaceChildren(
        document.createTextNode(UI_COPY.status.userWelcome(displayName)),
        document.createTextNode(' '),
        editButton
    );
}

function setWelcomeText(text) {
    hideDisplayNameEditor();
    els.welcomeLine.textContent = text;
}

function setGuestWelcome() {
    hideDisplayNameEditor();

    const link = document.createElement('a');
    link.href = NIMIQ_PAY_URL;
    link.textContent = 'Nimiq Pay';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.className = 'welcome-link';

    els.welcomeLine.replaceChildren(
        document.createTextNode(UI_COPY.status.guestBeforePay),
        link,
        document.createTextNode(' to identify this device.')
    );
}

function startDisplayNameEdit() {
    if (!state.user || state.banned) return;

    const input = document.createElement('input');
    input.id = 'display-name-input';
    input.className = 'display-name-input';
    input.type = 'text';
    input.value = currentDisplayName();
    input.minLength = DISPLAY_NAME_MIN_LENGTH;
    input.maxLength = DISPLAY_NAME_MAX_LENGTH;
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('aria-label', UI_COPY.profile.inputLabel);

    state.displayNameInput = input;
    els.welcomeLine.replaceChildren(document.createTextNode('Welcome, '), input);
    els.displayNameEditor.hidden = false;
    setDisplayNameError(null);

    input.addEventListener('input', () => {
        const check = setDisplayNameInputState(input);
        setDisplayNameSaveValid(check.ok);
        setDisplayNameError(check.ok ? null : check.message);
    });

    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            saveDisplayNameEdit();
        } else if (event.key === 'Escape') {
            event.preventDefault();
            cancelDisplayNameEdit();
        }
    });

    setDisplayNameSaveValid(setDisplayNameInputState(input).ok);

    requestAnimationFrame(() => {
        input.focus();
        input.select();
    });
}

function cancelDisplayNameEdit() {
    if (state.user) {
        renderUserWelcome();
    } else {
        setGuestWelcome();
    }
}

async function saveDisplayNameEdit() {
    const input = state.displayNameInput;
    if (!input || state.displayNameSaving) return;

    const check = setDisplayNameInputState(input);
    if (!check.ok) {
        setDisplayNameError(check.message);
        input.focus();
        return;
    }

    state.displayNameSaving = true;
    els.displayNameSave.disabled = true;
    els.displayNameSave.classList.remove('is-disabled-by-validation');
    els.displayNameSave.textContent = UI_COPY.profile.saving;
    els.displayNameCancel.disabled = true;

    try {
        const response = await fetch('/api/home/display-name', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(makeDisplayNamePayload(check.displayName)),
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.user) {
            throw new Error(responseErrorText(data));
        }

        state.user = data.user;
        state.banned = Boolean(data.user.is_banned);
        renderUserWelcome();
        updateButtons();
        updateDebug();
    } catch (err) {
        state.displayNameSaving = false;
        els.displayNameSave.textContent = UI_COPY.profile.save;
        els.displayNameCancel.disabled = false;
        setDisplayNameSaveValid(validateDisplayName(input.value).ok);
        setDisplayNameError(err?.message || UI_COPY.profile.saveFailed);
        input.focus();
    }
}

function setButtonEnabled(button, enabled, lockedReason = '') {
    button.classList.toggle('disabled', !enabled);
    button.setAttribute('aria-disabled', enabled ? 'false' : 'true');

    if (enabled) {
        button.removeAttribute('data-tooltip');
    } else {
        button.dataset.tooltip = lockedReason;
    }
}

function hideLockTooltip() {
    els.lockTooltip.hidden = true;
    els.lockTooltip.textContent = '';
    els.lockTooltip.removeAttribute('data-placement');
}

function positionLockTooltip(target) {
    if (els.lockTooltip.hidden) return;

    const gap = 12;
    const edgePadding = 12;
    const targetRect = target.getBoundingClientRect();
    const tooltipRect = els.lockTooltip.getBoundingClientRect();

    let placement = 'top';
    let top = targetRect.top - tooltipRect.height - gap;

    if (top < edgePadding) {
        placement = 'bottom';
        top = targetRect.bottom + gap;
    }

    let left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);
    left = Math.max(edgePadding, Math.min(left, window.innerWidth - tooltipRect.width - edgePadding));

    els.lockTooltip.style.left = `${Math.round(left)}px`;
    els.lockTooltip.style.top = `${Math.round(top)}px`;
    els.lockTooltip.dataset.placement = placement;
}

function showLockTooltipFor(target) {
    const text = target.dataset.tooltip;
    if (target.getAttribute('aria-disabled') !== 'true' || !text) return;

    els.lockTooltip.textContent = text;
    els.lockTooltip.hidden = false;
    els.lockTooltip.dataset.placement = 'top';

    requestAnimationFrame(() => positionLockTooltip(target));
}

function blockDisabledLink(event) {
    const link = event.currentTarget;
    if (link.getAttribute('aria-disabled') === 'true') {
        event.preventDefault();
        showLockTooltipFor(link);
    }
}

const showNotice = createNoticePresenter(els);

function renderHomeMetrics(metrics) {
    if (!els.metrics || !els.activeSpotsMetric || !els.dailyUsersMetric) return;

    const activeSpots = Number(metrics?.active_spot_count || 0);
    const dailyUsers = Number(metrics?.daily_user_count || 0);

    els.activeSpotsMetric.textContent = UI_COPY.metrics.activeSpots(activeSpots);
    els.dailyUsersMetric.textContent = UI_COPY.metrics.dailyUsers(dailyUsers);
    els.metrics.hidden = false;
}

async function loadHomeMetrics() {
    if (!els.metrics) return;

    try {
        const response = await fetch('/api/home/metrics');
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) throw new Error('Metrics request failed.');
        renderHomeMetrics(data.metrics || {});
    } catch (err) {
        console.error(err);
        // Metrics are decorative. Do not interrupt the Home page if they fail.
        els.metrics.hidden = true;
    }
}

function updateDebug() {
    els.debugWallet.textContent = state.walletAvailable
        ? UI_COPY.debug.available
        : UI_COPY.debug.notAvailable;

    // Home no longer requests geolocation. The Find Spots page owns that check.
    els.debugLocation.textContent = UI_COPY.debug.locationNotRequested;

    els.debugLanguage.textContent = state.language || UI_COPY.debug.unknown;
    els.debugUser.textContent = state.user
        ? UI_COPY.debug.userLoaded(state.user)
        : UI_COPY.debug.userNotLoaded;
}

function getLockedReason() {
    if (state.banned) return UI_COPY.locked.accountUnavailable;
    if (!state.user) return UI_COPY.locked.userRequired;
    return UI_COPY.locked.walletRequired;
}

function updateButtons() {
    const hasUser = Boolean(state.user);
    const usableAccount = hasUser && !state.banned;

    // Finding public Spots does not require a Home-page account or location check.
    // The Find Spots page requests location only when it needs it.
    setButtonEnabled(els.findSpotsButton, true);
    setButtonEnabled(els.mySpotsButton, usableAccount && state.walletAvailable, UI_COPY.locked.walletRequired);
    setButtonEnabled(els.myClaimsButton, usableAccount && state.walletAvailable, UI_COPY.locked.walletRequired);
}

function setDebugPanelOpen(open) {
    els.debugPanel.hidden = !open;
    els.debugToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

async function requestWalletDeviceId() {
    try {
        state.deviceIdHash = await requestDeviceIdentifierHash(
            requestDeviceIdentifier,
            UI_COPY.nimiqPay.deviceIdReason,
        );
        state.walletAvailable = true;
        els.connectionLine.textContent = UI_COPY.status.connectedPay;
    } catch (err) {
        state.walletAvailable = false;
        state.deviceIdHash = null;
        els.connectionLine.textContent = UI_COPY.status.notConnectedPay;
        setGuestWelcome();
    }
}

async function postSession() {
    const response = await fetch('/api/home/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            device_id_hash: state.deviceIdHash,
            wallet_available: state.walletAvailable,
            language: state.language,
            location_available: false,
            lat: null,
            long: null,
            accuracy: null,
        }),
    });

    const data = await response.json();

    if (data.test_user) {
        // Development convenience: the server has deliberately supplied a test
        // user even though Nimiq Pay was unavailable in this browser.
        state.walletAvailable = true;
        els.connectionLine.textContent = UI_COPY.status.testUser;
    }

    if (data.user) {
        state.user = data.user;
        state.banned = Boolean(data.user.is_banned);
        renderUserWelcome();
    } else {
        state.user = null;
        state.banned = false;

        if (data.code === 'wallet_unavailable' || !state.walletAvailable) {
            setGuestWelcome();
            showNotice(UI_COPY.notices.walletUnavailable);
        } else {
            setWelcomeText(data.message || UI_COPY.status.guestWelcome);
        }
    }

    if (data.code === 'test_user_missing') {
        showNotice({
            ...UI_COPY.notices.testUserMissing,
            body: data.message || UI_COPY.notices.testUserMissing.body,
        });
        return;
    }

    if (data.code === 'banned') {
        showNotice({
            ...UI_COPY.notices.banned,
            body: data.message || UI_COPY.notices.banned.body,
        });
        return;
    }

    if (data.created && state.user && !state.banned) {
        showNotice(UI_COPY.notices.firstVisit);
        scheduleWelcomeConfetti();
    }
}

async function initHome() {
    state.language = getLanguage();
    updateDebug();
    updateButtons();
    setDebugPanelOpen(false);

    await requestWalletDeviceId();

    await postSession();
    await loadHomeMetrics();

    updateButtons();
    updateDebug();
}

els.noticeOk.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

els.debugToggle.addEventListener('click', () => {
    setDebugPanelOpen(els.debugPanel.hidden);
});

for (const button of [els.findSpotsButton, els.mySpotsButton, els.myClaimsButton]) {
    button.addEventListener('click', blockDisabledLink);
    button.addEventListener('mouseenter', () => showLockTooltipFor(button));
    button.addEventListener('focus', () => showLockTooltipFor(button));
    button.addEventListener('mouseleave', hideLockTooltip);
    button.addEventListener('blur', hideLockTooltip);
}

if (els.displayNameSave && els.displayNameCancel) {
    els.displayNameSave.addEventListener('click', saveDisplayNameEdit);
    els.displayNameCancel.addEventListener('click', cancelDisplayNameEdit);
}

window.addEventListener('scroll', hideLockTooltip, { passive: true });
window.addEventListener('resize', hideLockTooltip);

initHome().catch((err) => {
    console.error(err);
    els.connectionLine.textContent = UI_COPY.status.notConnectedPay;
    setGuestWelcome();
    showNotice({
        ...UI_COPY.notices.setupFailed,
        body: err?.message || UI_COPY.notices.setupFailed.body,
    });
    updateButtons();
    updateDebug();
});
