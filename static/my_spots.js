import { init, requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import {
    appendBulletLine,
    appendDetailDescription,
    buildSpotLinkControl,
    createOwnerClaimCodesControl,
    copyText,
    createScheduleTextSpan,
    createSpotListItem,
    durationText,
    nimFromLunaText,
    spotPlaceText,
    publicSpotUrl,
    spotStatusText,
    spotScheduleSummary,
    unixToText,
} from './spot_ui.js?v=small-polish-v1-20260705';
import { createReusableSpotMap } from './spot_map.js';
import { createCaptchaController } from './simple_captcha.js?v=claim-polish-v2-20260704';
import { COMMON_TEXT, makeMySpotsText, SPOT_TEXT } from './interface_text.js?v=claim-polish-v2-20260704';

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    banned: false,
    expandedSpotIds: new Set(),
    sectionExpanded: {
        active: true,
        upcoming: false,
        draft: true,
        previous: false,
    },
    spotMap: null,
    creatingSpot: false,
    depositSpot: null,
    depositIntent: null,
    depositInProgress: false,
    publishSpot: null,
    publishInProgress: false,
    cancelSpot: null,
    cancelInProgress: false,
    draftSpotCount: 0,
    draftSpotLimit: Number.parseInt(document.body.dataset.draftLimit || '3', 10),
    createSpotCaptcha: null,
};

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const CREATE_SPOT_URL = document.body.dataset.createSpotUrl || '/create';
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';
const SPOT_TITLE_MIN_LENGTH = Number.parseInt(document.body.dataset.spotTitleMin || '3', 10);
const SPOT_TITLE_MAX_LENGTH = Number.parseInt(document.body.dataset.spotTitleMax || '18', 10);
const DRAFT_SPOT_LIMIT = Number.parseInt(document.body.dataset.draftLimit || '3', 10);

const TEXT = makeMySpotsText({
    appName: APP_NAME,
    nimiqPayUrl: NIMIQ_PAY_URL,
});

const els = {
    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),

    createSpotBackdrop: document.getElementById('create-spot-backdrop'),
    createSpotOpen: document.getElementById('create-spot-open'),
    createSpotTitle: document.getElementById('create-spot-title'),
    createSpotForm: document.getElementById('create-spot-form'),
    createSpotTitleInput: document.getElementById('create-spot-title-input'),
    createSpotTypeTooltip: document.getElementById('create-spot-type-tooltip'),
    createSpotTypeStandard: document.getElementById('create-spot-type-standard'),
    createSpotTypeStandardOption: document.getElementById('create-spot-type-standard-option'),
    createSpotTypeStandardLabel: document.getElementById('create-spot-type-standard-label'),
    createSpotTypePrizeDraw: document.getElementById('create-spot-type-prizedraw'),
    createSpotTypePrizeDrawOption: document.getElementById('create-spot-type-prizedraw-option'),
    createSpotTypePrizeDrawLabel: document.getElementById('create-spot-type-prizedraw-label'),
    createSpotCaptchaLabel: document.getElementById('create-spot-captcha-label'),
    createSpotCaptchaQuestion: document.getElementById('create-spot-captcha-question'),
    createSpotCaptchaInput: document.getElementById('create-spot-captcha-input'),
    createSpotSubmit: document.getElementById('create-spot-submit'),
    createSpotCancel: document.getElementById('create-spot-cancel'),
    createSpotError: document.getElementById('create-spot-error'),

    depositBackdrop: document.getElementById('deposit-spot-backdrop'),
    depositTitle: document.getElementById('deposit-spot-title'),
    depositBody: document.getElementById('deposit-spot-body'),
    depositConfirm: document.getElementById('deposit-spot-confirm'),
    depositCancel: document.getElementById('deposit-spot-cancel'),

    publishBackdrop: document.getElementById('publish-spot-backdrop'),
    publishTitle: document.getElementById('publish-spot-title'),
    publishBody: document.getElementById('publish-spot-body'),
    publishConfirm: document.getElementById('publish-spot-confirm'),
    publishCancel: document.getElementById('publish-spot-cancel'),

    cancelBackdrop: document.getElementById('cancel-spot-backdrop'),
    cancelTitle: document.getElementById('cancel-spot-title'),
    cancelBody: document.getElementById('cancel-spot-body'),
    cancelConfirm: document.getElementById('cancel-spot-confirm'),
    cancelCancel: document.getElementById('cancel-spot-cancel'),

    map: document.getElementById('spot-map'),
    sections: document.getElementById('my-spots-sections'),
    empty: document.getElementById('empty-my-spots'),
};

function getLanguage() {
    const payLanguage = window.nimiqPay?.language;
    if (typeof payLanguage === 'string' && payLanguage.length > 0) return payLanguage;

    const browserLanguage = navigator.language || navigator.userLanguage;
    if (typeof browserLanguage === 'string' && browserLanguage.length > 0) {
        return browserLanguage.split('-')[0];
    }

    return 'en';
}

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

function ensureMySpotsTooltip() {
    let tooltip = document.getElementById('my-spots-lock-tooltip');
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'my-spots-lock-tooltip';
        tooltip.className = 'lock-tooltip my-spots-lock-tooltip';
        tooltip.setAttribute('role', 'tooltip');
        tooltip.hidden = true;
        document.body.append(tooltip);
    }
    return tooltip;
}

function hideMySpotsTooltip() {
    const tooltip = document.getElementById('my-spots-lock-tooltip');
    if (!tooltip) return;
    tooltip.hidden = true;
    tooltip.textContent = '';
    tooltip.removeAttribute('data-placement');
}

function showMySpotsTooltip(target, text) {
    if (!target || !text) return;
    const tooltip = ensureMySpotsTooltip();
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

function draftLimitReached() {
    const limit = Number.isFinite(state.draftSpotLimit) ? state.draftSpotLimit : DRAFT_SPOT_LIMIT;
    return limit >= 0 && Number(state.draftSpotCount || 0) >= limit;
}

function syncCreateSpotAvailability() {
    if (!els.createSpotOpen) return;
    const locked = draftLimitReached();
    els.createSpotOpen.classList.toggle('is-locked', locked);
    els.createSpotOpen.setAttribute('aria-disabled', locked ? 'true' : 'false');
    els.createSpotOpen.disabled = false;
}

function showCreateSpotLimitTooltip() {
    if (!draftLimitReached() || !els.createSpotOpen) return;
    const limit = Number.isFinite(state.draftSpotLimit) ? state.draftSpotLimit : DRAFT_SPOT_LIMIT;
    showMySpotsTooltip(els.createSpotOpen, TEXT.createSpot.draftLimitTooltip({ limit }));
}

function hideCreateSpotTypeTooltip() {
    if (!els.createSpotTypeTooltip) return;
    els.createSpotTypeTooltip.hidden = true;
    els.createSpotTypeTooltip.textContent = '';
    els.createSpotTypeTooltip.removeAttribute('data-placement');
}

function positionCreateSpotTypeTooltip(target) {
    if (!els.createSpotTypeTooltip || els.createSpotTypeTooltip.hidden || !target) return;

    const gap = 12;
    const edgePadding = 12;
    const targetRect = target.getBoundingClientRect();
    const tooltipRect = els.createSpotTypeTooltip.getBoundingClientRect();

    let placement = 'top';
    let top = targetRect.top - tooltipRect.height - gap;

    if (top < edgePadding) {
        placement = 'bottom';
        top = targetRect.bottom + gap;
    }

    let left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);
    left = Math.max(edgePadding, Math.min(left, window.innerWidth - tooltipRect.width - edgePadding));

    els.createSpotTypeTooltip.style.left = `${Math.round(left)}px`;
    els.createSpotTypeTooltip.style.top = `${Math.round(top)}px`;
    els.createSpotTypeTooltip.dataset.placement = placement;
}

function showCreateSpotTypeTooltipFor(target) {
    if (!els.createSpotTypeTooltip || !target?.dataset?.tooltip) return;

    els.createSpotTypeTooltip.textContent = target.dataset.tooltip;
    els.createSpotTypeTooltip.hidden = false;
    els.createSpotTypeTooltip.dataset.placement = 'top';

    window.requestAnimationFrame(() => positionCreateSpotTypeTooltip(target));
}

function bindCreateSpotTypeTooltip(target) {
    if (!target) return;

    target.addEventListener('mouseenter', () => showCreateSpotTypeTooltipFor(target));
    target.addEventListener('focusin', () => showCreateSpotTypeTooltipFor(target));
    target.addEventListener('mouseleave', hideCreateSpotTypeTooltip);
    target.addEventListener('focusout', hideCreateSpotTypeTooltip);
    target.addEventListener('touchstart', () => {
        showCreateSpotTypeTooltipFor(target);
        window.setTimeout(hideCreateSpotTypeTooltip, 1800);
    }, { passive: true });
}


function setupCreateSpotCaptcha() {
    if (!els.createSpotCaptchaQuestion || !els.createSpotCaptchaInput) return;

    state.createSpotCaptcha = createCaptchaController({
        questionEl: els.createSpotCaptchaQuestion,
        inputEl: els.createSpotCaptchaInput,
        questionText: TEXT.createSpot.captchaQuestion,
        onChange: () => syncCreateSpotFormState({ showTitleError: false }),
    });
}

function applyCreateSpotText() {
    if (els.createSpotTitle) els.createSpotTitle.textContent = TEXT.createSpot.title;
    if (els.createSpotOpen) els.createSpotOpen.textContent = TEXT.createSpot.openButton;
    if (els.createSpotTitleInput) els.createSpotTitleInput.placeholder = TEXT.createSpot.titlePlaceholder;
    if (els.createSpotTypeStandardLabel) els.createSpotTypeStandardLabel.textContent = TEXT.createSpot.standard;
    if (els.createSpotTypePrizeDrawLabel) els.createSpotTypePrizeDrawLabel.textContent = TEXT.createSpot.prizeDraw;
    if (els.createSpotTypeStandardOption) els.createSpotTypeStandardOption.dataset.tooltip = TEXT.createSpot.standardTooltip;
    if (els.createSpotTypePrizeDrawOption) els.createSpotTypePrizeDrawOption.dataset.tooltip = TEXT.createSpot.prizeDrawTooltip;
    if (els.createSpotCaptchaLabel) els.createSpotCaptchaLabel.textContent = TEXT.createSpot.captchaLabel;
    if (els.createSpotCaptchaInput) els.createSpotCaptchaInput.placeholder = TEXT.createSpot.captchaPlaceholder;
    if (els.createSpotSubmit) els.createSpotSubmit.textContent = TEXT.createSpot.submit;
    if (els.createSpotCancel) els.createSpotCancel.textContent = TEXT.createSpot.cancel;
}

function setCreateSpotError(message) {
    if (!els.createSpotError) return;

    if (message) {
        els.createSpotError.textContent = message;
        els.createSpotError.hidden = false;
    } else {
        els.createSpotError.textContent = '';
        els.createSpotError.hidden = true;
    }
}

function validateCreateSpotTitle(value) {
    const title = String(value || '').trim();
    if (title.length < SPOT_TITLE_MIN_LENGTH || title.length > SPOT_TITLE_MAX_LENGTH) {
        return {
            ok: false,
            title,
            message: TEXT.createSpot.invalidTitle({
                min: SPOT_TITLE_MIN_LENGTH,
                max: SPOT_TITLE_MAX_LENGTH,
            }),
        };
    }

    return { ok: true, title, message: '' };
}

function createSpotCaptchaPassed() {
    return Boolean(state.createSpotCaptcha?.passed());
}

function setCreateSpotSubmitValid(valid) {
    if (!els.createSpotSubmit || state.creatingSpot) return;
    els.createSpotSubmit.disabled = !valid;
    els.createSpotSubmit.classList.toggle('is-disabled-by-validation', !valid);
}

function syncCreateSpotFormState({ showTitleError = true } = {}) {
    const check = validateCreateSpotTitle(els.createSpotTitleInput?.value);
    const captchaOk = createSpotCaptchaPassed();
    const formOk = check.ok && captchaOk;

    els.createSpotTitleInput?.classList.toggle('is-invalid', !check.ok);
    setCreateSpotSubmitValid(formOk);
    setCreateSpotError(!check.ok && showTitleError ? check.message : null);

    return { ...check, captchaOk, formOk };
}

function syncCreateSpotTitleState() {
    return syncCreateSpotFormState();
}

function resetCreateSpotForm() {
    state.creatingSpot = false;
    if (els.createSpotTitleInput) {
        els.createSpotTitleInput.value = '';
        els.createSpotTitleInput.classList.remove('is-invalid');
    }
    if (els.createSpotTypeStandard) els.createSpotTypeStandard.checked = true;
    if (state.createSpotCaptcha) {
        state.createSpotCaptcha.reset();
    }
    if (els.createSpotSubmit) {
        els.createSpotSubmit.disabled = true;
        els.createSpotSubmit.classList.add('is-disabled-by-validation');
        els.createSpotSubmit.textContent = TEXT.createSpot.submit;
    }
    setCreateSpotError(null);
    syncCreateSpotFormState({ showTitleError: false });
}

function showCreateSpotModal() {
    if (draftLimitReached()) {
        showCreateSpotLimitTooltip();
        return;
    }

    resetCreateSpotForm();
    els.createSpotBackdrop.hidden = false;
    window.setTimeout(() => els.createSpotTitleInput?.focus(), 0);
}

function hideCreateSpotModal() {
    hideCreateSpotTypeTooltip();
    els.createSpotBackdrop.hidden = true;
    resetCreateSpotForm();
}

async function createDraftSpot({ title, isPrizeDraw, captchaPayload }) {
    return fetchJson('/api/create-spot/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            device_id_hash: state.deviceIdHash,
            wallet_available: state.walletAvailable,
            language: state.language,
            title,
            is_prizedraw: isPrizeDraw,
            ...captchaPayload,
        }),
    });
}

async function submitCreateSpotForm(event) {
    event.preventDefault();
    if (state.creatingSpot) return;

    const check = syncCreateSpotFormState();
    if (!check.ok) {
        els.createSpotTitleInput?.focus();
        return;
    }
    if (!check.captchaOk) {
        setCreateSpotError(TEXT.createSpot.captchaIncomplete);
        els.createSpotCaptchaInput?.focus();
        return;
    }

    if (!state.walletAvailable && !state.deviceIdHash) {
        hideCreateSpotModal();
        showNotice(TEXT.createSpot.walletUnavailable);
        return;
    }

    state.creatingSpot = true;
    els.createSpotSubmit.disabled = true;
    els.createSpotSubmit.classList.remove('is-disabled-by-validation');
    els.createSpotSubmit.textContent = TEXT.createSpot.submitting;

    try {
        const data = await createDraftSpot({
            title: check.title,
            isPrizeDraw: Boolean(els.createSpotTypePrizeDraw?.checked),
            captchaPayload: state.createSpotCaptcha?.payload() || {},
        });

        const editUrl = data.edit_url || (data.spot?.id ? `${CREATE_SPOT_URL}/${data.spot.id}` : CREATE_SPOT_URL);
        window.location.href = editUrl;
    } catch (err) {
        console.error(err);
        state.creatingSpot = false;
        els.createSpotSubmit.textContent = TEXT.createSpot.submit;
        syncCreateSpotFormState();

        const data = err?.data || {};
        if (data.code === 'draft_limit_reached') {
            state.draftSpotCount = Number(data.draft_count ?? state.draftSpotCount);
            state.draftSpotLimit = Number(data.draft_limit ?? state.draftSpotLimit);
            syncCreateSpotAvailability();
            setCreateSpotError(data.message || TEXT.createSpot.draftLimitReached({ limit: state.draftSpotLimit }));
            return;
        }
        if (data.code === 'wallet_unavailable' || !state.walletAvailable) {
            hideCreateSpotModal();
            showNotice({
                ...TEXT.createSpot.walletUnavailable,
                body: data.message || TEXT.createSpot.walletUnavailable.body,
            });
            return;
        }

        setCreateSpotError(data.message || err?.message || TEXT.createSpot.createFailed.body);
    }
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

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
        const err = new Error(data.message || 'Request failed.');
        err.data = data;
        err.status = response.status;
        throw err;
    }
    return data;
}

function authPayload() {
    return {
        device_id_hash: state.deviceIdHash,
        wallet_available: state.walletAvailable,
        language: state.language,
    };
}

async function loadMySpots() {
    return fetchJson('/api/my-spots', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authPayload()),
    });
}

function groupSpots(spots) {
    return {
        active: spots.filter((spot) => spot.bucket === 'active'),
        upcoming: spots.filter((spot) => spot.bucket === 'upcoming' && spot.status_label !== 'draft'),
        draft: spots.filter((spot) => spot.bucket === 'draft' || spot.status_label === 'draft'),
        previous: spots.filter((spot) => spot.bucket === 'previous'),
    };
}

function draftDepositText(spot) {
    const deposit = spot.deposit || {};
    const status = deposit.status || 'missing';

    if (status === 'ready') return TEXT.draftDeposit.ready;
    if (status === 'partial') return TEXT.draftDeposit.partial(nimFromLunaText(deposit.amount));
    return TEXT.draftDeposit.missing;
}

function draftDepositClass(spot) {
    const status = spot.deposit?.status || 'missing';
    if (status === 'ready') return 'is-ready';
    if (status === 'partial') return 'is-partial';
    return 'is-missing';
}

function scheduleText(spot) {
    if (spot.status_label === 'draft') {
        const starts = unixToText(spot.starts_at) || 'now';
        const ends = unixToText(spot.ends_at) || 'no end time';
        return TEXT.spotDetail.scheduled({ starts, ends });
    }
    return spotScheduleSummary(spot);
}

function scheduleNode(spot) {
    if (spot.status_label === 'draft') return document.createTextNode(scheduleText(spot));
    return createScheduleTextSpan(spot);
}

function claimProgressText(spot) {
    const used = Number(spot.success_claim_count || 0);
    const max = Number(spot.max_total_claims || 1);
    const word = spot.is_prizedraw ? 'entries' : 'claims';
    return TEXT.spotDetail.progress({ used, max, word });
}

function prizeDrawValueLine(spot) {
    if (!spot.is_prizedraw) return null;

    const prizeCount = Math.max(1, Number(spot.prize_count || 1));
    const perPrize = nimFromLunaText(Number(spot.total_value || 0) / prizeCount);
    const prizeWord = prizeCount === 1 ? 'Prize' : 'Prizes';
    const eachText = prizeCount === 1 ? `(${perPrize})` : `(${perPrize} each)`;

    const fragment = document.createDocumentFragment();

    const pill = document.createElement('span');
    pill.className = 'spot-detail-prizedraw-pill';
    pill.textContent = SPOT_TEXT.type.prizeDraw;

    fragment.append(
        pill,
        document.createTextNode(` ${prizeCount} ${prizeWord} ${eachText}`)
    );

    return fragment;
}

function buildMySpotMeta(spot) {
    const fragment = document.createDocumentFragment();
    fragment.append(document.createTextNode(`${spotPlaceText(spot)} - `));

    if (spot.status_label === 'draft') {
        const notice = document.createElement('span');
        notice.className = `spot-list-meta-notice ${draftDepositClass(spot)}`;
        notice.textContent = draftDepositText(spot);
        fragment.append(notice);
        return fragment;
    }

    fragment.append(scheduleNode(spot));
    return fragment;
}


function ownerActionButton({ text, className, onClick, href = null }) {
    const button = href ? document.createElement('a') : document.createElement('button');
    button.className = `nq-button ${className} spot-owner-action-button`;
    button.textContent = text;

    if (href) {
        button.href = href;
    } else {
        button.type = 'button';
        button.addEventListener('click', onClick);
    }

    return button;
}

function lockedOwnerActionButton({ text, className = 'green', tooltip }) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `nq-button ${className} spot-owner-action-button is-disabled-by-validation`;
    button.textContent = text;
    button.setAttribute('aria-disabled', 'true');
    if (tooltip) button.dataset.tooltip = tooltip;

    const showTooltip = () => {
        if (button.dataset.tooltip) showMySpotsTooltip(button, button.dataset.tooltip);
    };

    button.addEventListener('click', (event) => {
        event.preventDefault();
        showTooltip();
    });
    button.addEventListener('mouseenter', showTooltip);
    button.addEventListener('focusin', showTooltip);
    button.addEventListener('mouseleave', hideMySpotsTooltip);
    button.addEventListener('focusout', hideMySpotsTooltip);
    button.addEventListener('touchstart', () => {
        showTooltip();
        window.setTimeout(hideMySpotsTooltip, 1800);
    }, { passive: true });

    return button;
}

function buildOwnerActions(spot) {
    const actions = document.createElement('div');
    actions.className = 'spot-owner-actions';

    if (spot.can_edit) {
        actions.append(ownerActionButton({
            text: TEXT.ownerActions.edit,
            className: 'light-blue',
            href: spot.edit_href || `${CREATE_SPOT_URL}/${spot.id}`,
        }));
    }

    if (spot.can_deposit) {
        actions.append(ownerActionButton({
            text: TEXT.ownerActions.deposit,
            className: 'gold',
            onClick: () => openDepositModal(spot),
        }));
    } else if (spot.can_publish) {
        actions.append(ownerActionButton({
            text: TEXT.ownerActions.publish,
            className: 'green',
            onClick: () => openPublishModal(spot),
        }));
    } else if (spot.publish_block_reason) {
        actions.append(lockedOwnerActionButton({
            text: TEXT.ownerActions.publish,
            className: 'green',
            tooltip: spot.publish_block_message || TEXT.ownerActions.publishUnavailableTooltip,
        }));
    }

    if (spot.can_cancel) {
        actions.append(ownerActionButton({
            text: TEXT.ownerActions.cancel,
            className: 'red',
            onClick: () => openCancelModal(spot),
        }));
    }

    return actions.children.length > 0 ? actions : null;
}

function withFromMySpotsParam(url) {
    if (!url) return url;
    const nextUrl = new URL(url, window.location.origin);
    nextUrl.searchParams.set('from', 'my-spots');
    return `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`;
}

function spotHrefFromMySpots(spot) {
    if (!spot?.href || spot.status_label === 'draft') return spot?.href || '';
    return withFromMySpotsParam(spot.href);
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

function buildMySpotsSpotLinkControl(spot) {
    if (!spot?.href || spot.status_label === 'draft') return buildSpotLinkControl(spot);

    const wrap = document.createElement('span');
    wrap.className = 'spot-detail-link-row';

    const link = document.createElement('a');
    link.href = spotHrefFromMySpots(spot);
    link.className = 'spot-link-anchor';
    link.textContent = spot.link || spot.href;

    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'spot-copy-button';
    copyButton.setAttribute('aria-label', SPOT_TEXT.copySpotLink);

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


async function loadOwnerClaimCodesForSpot(spot, control) {
    if (!spot || !control || Number(spot.claim_code_count || 0) <= 0) {
        control?.hide?.();
        return;
    }

    control.setLoading();
    try {
        const data = await fetchJson(`/api/spot/${spot.id}/claim-codes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });
        control.render(data.claim_codes || []);
    } catch (err) {
        console.error(err);
        control.setFailed();
    }
}

function buildMySpotDetail(spot) {
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail';

    appendDetailDescription(detail, spot.description);

    const lines = document.createElement('ul');
    lines.className = 'spot-detail-lines';

    appendBulletLine(
        lines,
        spot.is_prizedraw
            ? prizeDrawValueLine(spot)
            : TEXT.spotDetail.totalValue(nimFromLunaText(spot.total_value))
    );

    if (spot.status_label === 'draft' && spot.can_publish) {
        appendBulletLine(lines, TEXT.spotDetail.readyToPublish);
    }

    appendBulletLine(lines, claimProgressText(spot));
    appendBulletLine(lines, scheduleNode(spot));

    const duration = durationText(spot.claim_duration);
    if (duration) {
        appendBulletLine(lines, TEXT.spotDetail.claimDuration(duration));
    }

    appendBulletLine(lines, TEXT.spotDetail.claimRadius(Number(spot.radius || 0)));

    const maxClaimsPerUser = Number(spot.max_claims_per_user ?? 1);
    if (maxClaimsPerUser !== 1) {
        appendBulletLine(lines, TEXT.spotDetail.claimsPerUser(maxClaimsPerUser));
    }

    const codeCount = Number(spot.claim_code_count || 0);
    if (spot.use_password && spot.status_label === 'draft') {
        appendBulletLine(lines, TEXT.spotDetail.claimCodesOnPublish({ total: Number(spot.max_total_claims || 0) }));
    }

    const reportCount = Number(spot.report_count || 0);
    if (reportCount > 0) {
        appendBulletLine(lines, TEXT.spotDetail.reports({ pending: Number(spot.pending_report_count || 0), total: reportCount }));
    }

    appendBulletLine(lines, buildMySpotsSpotLinkControl(spot));

    if (codeCount > 0) {
        const claimCodesControl = createOwnerClaimCodesControl();
        lines.append(claimCodesControl.line);
        loadOwnerClaimCodesForSpot(spot, claimCodesControl);
    }

    detail.append(lines);

    const ownerActions = buildOwnerActions(spot);
    if (ownerActions) {
        detail.append(ownerActions);
    }

    return detail;
}

function renderEmptyAll() {
    const link = document.createElement('a');
    link.href = '#create-spot';
    link.className = 'welcome-link';
    link.textContent = TEXT.status.emptyLink;
    link.addEventListener('click', (event) => {
        event.preventDefault();
        showCreateSpotModal();
    });

    els.empty.replaceChildren(
        document.createTextNode(TEXT.status.emptyBeforeLink),
        link,
        document.createTextNode(TEXT.status.emptyAfterLink)
    );
    els.empty.hidden = false;
}

function renderSection(bucket, spots) {
    const copy = TEXT.sections[bucket];
    const section = document.createElement('section');
    section.className = `spot-list-card my-spots-section-card is-${bucket}`;
    section.setAttribute('aria-label', copy.title);

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'spot-section-toggle disclosure-toggle';
    toggle.setAttribute('aria-expanded', state.sectionExpanded[bucket] ? 'true' : 'false');

    const title = document.createElement('span');
    title.textContent = `${copy.title} (${spots.length})`;
    toggle.append(title);

    const list = document.createElement('ol');
    list.className = 'spot-list';
    list.hidden = !state.sectionExpanded[bucket];

    const empty = document.createElement('p');
    empty.className = 'empty-spots';
    empty.textContent = copy.empty;
    empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;

    toggle.addEventListener('click', () => {
        state.sectionExpanded[bucket] = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', state.sectionExpanded[bucket] ? 'true' : 'false');
        list.hidden = !state.sectionExpanded[bucket];
        empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;
    });

    for (const spot of spots) {
        list.append(createSpotListItem({
            spot,
            detailBuilder: buildMySpotDetail,
            metaBuilder: buildMySpotMeta,
            expanded: state.expandedSpotIds.has(Number(spot.id)),
            onToggle: (spotId, expanded) => {
                if (expanded) {
                    state.expandedSpotIds.add(spotId);
                } else {
                    state.expandedSpotIds.delete(spotId);
                }
            },
        }));
    }

    section.append(toggle, list, empty);
    return section;
}

function renderSpots(spots) {
    els.sections.replaceChildren();
    els.empty.hidden = true;

    if (spots.length === 0) {
        renderEmptyAll();
        return;
    }

    const groups = groupSpots(spots);
    els.sections.append(
        renderSection('active', groups.active),
        renderSection('upcoming', groups.upcoming),
        renderSection('draft', groups.draft),
        renderSection('previous', groups.previous)
    );
}

function spotPopupContent(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    title.textContent = spot.title || SPOT_TEXT.fallbackTitle;
    return title;
}

function openSpotPage(spot) {
    const href = spotHrefFromMySpots(spot);
    if (!href) return;
    window.location.href = href;
}

const MY_SPOTS_MAP_COLOURS = {
    activeStandard: '#21bca5',
    activePrizedraw: '#ffc435',
    muted: '#8c90a8',
};

function mySpotsMapColourForSpot(spot) {
    if (spot?.status_label !== 'active') return MY_SPOTS_MAP_COLOURS.muted;
    return spot.is_prizedraw ? MY_SPOTS_MAP_COLOURS.activePrizedraw : MY_SPOTS_MAP_COLOURS.activeStandard;
}

function renderMap(spots) {
    try {
        if (!state.spotMap) {
            state.spotMap = createReusableSpotMap({
                mapEl: els.map,
                tileUrl: MAP_TILE_URL,
                tileAttribution: MAP_TILE_ATTRIBUTION,
                spots,
                colourForSpot: mySpotsMapColourForSpot,
                popupBuilder: spotPopupContent,
                onSpotClick: openSpotPage,
            });
            if (!state.spotMap) throw new Error('Leaflet not available.');
            return;
        }

        state.spotMap.setSpots(spots);
    } catch (err) {
        console.error(err);
        showNotice(TEXT.notices.mapSetupFailed);
    }
}


function closeDepositModal() {
    if (!els.depositBackdrop || state.depositInProgress) return;
    state.depositSpot = null;
    state.depositIntent = null;
    els.depositBackdrop.hidden = true;
}

async function openDepositModal(spot) {
    if (!spot?.id || state.depositInProgress) return;

    try {
        const data = await fetchJson(`/api/my-spots/${spot.id}/deposit-intent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });

        state.depositSpot = spot;
        state.depositIntent = data;

        const amountText = nimFromLunaText(data.amount || spot.deposit?.amount_due || 0);
        els.depositTitle.textContent = TEXT.deposit.title;
        els.depositBody.textContent = TEXT.deposit.confirmBody({
            title: data.spot?.title || spot.title || SPOT_TEXT.fallbackTitle,
            amountText,
        });
        els.depositConfirm.textContent = TEXT.deposit.confirm;
        els.depositCancel.textContent = TEXT.deposit.cancel;
        els.depositConfirm.disabled = false;
        els.depositCancel.disabled = false;
        els.depositBackdrop.hidden = false;
    } catch (err) {
        console.error(err);
        showNotice({
            ...TEXT.deposit.intentFailed,
            body: err?.message || TEXT.deposit.intentFailed.body,
        });
    }
}

async function requestDepositPayment(intent) {
    const nimiq = await init();
    let fromAddress = null;

    try {
        const accounts = await nimiq.listAccounts();
        if (Array.isArray(accounts) && accounts.length > 0) {
            fromAddress = accounts[0];
        }
    } catch (err) {
        console.warn('Could not read Nimiq account list before deposit.', err);
    }

    const txHash = await nimiq.sendBasicTransaction({
        recipient: intent.recipient,
        value: Number(intent.amount),
    });

    return { txHash, fromAddress };
}

async function confirmDeposit() {
    if (!state.depositIntent || !state.depositSpot || state.depositInProgress) return;

    state.depositInProgress = true;
    els.depositConfirm.disabled = true;
    els.depositCancel.disabled = true;
    els.depositConfirm.textContent = TEXT.deposit.confirming;

    try {
        const payment = await requestDepositPayment(state.depositIntent);
        await fetchJson(`/api/my-spots/${state.depositSpot.id}/deposit-submitted`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...authPayload(),
                tx_hash: payment.txHash,
                from_address: payment.fromAddress,
                amount: state.depositIntent.amount,
            }),
        });

        state.depositInProgress = false;
        els.depositBackdrop.hidden = true;
        await refreshMySpots();
    } catch (err) {
        console.error(err);
        state.depositInProgress = false;
        els.depositConfirm.disabled = false;
        els.depositCancel.disabled = false;
        els.depositConfirm.textContent = TEXT.deposit.confirm;
        showNotice({
            ...TEXT.deposit.failed,
            body: err?.message || TEXT.deposit.failed.body,
        });
    }
}

function closePublishModal() {
    if (!els.publishBackdrop || state.publishInProgress) return;
    state.publishSpot = null;
    els.publishBackdrop.hidden = true;
}

function openPublishModal(spot) {
    if (!spot?.id || state.publishInProgress) return;

    state.publishSpot = spot;
    els.publishTitle.textContent = TEXT.publish.title;
    els.publishBody.textContent = TEXT.publish.confirmBody({
        title: spot.title || SPOT_TEXT.fallbackTitle,
    });
    els.publishConfirm.textContent = TEXT.publish.confirm;
    els.publishCancel.textContent = TEXT.publish.cancel;
    els.publishConfirm.disabled = false;
    els.publishCancel.disabled = false;
    els.publishBackdrop.hidden = false;
}

function publishedSpotHref(originalSpot, publishedSpot) {
    if (typeof publishedSpot?.href === 'string' && publishedSpot.href.trim()) {
        return publishedSpot.href;
    }

    const ref = publishedSpot?.link || originalSpot?.link || publishedSpot?.id || originalSpot?.id;
    return ref ? `/spot/${ref}` : '/my-spots';
}

function withPublishedCelebrationParam(url) {
    const nextUrl = new URL(url, window.location.origin);
    nextUrl.searchParams.set('from', 'my-spots');
    nextUrl.searchParams.set('published', '1');
    return nextUrl.toString();
}

async function confirmPublishSpot() {
    if (!state.publishSpot?.id || state.publishInProgress) return;

    state.publishInProgress = true;
    els.publishConfirm.disabled = true;
    els.publishCancel.disabled = true;
    els.publishConfirm.textContent = TEXT.publish.publishing;

    try {
        const data = await fetchJson(`/api/my-spots/${state.publishSpot.id}/publish`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });

        const redirectUrl = publishedSpotHref(state.publishSpot, data.spot);
        els.publishBackdrop.hidden = true;
        window.location.href = withPublishedCelebrationParam(redirectUrl);
    } catch (err) {
        console.error(err);
        state.publishInProgress = false;
        els.publishConfirm.disabled = false;
        els.publishCancel.disabled = false;
        els.publishConfirm.textContent = TEXT.publish.confirm;
        showNotice({
            ...TEXT.publish.failed,
            body: err?.message || TEXT.publish.failed.body,
        });
    }
}

function closeCancelModal() {
    if (!els.cancelBackdrop || state.cancelInProgress) return;
    state.cancelSpot = null;
    els.cancelBackdrop.hidden = true;
}

function setCancelBodyContent({ title, refundText, feeText, remainingLost, noRemaining }) {
    if (!els.cancelBody) return;

    if (noRemaining || remainingLost) {
        els.cancelBody.textContent = TEXT.cancelSpot.confirmBody({
            title,
            refundText,
            feeText,
            remainingLost,
            noRemaining,
        });
        return;
    }

    const lines = [
        `Are you sure you want to cancel '${title}'? Remaining funds will be returned, minus the cancellation fee.`,
        `Estimated refund: ${refundText}.`,
        `Cancellation fee: ${feeText}.`,
    ];

    els.cancelBody.replaceChildren(...lines.map((line) => {
        const span = document.createElement('span');
        span.className = 'cancel-spot-body-line';
        span.textContent = line;
        return span;
    }));
}

function openCancelModal(spot) {
    if (!spot?.id || state.cancelInProgress) return;

    state.cancelSpot = spot;
    const cancellation = spot.cancellation || {};
    const remainingAmount = Number(cancellation.remaining_amount || 0);
    const refundAmount = Number(cancellation.refund_amount || 0);
    const refundText = nimFromLunaText(refundAmount);
    const feeText = nimFromLunaText(cancellation.fee_amount || cancellation.configured_fee || 0);
    const remainingLost = Boolean(cancellation.remaining_lost) || (remainingAmount > 0 && refundAmount <= 0);
    const noRemaining = remainingAmount <= 0;

    els.cancelTitle.textContent = TEXT.cancelSpot.title;
    setCancelBodyContent({
        title: spot.title || SPOT_TEXT.fallbackTitle,
        refundText,
        feeText,
        remainingLost,
        noRemaining,
    });
    els.cancelConfirm.textContent = TEXT.cancelSpot.confirm;
    els.cancelCancel.textContent = TEXT.cancelSpot.cancel;
    els.cancelConfirm.disabled = false;
    els.cancelCancel.disabled = false;
    els.cancelBackdrop.hidden = false;
}

async function confirmCancelSpot() {
    if (!state.cancelSpot?.id || state.cancelInProgress) return;

    state.cancelInProgress = true;
    els.cancelConfirm.disabled = true;
    els.cancelCancel.disabled = true;
    els.cancelConfirm.textContent = TEXT.cancelSpot.confirming;

    try {
        await fetchJson(`/api/my-spots/${state.cancelSpot.id}/cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });

        state.cancelInProgress = false;
        els.cancelBackdrop.hidden = true;
        state.cancelSpot = null;
        await refreshMySpots();
    } catch (err) {
        console.error(err);
        state.cancelInProgress = false;
        els.cancelConfirm.disabled = false;
        els.cancelCancel.disabled = false;
        els.cancelConfirm.textContent = TEXT.cancelSpot.confirm;
        showNotice({
            ...TEXT.cancelSpot.failed,
            body: err?.message || TEXT.cancelSpot.failed.body,
        });
    }
}

function renderLoadedMySpots(data) {
    if (data.test_user) {
        state.walletAvailable = true;
    }

    state.user = data.user;
    state.banned = Boolean(data.user?.is_banned);
    state.draftSpotLimit = Number(data.draft_limit ?? state.draftSpotLimit ?? DRAFT_SPOT_LIMIT);

    const spots = Array.isArray(data.spots) ? data.spots : [];
    state.draftSpotCount = Number(data.draft_count ?? spots.filter((spot) => spot.status_label === 'draft').length);
    syncCreateSpotAvailability();
    renderMap(spots);
    renderSpots(spots);
}

async function refreshMySpots() {
    const data = await loadMySpots();
    renderLoadedMySpots(data);
}

async function initMySpots() {
    state.language = getLanguage();

    await requestWalletDeviceId();
    const data = await loadMySpots();

    if (data.test_user) {
        state.walletAvailable = true;
    }

    state.user = data.user;
    state.banned = Boolean(data.user?.is_banned);

    if (data.code === 'test_user_missing') {
        showNotice({
            ...TEXT.notices.testUserMissing,
            body: data.message || TEXT.notices.testUserMissing.body,
        });
        return;
    }

    if (data.code === 'banned') {
        showNotice({
            ...TEXT.notices.banned,
            body: data.message || TEXT.notices.banned.body,
        });
        return;
    }

    renderLoadedMySpots(data);

    if (new URLSearchParams(window.location.search).get('create') === '1') {
        showCreateSpotModal();
    }
}

els.noticeOk.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

if (els.depositCancel) els.depositCancel.addEventListener('click', closeDepositModal);
if (els.depositConfirm) els.depositConfirm.addEventListener('click', confirmDeposit);
if (els.publishCancel) els.publishCancel.addEventListener('click', closePublishModal);
if (els.publishConfirm) els.publishConfirm.addEventListener('click', confirmPublishSpot);
if (els.cancelCancel) els.cancelCancel.addEventListener('click', closeCancelModal);
if (els.cancelConfirm) els.cancelConfirm.addEventListener('click', confirmCancelSpot);

applyCreateSpotText();
setupCreateSpotCaptcha();
syncCreateSpotAvailability();

els.createSpotOpen?.addEventListener('mouseenter', showCreateSpotLimitTooltip);
els.createSpotOpen?.addEventListener('focus', showCreateSpotLimitTooltip);
els.createSpotOpen?.addEventListener('mouseleave', hideMySpotsTooltip);
els.createSpotOpen?.addEventListener('blur', hideMySpotsTooltip);
els.createSpotOpen?.addEventListener('touchstart', () => {
    if (!draftLimitReached()) return;
    showCreateSpotLimitTooltip();
    window.setTimeout(hideMySpotsTooltip, 1800);
}, { passive: true });

els.createSpotOpen?.addEventListener('click', showCreateSpotModal);
els.createSpotCancel?.addEventListener('click', hideCreateSpotModal);
els.createSpotForm?.addEventListener('submit', submitCreateSpotForm);
bindCreateSpotTypeTooltip(els.createSpotTypeStandardOption);
bindCreateSpotTypeTooltip(els.createSpotTypePrizeDrawOption);
window.addEventListener('scroll', () => { hideCreateSpotTypeTooltip(); hideMySpotsTooltip(); }, { passive: true });
window.addEventListener('resize', () => { hideCreateSpotTypeTooltip(); hideMySpotsTooltip(); });
els.createSpotTitleInput?.addEventListener('input', syncCreateSpotTitleState);
els.createSpotBackdrop?.addEventListener('click', (event) => {
    if (event.target === els.createSpotBackdrop && !state.creatingSpot) {
        hideCreateSpotModal();
    }
});

els.publishBackdrop?.addEventListener('click', (event) => {
    if (event.target === els.publishBackdrop && !state.publishInProgress) {
        closePublishModal();
    }
});

window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;

    if (!els.createSpotBackdrop?.hidden && !state.creatingSpot) {
        hideCreateSpotModal();
    }

    if (!els.publishBackdrop?.hidden && !state.publishInProgress) {
        closePublishModal();
    }
});

initMySpots().catch((err) => {
    console.error(err);

    const data = err?.data || {};

    if (data.code === 'test_user_missing') {
        showNotice({
            ...TEXT.notices.testUserMissing,
            body: data.message || TEXT.notices.testUserMissing.body,
        });
        return;
    }

    if (data.code === 'banned') {
        showNotice({
            ...TEXT.notices.banned,
            body: data.message || TEXT.notices.banned.body,
        });
        return;
    }

    if (data.code === 'wallet_unavailable' || !state.walletAvailable) {
        els.sections.replaceChildren();
        els.empty.hidden = true;
        showNotice({
            ...TEXT.notices.walletUnavailable,
            body: data.message || TEXT.notices.walletUnavailable.body,
        });
        return;
    }

    showNotice({
        ...TEXT.notices.loadFailed,
        body: err?.message || TEXT.notices.loadFailed.body,
    });
});
