import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { makeMyClaimsText } from './interface_text.js?v=qol-v1-20260717';
import {
    appendBulletLine,
    appendDetailDescription,
    appendSpotTitleWithLock,
    buildSpotLinkControl,
    nimFromLunaText,
    spotPlaceText,
    unixToText,
} from './spot_ui.js?v=qol-v1-20260717';
import { createReusableSpotMap } from './spot_map.js';
import {
    createNoticePresenter,
    getLanguage,
    requestDeviceIdentifierHash,
} from './browser_utils.js?v=qol-v1-20260717';

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';

const TEXT = makeMyClaimsText({ appName: APP_NAME, nimiqPayUrl: NIMIQ_PAY_URL });

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    claimMap: null,
    expandedClaimIds: new Set(),
    refreshTimerId: null,
    refreshInFlight: false,
};

const els = {
    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),
    map: document.getElementById('claim-map'),
    title: document.getElementById('my-claims-title'),
    list: document.getElementById('my-claims-list'),
    empty: document.getElementById('empty-my-claims'),
};

const showNotice = createNoticePresenter(els);

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
        const err = new Error(data.message || TEXT.requestFailed);
        err.data = data;
        err.status = response.status;
        throw err;
    }
    return data;
}

async function identify() {
    if (!state.walletAvailable || !state.deviceIdHash) await requestWalletDeviceId();
}

function claimHref(claim) {
    const href = claim?.href || `/claim/${claim?.id || ''}`;
    const url = new URL(href, window.location.origin);
    url.searchParams.set('from', 'my-claims');
    return `${url.pathname}${url.search}`;
}

function claimStatusText(claim) {
    if (typeof claim.display_status_text === 'string' && claim.display_status_text.trim()) {
        return claim.display_status_text;
    }
    if (claim.status_label === 'success') return TEXT.status.success;
    if (claim.status_label === 'failed') return TEXT.status.failed;
    if (claim.status_label === 'pending') return TEXT.status.pending;
    return TEXT.status.unknown;
}

function claimStatusClass(claim) {
    const displayClass = String(claim.display_status_class || '').toLowerCase();
    if (['success', 'failed', 'pending'].includes(displayClass)) return `is-${displayClass}`;
    const status = String(claim.status_label || '').toLowerCase();
    if (status === 'success') return 'is-success';
    if (status === 'failed') return 'is-failed';
    if (status === 'pending') return 'is-pending';
    return 'is-unknown';
}

function claimMapColour(claim) {
    const displayClass = String(claim.display_status_class || '').toLowerCase();
    if (displayClass === 'success') return '#21bca5';
    if (displayClass === 'failed') return '#d94432';
    if (displayClass === 'pending') return '#ffc435';

    const status = String(claim.status_label || '').toLowerCase();
    if (status === 'success') return '#21bca5';
    if (status === 'failed') return '#d94432';
    if (status === 'pending') return '#ffc435';
    return '#8c90a8';
}

function formatSeconds(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const minutes = Math.floor(value / 60);
    const secs = Math.floor(value % 60);
    return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function createDurationTimerText(claim) {
    const span = document.createElement('span');
    const required = Math.max(0, Number(claim.duration_required || 0));
    const claimedAt = Math.max(0, Number(claim.claimed_at || 0));

    const update = () => {
        const now = Math.floor(Date.now() / 1000);
        const elapsed = claimedAt > 0 ? Math.max(0, now - claimedAt) : Number(claim.duration_elapsed || 0);
        const cappedElapsed = Math.min(elapsed, required);
        span.textContent = ` (${formatSeconds(cappedElapsed)}/${formatSeconds(required)})`;

        if (required > 0 && cappedElapsed >= required && span._nhTimerId) {
            window.clearInterval(span._nhTimerId);
            span._nhTimerId = null;
        }
    };

    update();
    if (required > 0 && Number(claim.status_code || 0) === 0) {
        span._nhTimerId = window.setInterval(update, 1000);
    }

    return span;
}

function buildStatusWithTimer(claim) {
    const fragment = document.createDocumentFragment();
    fragment.append(buildStatusKeyword(claim));

    if (Number(claim.duration_required || 0) > 0) {
        fragment.append(createDurationTimerText(claim));
    }

    return fragment;
}

function buildStatusKeyword(claim, text = null) {
    const keyword = document.createElement('span');
    keyword.className = `claim-status-keyword ${claimStatusClass(claim)}`;
    keyword.textContent = text || claimStatusText(claim);
    return keyword;
}

function buildClaimMeta(claim) {
    const spot = claim.spot || {};
    const claimed = unixToText(claim.claimed_at) || TEXT.recent;
    return `${spotPlaceText(spot)} - ${claimed}`;
}

function buildClaimDetail(claim) {
    const spot = claim.spot || {};
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail claim-list-detail';

    appendDetailDescription(detail, spot.description);

    const lines = document.createElement('ul');
    lines.className = 'spot-detail-lines';

    const claimedAt = unixToText(claim.claimed_at) || TEXT.recent;
    const attemptedValue = nimFromLunaText(claim.reward_amount || 0);
    appendBulletLine(lines, TEXT.statusLabel, buildStatusWithTimer(claim));
    appendBulletLine(lines, TEXT.claimed({ when: claimedAt, value: attemptedValue }));

    if (claim.is_prizedraw) {
        const participants = Number(spot.success_claim_count || 0) + Number(spot.pending_claim_count || 0);
        appendBulletLine(lines, TEXT.participants(participants));
    }

    if (spot.href) appendBulletLine(lines, buildSpotLinkControl(spot));
    detail.append(lines);
    return detail;
}

function setClaimExpanded(item, summary, detail, claimId, expanded) {
    item.classList.toggle('is-expanded', expanded);
    summary.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    detail.hidden = !expanded;

    if (expanded) {
        state.expandedClaimIds.add(claimId);
    } else {
        state.expandedClaimIds.delete(claimId);
    }
}

function buildClaimListItem(claim) {
    const claimId = Number(claim.id);
    const spot = claim.spot || {};

    const item = document.createElement('li');
    item.className = 'spot-list-item my-claim-list-item';

    const summary = document.createElement('button');
    summary.type = 'button';
    summary.className = 'spot-list-toggle';
    summary.setAttribute('aria-expanded', 'false');

    const topRow = document.createElement('span');
    topRow.className = 'spot-list-row spot-list-top-row';

    const title = document.createElement('span');
    title.className = 'spot-list-title';
    appendSpotTitleWithLock(title, spot);

    const badge = document.createElement('span');
    badge.className = `spot-badge ${claimStatusClass(claim)}`;
    badge.textContent = claimStatusText(claim);

    const chevron = document.createElement('span');
    chevron.className = 'spot-list-chevron';
    chevron.textContent = '⌄';
    chevron.setAttribute('aria-hidden', 'true');

    const actions = document.createElement('span');
    actions.className = 'spot-list-actions';
    actions.append(badge, chevron);
    topRow.append(title, actions);

    const bottomRow = document.createElement('span');
    bottomRow.className = 'spot-list-row spot-list-bottom-row';
    const meta = document.createElement('span');
    meta.className = 'spot-list-meta';
    meta.textContent = buildClaimMeta(claim);
    bottomRow.append(meta);
    summary.append(topRow, bottomRow);

    const detail = buildClaimDetail(claim);
    setClaimExpanded(item, summary, detail, claimId, state.expandedClaimIds.has(claimId));

    summary.addEventListener('click', () => {
        setClaimExpanded(item, summary, detail, claimId, summary.getAttribute('aria-expanded') !== 'true');
    });

    item.append(summary, detail);
    return item;
}

function renderEmptyClaims() {
    const link = document.createElement('a');
    link.href = '/spots';
    link.className = 'welcome-link';
    link.textContent = TEXT.emptyLink;
    els.empty.replaceChildren(
        document.createTextNode(TEXT.emptyBeforeLink),
        link,
        document.createTextNode(TEXT.emptyAfterLink)
    );
}

function renderClaims(claims) {
    els.title.textContent = TEXT.title(claims.length);
    els.list.replaceChildren();
    els.empty.hidden = claims.length > 0;
    if (claims.length <= 0) renderEmptyClaims();

    for (const claim of claims) {
        els.list.append(buildClaimListItem(claim));
    }
}

function claimToMapItem(claim) {
    const spot = claim.spot || {};
    return {
        ...spot,
        id: claim.id,
        title: spot.title || TEXT.fallbackTitle,
        href: claimHref(claim),
        claim,
    };
}

function claimPopupContent(item) {
    const wrap = document.createElement('span');
    wrap.className = 'nh-spot-popup-title';
    wrap.textContent = `${item.title || TEXT.fallbackTitle} - ${claimStatusText(item.claim || {})}`;
    return wrap;
}

function renderMap(claims) {
    const items = claims.map(claimToMapItem);
    try {
        if (!state.claimMap) {
            state.claimMap = createReusableSpotMap({
                mapEl: els.map,
                tileUrl: MAP_TILE_URL,
                tileAttribution: MAP_TILE_ATTRIBUTION,
                spots: items,
                colourForSpot: (item) => claimMapColour(item.claim || {}),
                popupBuilder: claimPopupContent,
                onSpotClick: (item) => {
                    window.location.href = item.href;
                },
            });
            if (!state.claimMap) throw new Error('Leaflet not available.');
            return;
        }
        state.claimMap.setSpots(items);
    } catch (err) {
        console.error(err);
        showNotice({
            title: TEXT.mapSetupFailed.title,
            body: TEXT.mapSetupFailed.body,
        });
    }
}

async function loadClaims() {
    return fetchJson('/api/my-claims', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authPayload()),
    });
}

function renderLoadedClaims(data) {
    state.user = data.user || null;
    const claims = Array.isArray(data.claims) ? data.claims : [];
    renderMap(claims);
    renderClaims(claims);
}

function stopClaimRefresh() {
    if (state.refreshTimerId) window.clearTimeout(state.refreshTimerId);
    state.refreshTimerId = null;
}

function scheduleClaimRefresh(delayMs = 10000) {
    stopClaimRefresh();
    if (document.visibilityState !== 'visible') return;
    state.refreshTimerId = window.setTimeout(refreshClaims, delayMs);
}

async function refreshClaims() {
    if (state.refreshInFlight || document.visibilityState !== 'visible') {
        scheduleClaimRefresh();
        return;
    }
    state.refreshInFlight = true;
    try {
        renderLoadedClaims(await loadClaims());
    } catch (err) {
        console.warn('NimHunt could not refresh claim statuses yet.', err);
    } finally {
        state.refreshInFlight = false;
        scheduleClaimRefresh();
    }
}

async function initMyClaims() {
    state.language = getLanguage();
    await identify();
    renderLoadedClaims(await loadClaims());
    scheduleClaimRefresh();
}

els.noticeOk?.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void refreshClaims();
    else stopClaimRefresh();
});
window.addEventListener('pageshow', () => {
    if (state.user) void refreshClaims();
});
window.addEventListener('beforeunload', stopClaimRefresh);

initMyClaims().catch((err) => {
    console.error(err);
    const data = err?.data || {};
    if (data.code === 'wallet_unavailable') {
        showNotice({
            ...TEXT.walletUnavailable,
        });
        return;
    }
    els.empty.hidden = false;
    els.empty.textContent = data.message || TEXT.loadFailed;
});
