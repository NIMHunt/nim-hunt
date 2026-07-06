import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    locationAvailable: false,
    lat: null,
    long: null,
    accuracy: null,
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
const UI_COPY = {
    nimiqPay: {
        deviceIdReason: `Create or find your ${APP_NAME} device account.`,
    },
    notices: {
        walletUnavailable: {
            title: `Open ${APP_NAME} in Nimiq Pay`,
            body: `${APP_NAME} needs Nimiq Pay to identify this device. My Spots and My Claims are locked until this app is opened inside Nimiq Pay.`,
        },
        testUserMissing: {
            title: 'Test user missing',
            body: 'Desktop test mode is enabled, but the mock test user does not exist. Run spoof.py, then reload this page.',
        },
        banned: {
            title: 'Account unavailable',
            body: `This device account can no longer use ${APP_NAME}.`,
        },
        setupFailed: {
            title: 'Home setup failed',
            body: `${APP_NAME} could not initialise the home page. Reload the mini app or open it again from Nimiq Pay.`,
        },
        firstVisit: {
            title: `Welcome to ${APP_NAME}`,
            body: `Your ${APP_NAME} device account has been created. You can now find spots, create spots, and track your claims from this device.`,
            buttonText: "Let's Go!",
        },
    },
    metrics: {
        activeSpots: (n) => `${n.toLocaleString()} Active ${n === 1 ? 'Spot' : 'Spots'}`,
        dailyUsers: (n) => `${n.toLocaleString()} Daily ${n === 1 ? 'User' : 'Users'}`,
    },
    profile: {
        editLabel: 'Edit display name',
        inputLabel: 'Display name',
        save: 'Save',
        saving: 'Saving…',
        cancel: 'Cancel',
        invalidLength: () => `Display name must be between ${DISPLAY_NAME_MIN_LENGTH} and ${DISPLAY_NAME_MAX_LENGTH} characters.`,
        saveFailed: 'Display name could not be saved. Try again.',
        invalidResponse: 'The server did not understand the display-name update.',
    },
    status: {
        checkingPay: 'Checking Nimiq Pay…',
        connectedPay: 'Connected through Nimiq Pay.',
        notConnectedPay: 'Not connected through Nimiq Pay.',
        testUser: 'Using desktop test user.',
        guestWelcome: `Open ${APP_NAME} inside Nimiq Pay to identify this device.`,
        userWelcome: (displayName) => `Welcome, ${displayName}`,
        userFallback: (id) => `User ${id}`,
    },
    locked: {
        walletRequired: 'This feature requires Nimiq Pay.',
        accountUnavailable: `This account cannot use ${APP_NAME}.`,
        userRequired: `Open ${APP_NAME} in Nimiq Pay first.`,
        locationRequired: 'Find Spots requires location access.',
    },
    debug: {
        available: 'available',
        notAvailable: 'not available',
        locationNotRequested: 'not requested on Home',
        unknown: 'unknown',
        userNotLoaded: 'not loaded',
        userLoaded: (user) => `${user.display_name || UI_COPY.status.userFallback(user.id)}(#${user.id})`,
    },
};

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

    if (typeof detail === 'string' && detail.trim()) return detail;

    if (Array.isArray(detail)) {
        const messages = detail
            .map((item) => item?.msg || item?.message || item?.detail)
            .filter(Boolean);

        if (messages.length > 0) return messages.join(' ');
        return UI_COPY.profile.invalidResponse;
    }

    if (detail && typeof detail === 'object') {
        return detail.msg || detail.message || detail.detail || UI_COPY.profile.invalidResponse;
    }

    if (typeof data?.message === 'string' && data.message.trim()) return data.message;

    return fallback;
}

function renderUserWelcome() {
    hideDisplayNameEditor();

    const displayName = currentDisplayName();
    const editButton = document.createElement('button');
    editButton.type = 'button';
    editButton.className = 'welcome-edit-button';
    editButton.textContent = '🖉';
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
        document.createTextNode(`Open ${APP_NAME} inside `),
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

function showNotice({ title, body, href = null, linkText = 'Read more', buttonText = 'OK' }) {
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

    els.debugLocation.textContent = state.locationAvailable
        ? UI_COPY.debug.available
        : UI_COPY.debug.notAvailable;

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

function getFindSpotsLockedReason() {
    if (state.banned) return UI_COPY.locked.accountUnavailable;
    if (!state.locationAvailable) return UI_COPY.locked.locationRequired;
    return '';
}

function updateButtons() {
    const hasUser = Boolean(state.user);
    const usableAccount = hasUser && !state.banned;

    setButtonEnabled(els.findSpotsButton, !state.banned && state.locationAvailable, getFindSpotsLockedReason());
    setButtonEnabled(els.mySpotsButton, usableAccount && state.walletAvailable, UI_COPY.locked.walletRequired);
    setButtonEnabled(els.myClaimsButton, usableAccount && state.walletAvailable, UI_COPY.locked.walletRequired);
}

function setDebugPanelOpen(open) {
    els.debugPanel.hidden = !open;
    els.debugToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
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

async function requestWalletDeviceId() {
    try {
        const id = await requestDeviceIdentifier({
            reason: UI_COPY.nimiqPay.deviceIdReason,
        });

        if (typeof id === 'string' && /^[0-9a-fA-F]{64}$/.test(id)) {
            state.walletAvailable = true;
            state.deviceIdHash = id.toLowerCase();
            els.connectionLine.textContent = UI_COPY.status.connectedPay;
            return;
        }

        throw new Error('Nimiq Pay returned an invalid device identifier.');
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
            location_available: state.locationAvailable,
            lat: state.lat,
            long: state.long,
            accuracy: state.accuracy,
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
    }
}

async function initHome() {
    state.language = getLanguage();
    updateDebug();
    updateButtons();
    setDebugPanelOpen(false);

    const location = await requestLocation();
    if (location) {
        state.locationAvailable = true;
        state.lat = location.lat;
        state.long = location.long;
        state.accuracy = location.accuracy;
    } else {
        state.locationAvailable = false;
        state.lat = null;
        state.long = null;
        state.accuracy = null;
    }

    updateButtons();
    updateDebug();

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
