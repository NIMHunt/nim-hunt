import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { COMMON_TEXT, makeCreateSpotFormText } from './interface_text.js?v=remove-help-pages-v1-20260705';
import { LUNA_PER_NIM, formatNimAmount } from './nim_format.js';
const DEFAULT_MAP_CENTRE = [51.5074, -0.1278];
const DEFAULT_MAP_ZOOM = 13;

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    userLocation: null,
    spot: null,
    map: null,
    radiusCircle: null,
    reverseController: null,
    reverseTimer: null,
    city: null,
    country: null,
    saveInProgress: false,
    deleteInProgress: false,
    totalValueLocked: false,
    initialSnapshot: null,
    hydrating: true,
};

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const SPOT_ID = Number.parseInt(document.body.dataset.spotId || '0', 10);
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';
const TITLE_MIN = Number.parseInt(document.body.dataset.spotTitleMin || '3', 10);
const TITLE_MAX = Number.parseInt(document.body.dataset.spotTitleMax || '18', 10);
const MIN_RADIUS = Number.parseInt(document.body.dataset.minSpotRadius || '25', 10);
const MAX_RADIUS = Number.parseInt(document.body.dataset.maxSpotRadius || '1000', 10);
const MIN_DURATION = Number.parseInt(document.body.dataset.minClaimDuration || '0', 10);
const MIN_NONZERO_DURATION = Number.parseInt(document.body.dataset.minNonzeroClaimDuration || String(10 * 60), 10);
const MAX_DURATION = Number.parseInt(document.body.dataset.maxClaimDuration || String(12 * 60 * 60), 10);
const MIN_CLAIMS_PER_USER = Number.parseInt(document.body.dataset.minClaimsPerUser || '0', 10);
const MAX_CLAIMS_PER_USER = Number.parseInt(document.body.dataset.maxClaimsPerUser || '10', 10);
const MIN_STANDARD_TOTAL_PARTICIPANTS = Number.parseInt(document.body.dataset.minStandardTotalParticipants || '1', 10);
const MAX_TOTAL_PARTICIPANTS = Number.parseInt(document.body.dataset.maxTotalParticipants || '1000', 10);
const MIN_TOTAL_NIM = Number.parseFloat(document.body.dataset.minTotalNim || '100');
const MIN_STANDARD_CLAIM_PAYOUT_NIM = Number.parseFloat(document.body.dataset.minStandardClaimPayoutNim || '100');
const MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM = Number.parseFloat(document.body.dataset.minPrizedrawPrizePayoutNim || '1000');
const MIN_ENDS_AFTER = Number.parseInt(document.body.dataset.minEndsAfter || String(60 * 60), 10);
const MAX_ENDS_AFTER = Number.parseInt(document.body.dataset.maxEndsAfter || String(4 * 7 * 24 * 60 * 60), 10);
const DEFAULT_ENDS_AFTER = Number.parseInt(document.body.dataset.defaultEndsAfter || String(7 * 24 * 60 * 60), 10);
const PRIZEDRAW_PRIZE_COUNT_OPTIONS = (document.body.dataset.prizedrawPrizeCountOptions || '1,2,3,4,5,6,7,8,9,10,11,12,15,20,25,50,100')
    .split(',')
    .map((value) => Number.parseInt(value.trim(), 10))
    .filter(Number.isFinite);

const TEXT = makeCreateSpotFormText({
    appName: APP_NAME,
    nimiqPayUrl: NIMIQ_PAY_URL,
});

const els = {
    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),
    tooltip: document.getElementById('create-spot-tooltip'),

    loading: document.getElementById('create-spot-loading'),
    card: document.getElementById('create-spot-card'),
    form: document.getElementById('create-spot-form'),
    error: document.getElementById('create-spot-error'),
    save: document.getElementById('create-spot-save'),
    delete: document.getElementById('create-spot-delete'),
    deleteBackdrop: document.getElementById('delete-spot-backdrop'),
    deleteTitle: document.getElementById('delete-spot-title'),
    deleteBody: document.getElementById('delete-spot-body'),
    deleteConfirm: document.getElementById('delete-spot-confirm'),
    deleteCancel: document.getElementById('delete-spot-cancel'),

    title: document.getElementById('spot-title-input'),
    city: document.getElementById('create-spot-city'),
    country: document.getElementById('create-spot-country'),
    map: document.getElementById('create-spot-map'),
    latitude: document.getElementById('create-spot-latitude'),
    longitude: document.getElementById('create-spot-longitude'),
    description: document.getElementById('spot-description-input'),

    radius: document.getElementById('spot-radius-input'),
    radiusValue: document.getElementById('spot-radius-value'),

    claimDuration: document.getElementById('spot-duration-input'),
    claimDurationValue: document.getElementById('spot-duration-value'),

    maxClaimsPerUser: document.getElementById('spot-max-user-input'),
    maxClaimsPerUserValue: document.getElementById('spot-max-user-value'),

    maxTotalClaims: document.getElementById('spot-max-total-input'),
    maxTotalClaimsValue: document.getElementById('spot-max-total-value'),
    maxTotalClaimsRow: document.getElementById('spot-total-participants-row'),

    prizeCount: document.getElementById('spot-prize-count-input'),
    prizeCountValue: document.getElementById('spot-prize-count-value'),
    prizeCountRow: document.getElementById('spot-prize-count-row'),

    totalValue: document.getElementById('spot-total-value-input'),
    totalValueLocked: document.getElementById('spot-total-value-locked'),
    totalValueRate: document.getElementById('spot-total-value-rate'),
    startsAt: document.getElementById('spot-starts-input'),
    endsAt: document.getElementById('spot-ends-input'),
    endsAtValue: document.getElementById('spot-ends-value'),
    usePassword: document.getElementById('spot-use-password-input'),
    usePasswordRow: document.getElementById('spot-use-password-row'),
    helpButtons: document.querySelectorAll('.create-spot-label-help'),
};

const radiusOptions = buildRadiusOptions();
const durationOptions = buildDurationOptions();
const perUserOptions = buildIntegerOptions(MIN_CLAIMS_PER_USER, MAX_CLAIMS_PER_USER);
const standardParticipantOptions = buildParticipantOptions(false);
const prizedrawParticipantOptions = buildParticipantOptions(true);
const endsAfterOptions = buildEndsAfterOptions();
const prizeCountOptions = buildPrizeCountOptions();

const sliderControls = {
    radius: {
        range: els.radius,
        bubble: els.radiusValue,
        options: radiusOptions,
        display: formatRadiusValue,
    },
    duration: {
        range: els.claimDuration,
        bubble: els.claimDurationValue,
        options: durationOptions,
        display: formatDurationValue,
    },
    perUser: {
        range: els.maxClaimsPerUser,
        bubble: els.maxClaimsPerUserValue,
        options: perUserOptions,
        display: formatUnlimitedZeroValue,
    },
    totalParticipants: {
        range: els.maxTotalClaims,
        bubble: els.maxTotalClaimsValue,
        options: standardParticipantOptions,
        display: formatUnlimitedZeroValue,
    },
    prizeCount: {
        range: els.prizeCount,
        bubble: els.prizeCountValue,
        options: prizeCountOptions,
        display: (value) => Number(value).toLocaleString(),
    },
    endsAfter: {
        range: els.endsAt,
        bubble: els.endsAtValue,
        options: endsAfterOptions,
        display: formatEndsAfterValue,
    },
};

function showNotice({ title, body, href = null, linkText = COMMON_TEXT.notice.readMore, buttonText = COMMON_TEXT.notice.ok }) {
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

function hideHelpTooltip() {
    if (!els.tooltip) return;
    els.tooltip.hidden = true;
    els.tooltip.textContent = '';
    els.tooltip.removeAttribute('data-placement');
}

function positionHelpTooltip(target) {
    if (!els.tooltip || els.tooltip.hidden) return;

    const gap = 12;
    const edgePadding = 12;
    const targetRect = target.getBoundingClientRect();
    const tooltipRect = els.tooltip.getBoundingClientRect();

    let placement = 'top';
    let top = targetRect.top - tooltipRect.height - gap;
    if (top < edgePadding) {
        placement = 'bottom';
        top = targetRect.bottom + gap;
    }

    let left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);
    left = Math.max(edgePadding, Math.min(left, window.innerWidth - tooltipRect.width - edgePadding));

    els.tooltip.style.left = `${Math.round(left)}px`;
    els.tooltip.style.top = `${Math.round(top)}px`;
    els.tooltip.dataset.placement = placement;
}

function showHelpTooltip(target) {
    if (!els.tooltip) return;
    const text = target?.dataset?.tooltip;
    if (!text) return;

    els.tooltip.textContent = text;
    els.tooltip.hidden = false;
    els.tooltip.dataset.placement = 'top';
    requestAnimationFrame(() => positionHelpTooltip(target));
}

function showSaveLockedTooltip() {
    if (!els.save || els.save.getAttribute('aria-disabled') !== 'true' || !els.save.dataset.tooltip) return;
    showHelpTooltip(els.save);
}

function responseErrorText(data, fallback) {
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
        const messages = detail.map((item) => item?.msg || item?.message || item?.detail).filter(Boolean);
        if (messages.length > 0) return messages.join(' ');
    }
    if (typeof data?.message === 'string' && data.message.trim()) return data.message;
    return fallback;
}

function redirectHome() {
    window.location.replace('/');
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
            (pos) => resolve({
                lat: pos.coords.latitude,
                long: pos.coords.longitude,
                accuracy: pos.coords.accuracy,
            }),
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
            reason: TEXT.nimiqPay.deviceIdReason,
        });

        if (typeof id === 'string' && /^[0-9a-fA-F]{64}$/.test(id)) {
            state.walletAvailable = true;
            state.deviceIdHash = id.toLowerCase();
            return;
        }

        throw new Error('Nimiq Pay returned an invalid device identifier.');
    } catch (err) {
        state.walletAvailable = false;
        state.deviceIdHash = null;
    }
}

function authPayload() {
    return {
        device_id_hash: state.deviceIdHash,
        wallet_available: state.walletAvailable,
        language: state.language,
        location_available: Boolean(state.userLocation),
        lat: state.userLocation?.lat ?? null,
        long: state.userLocation?.long ?? null,
        accuracy: state.userLocation?.accuracy ?? null,
    };
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
        const err = new Error(responseErrorText(data, 'Request failed.'));
        err.data = data;
        err.status = response.status;
        throw err;
    }
    return data;
}

async function loadSpot() {
    return fetchJson(`/api/create-spot/${SPOT_ID}/detail`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authPayload()),
    });
}

function setError(message) {
    if (message) {
        els.error.textContent = message;
        els.error.hidden = false;
    } else {
        els.error.textContent = '';
        els.error.hidden = true;
    }
}

function lunaToNimInput(value) {
    const nim = Number(value || 0) / LUNA_PER_NIM;
    return Number.isFinite(nim) && nim > 0 ? nim.toFixed(5).replace(/0+$/, '').replace(/\.$/, '') : '';
}

function nimInputToLuna(value) {
    const nim = Number.parseFloat(String(value || '').trim());
    const safeNim = Number.isFinite(nim) ? Math.max(MIN_TOTAL_NIM, nim) : MIN_TOTAL_NIM;
    return Math.round(safeNim * LUNA_PER_NIM);
}

function normaliseTotalValueInput() {
    if (state.totalValueLocked) {
        updateTotalValueRate();
        updateSaveButtonState();
        return;
    }

    const nim = Number.parseFloat(String(els.totalValue.value || '').trim());
    if (!Number.isFinite(nim) || nim < MIN_TOTAL_NIM) {
        els.totalValue.value = String(MIN_TOTAL_NIM);
    }
    updateTotalValueRate();
    updateSaveButtonState();
}



function currentTotalNim() {
    if (state.totalValueLocked) {
        const lockedNim = Number(state.spot?.total_value || 0) / LUNA_PER_NIM;
        return Number.isFinite(lockedNim) ? lockedNim : 0;
    }

    const nim = Number.parseFloat(String(els.totalValue?.value || '').trim());
    return Number.isFinite(nim) ? nim : 0;
}

function updateTotalValueRate() {
    if (!els.totalValueRate) return;

    const totalNim = currentTotalNim();
    if (!Number.isFinite(totalNim) || totalNim <= 0) {
        els.totalValueRate.textContent = '';
        return;
    }

    if (isPrizedrawForm()) {
        const prizeCount = Math.max(1, Number(sliderValue('prizeCount') || 1));
        els.totalValueRate.textContent = TEXT.form.perPrize(formatNimAmount(totalNim / prizeCount));
        return;
    }

    const participants = Number(sliderValue('totalParticipants') || 0);
    if (participants <= 0) {
        els.totalValueRate.textContent = '';
        return;
    }
    els.totalValueRate.textContent = TEXT.form.perClaim(formatNimAmount(totalNim / participants));
}

function unixToDateTimeLocal(value) {
    if (!value) return '';
    const date = new Date(Number(value) * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function dateTimeLocalToUnix(value) {
    if (!value) return null;
    const ms = new Date(value).getTime();
    if (!Number.isFinite(ms)) return null;
    return Math.floor(ms / 1000);
}

function currentUnixSeconds() {
    return Math.floor(Date.now() / 1000);
}

function updateStartsAtMin() {
    if (!els.startsAt) return;
    // datetime-local does not include seconds; add a minute so the browser UI
    // cannot choose a value that is already stale by the time it is saved.
    els.startsAt.min = unixToDateTimeLocal(currentUnixSeconds() + 60);
}

function startsAtValidation() {
    if (!els.startsAt?.value) return { ok: true, message: '' };

    const startsAt = dateTimeLocalToUnix(els.startsAt.value);
    if (startsAt === null) {
        return { ok: false, message: TEXT.form.startsInvalid };
    }

    if (startsAt <= currentUnixSeconds()) {
        return { ok: false, message: TEXT.form.startsInPast || TEXT.form.startsInvalid };
    }

    return { ok: true, message: '' };
}

function clampNumber(value, min, max, fallback = min) {
    const number = Number.parseInt(String(value), 10);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(min, Math.min(max, number));
}

function uniqueSortedNumbers(values) {
    return [...new Set(values.map((value) => Number(value)).filter(Number.isFinite))]
        .sort((a, b) => a - b);
}

function buildIntegerOptions(min, max) {
    const values = [];
    for (let value = Number(min); value <= Number(max); value += 1) {
        values.push(value);
    }
    return uniqueSortedNumbers(values);
}

function buildSteppedOptions(groups) {
    const values = [];
    for (const { start, end, step } of groups) {
        for (let value = start; value <= end; value += step) {
            values.push(value);
        }
    }
    return uniqueSortedNumbers(values).filter((value) => value >= MIN_RADIUS && value <= MAX_RADIUS);
}

function buildRadiusOptions() {
    return buildSteppedOptions([
        { start: 25, end: 50, step: 5 },
        { start: 60, end: 100, step: 10 },
        { start: 125, end: 250, step: 25 },
        { start: 300, end: 500, step: 50 },
        { start: 600, end: 1000, step: 100 },
    ]);
}

function buildDurationOptions() {
    const values = [0];
    const minuteStep = 5 * 60;
    const hour = 60 * 60;

    for (let value = MIN_NONZERO_DURATION; value <= Math.min(hour, MAX_DURATION); value += minuteStep) {
        values.push(value);
    }

    for (let value = 2 * hour; value <= MAX_DURATION; value += hour) {
        values.push(value);
    }

    return uniqueSortedNumbers(values);
}

function uniqueNumbersInOrder(values) {
    const seen = new Set();
    const out = [];
    for (const rawValue of values) {
        const value = Number(rawValue);
        if (!Number.isFinite(value) || seen.has(value)) continue;
        seen.add(value);
        out.push(value);
    }
    return out;
}

function buildParticipantOptions(includeUnlimited) {
    const values = [];

    for (let value = MIN_STANDARD_TOTAL_PARTICIPANTS; value <= Math.min(10, MAX_TOTAL_PARTICIPANTS); value += 1) {
        values.push(value);
    }

    for (let value = 20; value <= Math.min(100, MAX_TOTAL_PARTICIPANTS); value += 10) {
        values.push(value);
    }

    for (let value = 200; value <= MAX_TOTAL_PARTICIPANTS; value += 100) {
        values.push(value);
    }

    const sortedFiniteValues = uniqueSortedNumbers(values);
    return includeUnlimited ? [...sortedFiniteValues, 0] : sortedFiniteValues;
}

function buildPrizeCountOptions() {
    return uniqueSortedNumbers(PRIZEDRAW_PRIZE_COUNT_OPTIONS.length ? PRIZEDRAW_PRIZE_COUNT_OPTIONS : [1]);
}

function buildEndsAfterOptions() {
    const hour = 60 * 60;
    const day = 24 * hour;
    const week = 7 * day;
    return uniqueSortedNumbers([
        1 * hour,
        2 * hour,
        3 * hour,
        4 * hour,
        6 * hour,
        12 * hour,
        1 * day,
        2 * day,
        3 * day,
        4 * day,
        1 * week,
        2 * week,
        3 * week,
        4 * week,
    ]).filter((value) => value >= MIN_ENDS_AFTER && value <= MAX_ENDS_AFTER);
}

function nearestOption(value, options) {
    const number = Number(value);
    if (!Number.isFinite(number)) return options[0] ?? 0;

    let best = options[0] ?? 0;
    let bestDistance = Math.abs(number - best);
    for (const option of options) {
        const distance = Math.abs(number - option);
        if (distance < bestDistance) {
            best = option;
            bestDistance = distance;
        }
    }
    return best;
}

function optionIndexForValue(value, options) {
    const nearest = nearestOption(value, options);
    return Math.max(0, options.indexOf(nearest));
}

function formatRadiusValue(value) {
    return `${Math.round(Number(value || 0))} m`;
}

function formatDurationValue(value) {
    const seconds = Number(value || 0);
    if (seconds <= 0) return 'None';
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    const hours = seconds / 3600;
    return `${hours.toFixed(seconds % 3600 === 0 ? 0 : 1)} hr`;
}

function formatEndsAfterValue(value) {
    const seconds = Number(value || 0);
    const hour = 60 * 60;
    const day = 24 * hour;
    const week = 7 * day;
    if (seconds < day) {
        const hours = Math.round(seconds / hour);
        return `${hours} ${hours === 1 ? 'Hour' : 'Hours'}`;
    }
    if (seconds < week) {
        const days = Math.round(seconds / day);
        return `${days} ${days === 1 ? 'Day' : 'Days'}`;
    }
    const weeks = Math.round(seconds / week);
    return `${weeks} ${weeks === 1 ? 'Week' : 'Weeks'}`;
}

function formatUnlimitedZeroValue(value) {
    return Number(value) <= 0 ? 'Unlimited' : Number(value).toLocaleString();
}

function hasSavedCoordinates(spot) {
    return spot.lat !== null
        && spot.lat !== undefined
        && spot.long !== null
        && spot.long !== undefined
        && Number.isFinite(Number(spot.lat))
        && Number.isFinite(Number(spot.long));
}

function configureRangeInput(control) {
    if (!control?.range) return;
    const options = control.options || [];
    control.range.min = '0';
    control.range.max = String(Math.max(0, options.length - 1));
    control.range.step = '1';
}

function normaliseControlValue(control, value) {
    return nearestOption(value, control.options || [0]);
}

function rangePositionForValue(control, value) {
    return optionIndexForValue(value, control.options || [0]);
}

function valueForRangePosition(control, position) {
    const options = control.options || [0];
    const index = clampNumber(position, 0, options.length - 1, 0);
    return options[index] ?? options[0] ?? 0;
}

function positionSliderBubble(control, rangePosition) {
    if (!control?.bubble || !control?.range) return;

    const min = Number(control.range.min || 0);
    const max = Number(control.range.max || 0);
    const pct = (Number(rangePosition) - min) / Math.max(1, max - min);
    const rangeRect = control.range.getBoundingClientRect();
    const wrapRect = control.range.parentElement.getBoundingClientRect();
    const bubbleRect = control.bubble.getBoundingClientRect();

    if (!rangeRect.width || !wrapRect.width || !bubbleRect.width) {
        control.bubble.style.left = '50%';
        return;
    }

    const thumbX = (rangeRect.left - wrapRect.left) + (pct * rangeRect.width);
    const minLeft = bubbleRect.width / 2;
    const maxLeft = wrapRect.width - (bubbleRect.width / 2);
    const left = Math.max(minLeft, Math.min(maxLeft, thumbX));

    control.bubble.style.left = `${left}px`;
}

function setSliderOptions(name, options) {
    const control = sliderControls[name];
    if (!control || !control.range) return;

    // Preserve caller order. Most sliders are numerically sorted already, but
    // Prizedraw total participants deliberately puts 0 / Unlimited at the far
    // right of the slider rather than the far left.
    control.options = uniqueNumbersInOrder(options);
    configureRangeInput(control);
}


function participantLimitIsFinite(value = sliderValue('totalParticipants')) {
    return Number(value) > 0;
}

function enforcePerUserTotalParticipantRelationship(changedName) {
    const perUser = Number(sliderValue('perUser') || 0);
    const totalParticipants = Number(sliderValue('totalParticipants') || 0);

    // 0 means Unlimited for either slider, so only finite positive values are constrained.
    if (perUser <= 0 || totalParticipants <= 0) return;

    if (changedName === 'perUser' && perUser > totalParticipants) {
        setSliderValue('totalParticipants', perUser, { updateState: false });
        return;
    }

    if (changedName === 'totalParticipants' && totalParticipants < perUser) {
        setSliderValue('perUser', totalParticipants, { updateState: false });
    }
}

function enforcePrizedrawParticipantPrizeRelationship(changedName) {
    if (!isPrizedrawForm()) return;

    const totalParticipants = Number(sliderValue('totalParticipants') || 0);
    const prizeCount = Number(sliderValue('prizeCount') || 0);

    // 0 means Unlimited total participants for Prizedraws, so it does not
    // constrain the finite number of prizes.
    if (totalParticipants <= 0 || prizeCount <= 0) return;

    if (changedName === 'totalParticipants' && totalParticipants < prizeCount) {
        setSliderValue('prizeCount', totalParticipants, { updateState: false });
        return;
    }

    if (changedName === 'prizeCount' && prizeCount > totalParticipants) {
        setSliderValue('totalParticipants', prizeCount, { updateState: false });
    }
}

function setSliderValue(name, value, { updateState = true } = {}) {
    const control = sliderControls[name];
    if (!control || !control.range) return;

    configureRangeInput(control);
    const actualValue = normaliseControlValue(control, value);
    const rangePosition = rangePositionForValue(control, actualValue);

    control.range.value = String(rangePosition);
    control.bubble.textContent = control.display(actualValue);
    requestAnimationFrame(() => positionSliderBubble(control, rangePosition));

    if (name === 'perUser' || name === 'totalParticipants') {
        enforcePerUserTotalParticipantRelationship(name);
    }

    if (name === 'totalParticipants' || name === 'prizeCount') {
        enforcePrizedrawParticipantPrizeRelationship(name);
    }

    if (name === 'radius') {
        updateRadiusCircle(actualValue);
    }

    if (name === 'totalParticipants' || name === 'prizeCount') {
        updateTotalValueRate();
    }

    updateIgnoredRows();
    if (updateState) updateSaveButtonState();
}

function sliderValue(name) {
    const control = sliderControls[name];
    if (!control || !control.range) return 0;
    configureRangeInput(control);
    return valueForRangePosition(control, control.range.value);
}

function updateIgnoredRows() {
    const ignored = {
        duration: sliderValue('duration') <= 0,
        perUser: sliderValue('perUser') <= 0,
        totalParticipants: sliderValue('totalParticipants') <= 0,
    };

    for (const row of document.querySelectorAll('[data-ignore-when-zero]')) {
        const key = row.dataset.ignoreWhenZero;
        row.classList.toggle('is-ignored', Boolean(ignored[key]));
    }
}

function bindSliderControl(name) {
    const control = sliderControls[name];
    if (!control || !control.range) return;

    configureRangeInput(control);

    control.range.addEventListener('input', () => {
        setSliderValue(name, valueForRangePosition(control, control.range.value));
    });
}

function setLocationLines({ city, country, lat, long } = {}) {
    if (Object.prototype.hasOwnProperty.call(arguments[0] || {}, 'city')) {
        state.city = city || null;
        els.city.textContent = city || TEXT.form.locationNotSet;
        els.city.hidden = false;
    }
    if (Object.prototype.hasOwnProperty.call(arguments[0] || {}, 'country')) {
        state.country = country || null;
        els.country.textContent = country || '';
        els.country.hidden = !country;
    }
    if (Object.prototype.hasOwnProperty.call(arguments[0] || {}, 'lat')) {
        const ok = Number.isFinite(Number(lat));
        els.latitude.textContent = ok ? Number(lat).toFixed(5) : '';
        els.latitude.hidden = !ok;
    }
    if (Object.prototype.hasOwnProperty.call(arguments[0] || {}, 'long')) {
        const ok = Number.isFinite(Number(long));
        els.longitude.textContent = ok ? Number(long).toFixed(5) : '';
        els.longitude.hidden = !ok;
    }
}

function setLatLongFromMap() {
    if (!state.map) return;
    const centre = state.map.getCenter();
    state.spot.lat = centre.lat;
    state.spot.long = centre.lng;
    setLocationLines({ lat: centre.lat, long: centre.lng });
    updateRadiusCircle();
    updateSaveButtonState();
}

function chosenStartPoint(spot) {
    if (hasSavedCoordinates(spot)) {
        return [Number(spot.lat), Number(spot.long)];
    }
    if (state.userLocation) {
        return [Number(state.userLocation.lat), Number(state.userLocation.long)];
    }
    return DEFAULT_MAP_CENTRE;
}

function mapRadiusColour() {
    return getComputedStyle(document.documentElement).getPropertyValue('--nh-success').trim() || '#21bca5';
}

function updateRadiusCircle(radius = sliderValue('radius')) {
    if (!state.map || !state.radiusCircle) return;
    const centre = state.map.getCenter();
    state.radiusCircle.setLatLng(centre);
    state.radiusCircle.setRadius(Math.max(1, Number(radius || MIN_RADIUS)));
}

function setupMap(spot) {
    if (!window.L || !els.map) return;

    const start = chosenStartPoint(spot);
    state.map = window.L.map(els.map, {
        zoomControl: true,
        attributionControl: true,
    }).setView(start, DEFAULT_MAP_ZOOM);

    window.L.tileLayer(MAP_TILE_URL, {
        attribution: MAP_TILE_ATTRIBUTION,
        maxZoom: 19,
    }).addTo(state.map);

    const colour = mapRadiusColour();
    state.radiusCircle = window.L.circle(start, {
        radius: sliderValue('radius') || MIN_RADIUS,
        color: colour,
        fillColor: colour,
        fillOpacity: 0.12,
        opacity: 0.78,
        weight: 2,
        interactive: false,
    }).addTo(state.map);

    state.map.on('moveend', () => {
        setLatLongFromMap();
        scheduleReverseGeocode();
    });

    setLatLongFromMap();
    scheduleReverseGeocode(0);
}

function scheduleReverseGeocode(delay = 650) {
    window.clearTimeout(state.reverseTimer);
    state.reverseTimer = window.setTimeout(reverseGeocodeMapCentre, delay);
}

function finishHydration() {
    if (!state.hydrating) return;
    state.hydrating = false;
    setInitialSnapshot();
    updateSaveButtonState();
}

async function reverseGeocodeMapCentre() {
    if (!state.map) {
        finishHydration();
        return;
    }

    const centre = state.map.getCenter();
    if (state.reverseController) state.reverseController.abort();
    state.reverseController = new AbortController();

    try {
        const params = new URLSearchParams({
            lat: String(centre.lat),
            long: String(centre.lng),
        });
        const data = await fetchJson(`/api/location/reverse?${params.toString()}`, {
            signal: state.reverseController.signal,
        });

        setLocationLines({
            city: data.city || TEXT.form.selectedLocation,
            country: data.country || '',
        });
    } catch (err) {
        if (err.name === 'AbortError') return;
        setLocationLines({
            city: state.city || TEXT.form.selectedLocation,
            country: state.country || '',
        });
    } finally {
        finishHydration();
        updateSaveButtonState();
    }
}

function configureSpotTypeControls(spot) {
    const isPrizedraw = Boolean(spot.is_prizedraw);
    const current = sliderValue('totalParticipants');
    setSliderOptions(
        'totalParticipants',
        isPrizedraw ? prizedrawParticipantOptions : standardParticipantOptions
    );
    setSliderValue('totalParticipants', current, { updateState: false });

    if (els.prizeCountRow) els.prizeCountRow.hidden = !isPrizedraw;
    if (els.usePasswordRow) els.usePasswordRow.hidden = isPrizedraw;
    if (isPrizedraw && els.usePassword) els.usePassword.checked = false;
    updateTotalValueRate();
}

function validTitle() {
    const title = els.title.value.trim();
    return title.length >= TITLE_MIN && title.length <= TITLE_MAX;
}

function validTotalNim() {
    const nim = currentTotalNim();
    return Number.isFinite(nim) && nim >= MIN_TOTAL_NIM;
}

function payoutValidation() {
    const totalNim = currentTotalNim();
    if (!Number.isFinite(totalNim) || totalNim <= 0) {
        return { ok: false, kind: isPrizedrawForm() ? 'prize' : 'claim', minimum: 0 };
    }

    if (isPrizedrawForm()) {
        const prizeCount = Math.max(1, Number(sliderValue('prizeCount') || 1));
        const minimum = MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM;
        return {
            ok: (totalNim / prizeCount) >= minimum,
            kind: 'prize',
            minimum,
        };
    }

    const participants = Number(sliderValue('totalParticipants') || 0);
    const minimum = MIN_STANDARD_CLAIM_PAYOUT_NIM;
    return {
        ok: participants > 0 && (totalNim / participants) >= minimum,
        kind: 'claim',
        minimum,
    };
}

function validMinimumPayout() {
    return payoutValidation().ok;
}

function validDates() {
    return startsAtValidation().ok;
}

function isPrizedrawForm() {
    return Boolean(state.spot?.is_prizedraw);
}

function validTotalParticipants() {
    return isPrizedrawForm() || sliderValue('totalParticipants') > 0;
}

function validPrizeCount() {
    if (!isPrizedrawForm()) return true;
    const totalParticipants = sliderValue('totalParticipants');
    const prizeCount = sliderValue('prizeCount');
    return totalParticipants <= 0 || prizeCount <= totalParticipants;
}

function validPasswordSettings() {
    if (isPrizedrawForm()) return !els.usePassword?.checked;
    return !els.usePassword.checked || sliderValue('totalParticipants') > 0;
}

function validLocation() {
    const lat = state.spot?.lat;
    const long = state.spot?.long;
    return Number.isFinite(Number(lat)) && Number.isFinite(Number(long));
}

function validateForm({ showMessage = true } = {}) {
    const titleOk = validTitle();
    const totalOk = validTotalNim();
    updateStartsAtMin();
    const startsCheck = startsAtValidation();
    const datesOk = startsCheck.ok;
    const locationOk = validLocation();
    const participantsOk = validTotalParticipants();
    const prizeOk = validPrizeCount();
    const passwordOk = validPasswordSettings();
    const payoutCheck = payoutValidation();
    const payoutOk = payoutCheck.ok;
    const ok = titleOk && totalOk && datesOk && locationOk && participantsOk && prizeOk && passwordOk && payoutOk;

    els.title.classList.toggle('is-invalid', !titleOk);
    if (!state.totalValueLocked) {
        els.totalValue.classList.toggle('is-invalid', !totalOk);
    } else {
        els.totalValue.classList.remove('is-invalid');
    }

    if (showMessage) {
        if (!titleOk) {
            setError(TEXT.form.titleInvalid({ min: TITLE_MIN, max: TITLE_MAX }));
        } else if (!locationOk) {
            setError(TEXT.form.locationRequired);
        } else if (!totalOk) {
            setError(TEXT.form.totalNimMinimum(MIN_TOTAL_NIM));
        } else if (!datesOk) {
            setError(startsCheck.message || TEXT.form.startsInvalid);
        } else if (!participantsOk) {
            setError(TEXT.form.standardParticipantsRequired);
        } else if (!prizeOk) {
            setError(TEXT.form.prizeCountInvalid);
        } else if (!passwordOk) {
            setError(isPrizedrawForm() ? TEXT.form.prizedrawPasswordInvalid : TEXT.form.passwordRequiresFiniteParticipants);
        } else if (!payoutOk) {
            setError(TEXT.form.payoutTooLow(payoutCheck));
        } else {
            setError(null);
        }
    }

    return ok;
}

function buildDraftFields() {
    const lat = state.spot?.lat;
    const long = state.spot?.long;

    return {
        title: els.title.value.trim(),
        description: els.description.value.trim() || null,
        lat: Number.isFinite(Number(lat)) ? Number(lat) : null,
        long: Number.isFinite(Number(long)) ? Number(long) : null,
        city: state.city || null,
        country: state.country || null,
        radius: sliderValue('radius'),
        claim_duration: sliderValue('duration'),
        max_claims_per_user: sliderValue('perUser'),
        max_total_claims: sliderValue('totalParticipants'),
        total_value: state.totalValueLocked
            ? Number(state.spot?.total_value || MIN_TOTAL_NIM * LUNA_PER_NIM)
            : nimInputToLuna(els.totalValue.value),
        starts_at: dateTimeLocalToUnix(els.startsAt.value),
        ends_at: sliderValue('endsAfter'),
        use_password: isPrizedrawForm() ? false : Boolean(els.usePassword.checked),
        prize_count: isPrizedrawForm() ? sliderValue('prizeCount') : undefined,
    };
}

function snapshotForComparison() {
    return JSON.stringify(buildDraftFields());
}

function setInitialSnapshot() {
    state.initialSnapshot = snapshotForComparison();
}

function hasChanges() {
    return state.initialSnapshot !== null && snapshotForComparison() !== state.initialSnapshot;
}

function updateSaveButtonState() {
    const ok = validateForm({ showMessage: false });
    const changed = hasChanges();
    const savingOrLoading = state.saveInProgress || state.hydrating;
    const enabled = ok && changed && !savingOrLoading;
    const lockedBecauseSaved = ok && !changed && !savingOrLoading;
    const payoutCheck = payoutValidation();
    const lockedBecausePayoutTooLow = !payoutCheck.ok
        && validTitle()
        && validTotalNim()
        && validDates()
        && validLocation()
        && validTotalParticipants()
        && validPrizeCount()
        && validPasswordSettings()
        && !savingOrLoading
        && !state.hydrating;

    // Keep the button focusable when the draft is already saved so the same
    // tooltip pattern used by homepage locked buttons can work here too.
    els.save.disabled = savingOrLoading;
    els.save.setAttribute('aria-disabled', enabled ? 'false' : 'true');
    els.save.classList.toggle('is-disabled-by-validation', !enabled);

    if (lockedBecausePayoutTooLow) {
        els.save.dataset.tooltip = TEXT.form.payoutTooLowTooltip(payoutCheck);
    } else if (lockedBecauseSaved) {
        els.save.dataset.tooltip = TEXT.form.changesAlreadySaved || 'changes are already saved';
    } else {
        delete els.save.dataset.tooltip;
    }

    if (els.delete) {
        const deleteDisabled = state.saveInProgress || state.deleteInProgress || state.hydrating;
        els.delete.disabled = deleteDisabled;
        els.delete.classList.toggle('is-disabled-by-validation', deleteDisabled);
    }
}

function populateForm(spot) {
    state.spot = { ...spot };
    state.city = spot.city || null;
    state.country = spot.country || null;
    state.totalValueLocked = Boolean(spot.total_value_locked);
    state.hydrating = true;

    els.loading.hidden = true;
    els.card.hidden = false;

    els.title.value = spot.title || '';
    setLocationLines({
        city: spot.city || '',
        country: spot.country || '',
        lat: hasSavedCoordinates(spot) ? spot.lat : undefined,
        long: hasSavedCoordinates(spot) ? spot.long : undefined,
    });
    els.description.value = spot.description || '';

    configureSpotTypeControls(spot);
    setSliderValue('radius', Number(spot.radius || MIN_RADIUS), { updateState: false });
    setSliderValue('duration', Number(spot.claim_duration ?? MIN_DURATION), { updateState: false });
    setSliderValue('perUser', Number(spot.max_claims_per_user ?? 1), { updateState: false });
    setSliderValue('totalParticipants', Number(spot.max_total_claims ?? sliderControls.totalParticipants.options[0]), { updateState: false });
    setSliderValue('prizeCount', Number(spot.prize_count || 1), { updateState: false });
    els.totalValue.value = lunaToNimInput(spot.total_value) || String(MIN_TOTAL_NIM);
    if (els.totalValueLocked) {
        els.totalValueLocked.textContent = formatNimAmount(Number(spot.total_value || 0) / LUNA_PER_NIM);
        els.totalValueLocked.hidden = !state.totalValueLocked;
    }
    els.totalValue.hidden = state.totalValueLocked;
    els.totalValue.disabled = state.totalValueLocked;
    updateTotalValueRate();
    updateStartsAtMin();
    els.startsAt.value = unixToDateTimeLocal(spot.starts_at);
    setSliderValue('endsAfter', Number(spot.ends_after ?? spot.ends_at ?? DEFAULT_ENDS_AFTER), { updateState: false });
    els.usePassword.checked = Boolean(spot.use_password) && !Boolean(spot.is_prizedraw);

    setupMap(spot);
    updateSaveButtonState();

    requestAnimationFrame(() => {
        if (state.map) state.map.invalidateSize();
        for (const name of Object.keys(sliderControls)) {
            setSliderValue(name, sliderValue(name), { updateState: false });
        }
        updateRadiusCircle();
        updateSaveButtonState();
    });
}

function buildUpdatePayload() {
    return {
        ...authPayload(),
        ...buildDraftFields(),
    };
}

async function saveDraft() {
    if (!validateForm() || !hasChanges() || state.saveInProgress || state.hydrating) {
        updateSaveButtonState();
        if (els.save?.dataset?.tooltip) showSaveLockedTooltip();
        return;
    }

    state.saveInProgress = true;
    els.save.disabled = true;
    els.save.setAttribute('aria-disabled', 'true');
    els.save.classList.add('is-disabled-by-validation');
    els.save.textContent = TEXT.form.saving;
    setError(null);

    try {
        const data = await fetchJson(`/api/create-spot/${SPOT_ID}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildUpdatePayload()),
        });

        if (data.spot) {
            state.spot = { ...state.spot, ...data.spot };
            configureSpotTypeControls(data.spot);
        }

        setInitialSnapshot();
    } catch (err) {
        const data = err?.data || {};
        if (data.redirect_url || data.code === 'not_owner') {
            redirectHome();
            return;
        }
        setError(err?.message || TEXT.notices.saveFailed.body);
        showNotice({
            ...TEXT.notices.saveFailed,
            body: err?.message || TEXT.notices.saveFailed.body,
        });
    } finally {
        state.saveInProgress = false;
        els.save.textContent = TEXT.form.save;
        updateSaveButtonState();
    }
}


function openDeleteConfirmation() {
    if (!els.deleteBackdrop || state.deleteInProgress) return;
    const title = els.title?.value?.trim() || state.spot?.title || 'this draft';
    if (els.deleteBody) els.deleteBody.textContent = TEXT.notices.deleteConfirm.body(title);
    if (els.deleteConfirm) els.deleteConfirm.textContent = TEXT.notices.deleteConfirm.confirm;
    if (els.deleteCancel) els.deleteCancel.textContent = TEXT.notices.deleteConfirm.cancel;
    els.deleteBackdrop.hidden = false;
}

function closeDeleteConfirmation() {
    if (!els.deleteBackdrop || state.deleteInProgress) return;
    els.deleteBackdrop.hidden = true;
}

async function deleteDraftSpot() {
    if (state.deleteInProgress) return;
    state.deleteInProgress = true;
    if (els.deleteConfirm) els.deleteConfirm.disabled = true;
    if (els.deleteCancel) els.deleteCancel.disabled = true;
    if (els.delete) els.delete.disabled = true;
    setError(null);

    try {
        const data = await fetchJson(`/api/create-spot/${SPOT_ID}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });
        window.location.href = data.redirect_url || '/my-spots';
    } catch (err) {
        const data = err?.data || {};
        if (data.redirect_url || data.code === 'not_owner') {
            redirectHome();
            return;
        }
        state.deleteInProgress = false;
        if (els.deleteConfirm) els.deleteConfirm.disabled = false;
        if (els.deleteCancel) els.deleteCancel.disabled = false;
        if (els.delete) els.delete.disabled = false;
        closeDeleteConfirmation();
        showNotice({
            ...TEXT.notices.deleteFailed,
            body: err?.message || TEXT.notices.deleteFailed.body,
        });
        updateSaveButtonState();
    }
}

async function initCreateSpot() {
    if (!SPOT_ID) {
        redirectHome();
        return;
    }

    state.language = getLanguage();
    for (const name of Object.keys(sliderControls)) {
        bindSliderControl(name);
    }

    const [, location] = await Promise.all([
        requestWalletDeviceId(),
        requestLocation().then((value) => {
            state.userLocation = value;
            return value;
        }),
    ]);
    state.userLocation = location;

    const data = await loadSpot();
    if (data.test_user) state.walletAvailable = true;
    populateForm(data.spot);
}

updateStartsAtMin();

els.noticeOk.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

for (const button of els.helpButtons) {
    button.addEventListener('mouseenter', () => showHelpTooltip(button));
    button.addEventListener('focus', () => showHelpTooltip(button));
    button.addEventListener('mouseleave', hideHelpTooltip);
    button.addEventListener('blur', hideHelpTooltip);
}

if (els.save) {
    els.save.addEventListener('mouseenter', showSaveLockedTooltip);
    els.save.addEventListener('focus', showSaveLockedTooltip);
    els.save.addEventListener('mouseleave', hideHelpTooltip);
    els.save.addEventListener('blur', hideHelpTooltip);
}

for (const input of [els.title, els.description, els.totalValue, els.startsAt]) {
    input.addEventListener('input', () => {
        if (input === els.totalValue) updateTotalValueRate();
        updateSaveButtonState();
    });
    input.addEventListener('change', () => {
        if (input === els.totalValue) updateTotalValueRate();
        updateSaveButtonState();
    });
}

els.startsAt?.addEventListener('focus', updateStartsAtMin);
els.startsAt?.addEventListener('click', updateStartsAtMin);

if (els.usePassword) els.usePassword.addEventListener('change', updateSaveButtonState);
if (els.delete) els.delete.addEventListener('click', openDeleteConfirmation);
if (els.deleteCancel) els.deleteCancel.addEventListener('click', closeDeleteConfirmation);
if (els.deleteConfirm) els.deleteConfirm.addEventListener('click', deleteDraftSpot);

els.title.addEventListener('input', () => validateForm({ showMessage: false }));
els.totalValue.addEventListener('blur', normaliseTotalValueInput);

els.form.addEventListener('submit', (event) => {
    event.preventDefault();
    saveDraft();
});

window.addEventListener('resize', () => {
    hideHelpTooltip();
    for (const name of Object.keys(sliderControls)) {
        positionSliderBubble(sliderControls[name], sliderControls[name].range.value);
    }
});

initCreateSpot().catch((err) => {
    console.error(err);
    const data = err?.data || {};
    if (data.redirect_url || data.code === 'not_owner') {
        redirectHome();
        return;
    }
    if (data.code === 'wallet_unavailable') {
        showNotice(TEXT.notices.walletUnavailable);
        return;
    }
    showNotice({
        ...TEXT.notices.loadFailed,
        body: err?.message || TEXT.notices.loadFailed.body,
    });
});
