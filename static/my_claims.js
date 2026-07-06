import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import {
    appendBulletLine,
    appendDetailDescription,
    appendSpotTitleWithLock,
    buildSpotLinkControl,
    durationText,
    nimFromLunaText,
    spotPlaceText,
    unixToText,
} from './spot_ui.js?v=small-polish-v1-20260705';
import { createReusableSpotMap } from './spot_map.js';

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    claimMap: null,
    expandedClaimIds: new Set(),
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

function getLanguage() {
    const payLanguage = window.nimiqPay?.language;
    if (typeof payLanguage === 'string' && payLanguage.length > 0) return payLanguage;
    const browserLanguage = navigator.language || navigator.userLanguage;
    return typeof browserLanguage === 'string' && browserLanguage ? browserLanguage.split('-')[0] : 'en';
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

async function requestWalletDeviceId() {
    try {
        const id = await requestDeviceIdentifier({ reason: `View the ${APP_NAME} claims made by this device.` });
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
        const err = new Error(data.message || 'Request failed.');
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
    if (claim.status_label === 'success') return 'Success';
    if (claim.status_label === 'failed') return 'Failed';
    if (claim.status_label === 'pending') return 'Pending';
    return 'Unknown';
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
    const claimed = unixToText(claim.claimed_at) || 'recently';
    return `${spotPlaceText(spot)} - ${claimed}`;
}

function buildClaimDetail(claim) {
    const spot = claim.spot || {};
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail claim-list-detail';

    appendDetailDescription(detail, spot.description);

    const lines = document.createElement('ul');
    lines.className = 'spot-detail-lines';

    const claimedAt = unixToText(claim.claimed_at) || 'recently';
    const attemptedValue = nimFromLunaText(claim.reward_amount || 0);
    appendBulletLine(lines, 'Status: ', buildStatusWithTimer(claim));
    appendBulletLine(lines, `Claimed ${claimedAt} (${attemptedValue})`);

    if (claim.is_prizedraw) {
        const participants = Number(spot.success_claim_count || 0) + Number(spot.pending_claim_count || 0);
        appendBulletLine(lines, `${participants} current participants`);
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
    link.textContent = 'Go collect';
    els.empty.replaceChildren(
        document.createTextNode('You have no claims. '),
        link,
        document.createTextNode(' some!')
    );
}

function renderClaims(claims) {
    els.title.textContent = `My Claims (${claims.length})`;
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
        title: spot.title || 'Claim',
        href: claimHref(claim),
        claim,
    };
}

function claimPopupContent(item) {
    const wrap = document.createElement('span');
    wrap.className = 'nh-spot-popup-title';
    wrap.textContent = `${item.title || 'Claim'} - ${claimStatusText(item.claim || {})}`;
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
            title: 'Map setup failed',
            body: 'The claim map could not be loaded. Reload the page and try again.',
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

async function initMyClaims() {
    state.language = getLanguage();
    await identify();
    const data = await loadClaims();
    state.user = data.user || null;
    const claims = Array.isArray(data.claims) ? data.claims : [];
    renderMap(claims);
    renderClaims(claims);
}

els.noticeOk?.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

initMyClaims().catch((err) => {
    console.error(err);
    const data = err?.data || {};
    if (data.code === 'wallet_unavailable') {
        showNotice({
            title: `Open ${APP_NAME} in Nimiq Pay`,
            body: `${APP_NAME} needs Nimiq Pay to identify this device before showing My Claims.`,
            href: NIMIQ_PAY_URL,
            linkText: 'Open Nimiq Pay',
        });
        return;
    }
    els.empty.hidden = false;
    els.empty.textContent = data.message || 'My Claims could not be loaded.';
});
