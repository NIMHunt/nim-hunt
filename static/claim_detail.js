import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import { makeClaimDetailText } from './interface_text.js?v=transaction-integrity-v1-20260721';
import {
    createNoticePresenter,
    getLanguage,
    requestDeviceIdentifierHash,
} from './browser_utils.js?v=polish-live-v1-20260720';
import {
    appendBulletLine,
    appendDetailDescription,
    appendSpotTitleWithLock,
    buildSpotLinkControl,
    nimFromLunaText,
    unixToText,
} from './spot_ui.js?v=spot-requirements-v1-20260725';

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const CLAIM_ID = Number.parseInt(document.body.dataset.claimId || '0', 10);
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';

const TEXT = makeClaimDetailText({ appName: APP_NAME, nimiqPayUrl: NIMIQ_PAY_URL });

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    currentClaim: null,
    heartbeatTimerId: null,
    heartbeatInFlight: false,
    heartbeatNoticeShown: false,
    statusPollTimerId: null,
    statusPollInFlight: false,
    celebrationShown: false,
    timerIds: [],
};

const els = {
    noticeBackdrop: document.getElementById('notice-backdrop'),
    noticeTitle: document.getElementById('notice-title'),
    noticeBody: document.getElementById('notice-body'),
    noticeLink: document.getElementById('notice-link'),
    noticeOk: document.getElementById('notice-ok'),
    title: document.getElementById('claim-page-title'),
    list: document.getElementById('claim-detail-list'),
    fallback: document.getElementById('claim-detail-fallback'),
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

function authPayload(location = null) {
    return {
        device_id_hash: state.deviceIdHash,
        wallet_available: state.walletAvailable,
        language: state.language,
        location_available: Boolean(location),
        lat: location ? location.lat : null,
        long: location ? location.long : null,
        accuracy: location ? location.accuracy : null,
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

function requestCurrentLocation() {
    return new Promise((resolve, reject) => {
        if (!navigator.geolocation) {
            reject(new Error(TEXT.geolocationUnavailable));
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
            (err) => reject(err),
            {
                enableHighAccuracy: true,
                timeout: 15000,
                maximumAge: 20000,
            }
        );
    });
}

function clearStatusTimers() {
    for (const timerId of state.timerIds) window.clearInterval(timerId);
    state.timerIds = [];
}

function stopHeartbeat() {
    if (state.heartbeatTimerId) window.clearInterval(state.heartbeatTimerId);
    state.heartbeatTimerId = null;
    state.heartbeatInFlight = false;
}

function stopStatusPolling() {
    if (state.statusPollTimerId) window.clearTimeout(state.statusPollTimerId);
    state.statusPollTimerId = null;
    state.statusPollInFlight = false;
}

function formatSeconds(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const minutes = Math.floor(value / 60);
    const secs = Math.floor(value % 60);
    return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function durationGoalReached(claim, now = Math.floor(Date.now() / 1000)) {
    const required = Math.max(0, Number(claim?.duration_required || 0));
    const claimedAt = Math.max(0, Number(claim?.claimed_at || 0));
    return required > 0 && claimedAt > 0 && now >= claimedAt + required;
}

function claimPresentationSignature(claim) {
    const spot = claim?.spot || {};
    return JSON.stringify([
        claim?.status_label,
        claim?.display_status_label,
        claim?.display_status_text,
        claim?.display_status_class,
        Boolean(claim?.location_monitoring_required),
        Boolean(claim?.viewer_is_recipient),
        Number(claim?.payout_pending_count || 0),
        Number(claim?.payout_confirmed_count || 0),
        Number(claim?.payout_failed_count || 0),
        Number(spot.success_claim_count || 0),
        Number(spot.pending_claim_count || 0),
    ]);
}

function applyClaimUpdate(claim, { forceRender = false } = {}) {
    if (
        forceRender
        || !state.currentClaim
        || claimPresentationSignature(state.currentClaim) !== claimPresentationSignature(claim)
    ) {
        renderClaim(claim);
        return;
    }

    state.currentClaim = claim;
    syncHeartbeat(claim);
    syncStatusPolling(claim);
}


function createDurationTimerText(claim, statusKeyword) {
    const span = document.createElement('span');
    const required = Math.max(0, Number(claim.duration_required || 0));
    const claimedAt = Math.max(0, Number(claim.claimed_at || 0));

    const update = () => {
        const now = Math.floor(Date.now() / 1000);
        const elapsed = claimedAt > 0 ? Math.max(0, now - claimedAt) : Number(claim.duration_elapsed || 0);
        const cappedElapsed = Math.min(elapsed, required);
        const reachedGoal = required > 0 && cappedElapsed >= required;
        if (reachedGoal) {
            statusKeyword.textContent = 'Verifying';
            span.textContent = '';
        } else {
            span.textContent = ` (${formatSeconds(cappedElapsed)}/${formatSeconds(required)})`;
        }

        if (reachedGoal && span._nhTimerId) {
            window.clearInterval(span._nhTimerId);
            span._nhTimerId = null;
            handleDurationGoalReached();
        }
    };

    update();
    if (required > 0 && Number(claim.status_code || 0) === 0) {
        span._nhTimerId = window.setInterval(update, 1000);
        state.timerIds.push(span._nhTimerId);
    }

    return span;
}

function claimStatusText(claim) {
    if (typeof claim.display_status_text === 'string' && claim.display_status_text.trim()) {
        return claim.display_status_text;
    }
    if (claim.status_label === 'success') return TEXT.status.success;
    if (claim.status_label === 'failed') return TEXT.status.failed;
    return TEXT.status.pending;
}

function claimStatusColourClass(claim) {
    const displayClass = String(claim.display_status_class || '').toLowerCase();
    if (['success', 'failed', 'pending'].includes(displayClass)) return `is-${displayClass}`;
    const status = String(claim.status_label || '').toLowerCase();
    if (status === 'success') return 'is-success';
    if (status === 'failed') return 'is-failed';
    return 'is-pending';
}

function buildStatusKeyword(claim, text = null) {
    const keyword = document.createElement('span');
    keyword.className = `claim-status-keyword ${claimStatusColourClass(claim)}`;
    keyword.textContent = text || claimStatusText(claim);
    return keyword;
}

function buildStatusWithTimer(claim) {
    const fragment = document.createDocumentFragment();
    const statusKeyword = buildStatusKeyword(claim);
    fragment.append(statusKeyword);

    const status = String(claim.status_label || '').toLowerCase();
    const durationRemaining = Number(claim.duration_remaining || 0);
    if (Number(claim.duration_required || 0) > 0 && status === 'pending' && durationRemaining > 0) {
        fragment.append(createDurationTimerText(claim, statusKeyword));
    }

    return fragment;
}

function validCoordinatePair(lat, long) {
    return Number.isFinite(Number(lat)) && Number.isFinite(Number(long));
}

function buildSpotMapShell() {
    const map = document.createElement('div');
    map.className = 'spot-detail-map claim-detail-map';
    map.setAttribute('aria-label', TEXT.mapAriaLabel);
    return map;
}

function spotMapColour(spot) {
    return spot.is_prizedraw ? '#ffc435' : '#21bca5';
}


function metreBoundsAround(lat, long, radiusMetres) {
    // Avoid Leaflet Circle.getBounds() here. On some freshly-created, locked
    // maps the circle can exist before Leaflet has finished attaching enough
    // internal map state, which causes Circle.getBounds() to crash.
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

function shouldShowClaimLocation(claim) {
    return Boolean(
        claim.viewer_is_recipient
        && claim.location_monitoring_required
        && Number(claim.duration_remaining || 0) > 0
        && validCoordinatePair(claim.lat, claim.long)
    );
}

function renderLockedClaimMap(mapEl, claim) {
    const spot = claim.spot || {};
    if (!mapEl || !window.L || !validCoordinatePair(spot.lat, spot.long)) return;

    const centre = [Number(spot.lat), Number(spot.long)];
    const colour = spotMapColour(spot);
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
    });

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

    window.L.circleMarker(centre, {
        radius: 7,
        color: '#ffffff',
        fillColor: colour,
        fillOpacity: 1,
        opacity: 1,
        weight: 2,
    }).addTo(map);

    const bounds = metreBoundsAround(centre[0], centre[1], spot.radius);
    if (shouldShowClaimLocation(claim)) {
        const userLatLng = [Number(claim.lat), Number(claim.long)];
        window.L.circleMarker(userLatLng, {
            radius: 7,
            color: '#ffffff',
            fillColor: '#1f2348',
            fillOpacity: 1,
            opacity: 1,
            weight: 2,
        }).addTo(map);
        bounds.extend(userLatLng);
    }

    const paddedBounds = bounds.pad(0.18);
    map.invalidateSize(false);
    map.fitBounds(paddedBounds, { animate: false, maxZoom: 16 });
    window.setTimeout(() => {
        map.invalidateSize(false);
        map.fitBounds(paddedBounds, { animate: false, maxZoom: 16 });
    }, 0);
}

function buildClaimDetail(claim) {
    const spot = claim.spot || {};
    const detail = document.createElement('div');
    detail.className = 'spot-list-detail claim-list-detail';

    const map = buildSpotMapShell();
    detail.append(map);

    appendDetailDescription(detail, spot.description);

    const lines = document.createElement('ul');
    lines.className = 'spot-detail-lines';

    const claimedAt = unixToText(claim.claimed_at) || TEXT.now;
    const attemptedValue = nimFromLunaText(claim.reward_amount || 0);
    appendBulletLine(lines, TEXT.statusLabel, buildStatusWithTimer(claim));
    appendBulletLine(lines, TEXT.claimed({ when: claimedAt, value: attemptedValue }));

    if (claim.is_prizedraw) {
        const participants = Number(spot.success_claim_count || 0) + Number(spot.pending_claim_count || 0);
        appendBulletLine(lines, TEXT.participants(participants));
    }

    if (shouldShowClaimLocation(claim)) {
        const score = Math.round(Number(claim.duration_score || 0) * 100);
        appendBulletLine(lines, TEXT.locationScore(score));
    }

    if (spot.href) appendBulletLine(lines, buildSpotLinkControl(spot));

    detail.append(lines);
    return { detail, map };
}

function renderClaim(claim) {
    const previousClaim = state.currentClaim;
    state.currentClaim = claim;
    clearStatusTimers();

    const spot = claim.spot || {};
    els.fallback.hidden = true;
    els.title.replaceChildren();
    appendSpotTitleWithLock(els.title, { ...spot, title: spot.title || TEXT.fallbackTitle });

    const item = document.createElement('li');
    item.className = 'spot-list-item is-expanded';

    const summary = document.createElement('div');
    summary.className = 'spot-list-toggle spot-detail-static-summary';
    summary.setAttribute('aria-expanded', 'true');

    const topRow = document.createElement('span');
    topRow.className = 'spot-list-row spot-list-top-row';

    const title = document.createElement('span');
    title.className = 'spot-list-title';
    appendSpotTitleWithLock(title, spot);

    const badge = document.createElement('span');
    badge.className = `spot-badge ${claimStatusColourClass(claim)}`;
    badge.textContent = claimStatusText(claim);

    const actions = document.createElement('span');
    actions.className = 'spot-list-actions';
    actions.append(badge);

    topRow.append(title, actions);

    const bottomRow = document.createElement('span');
    bottomRow.className = 'spot-list-row spot-list-bottom-row';
    const meta = document.createElement('span');
    meta.className = 'spot-list-meta';
    meta.textContent = spot.city || spot.country || TEXT.unknownArea;
    bottomRow.append(meta);
    summary.append(topRow, bottomRow);

    const { detail, map } = buildClaimDetail(claim);
    detail.hidden = false;

    item.append(summary, detail);
    els.list.replaceChildren(item);
    requestAnimationFrame(() => renderLockedClaimMap(map, claim));

    syncHeartbeat(claim);
    syncStatusPolling(claim);
    maybeCelebrateClaimTransition(previousClaim, claim);
}

async function fetchClaimDetail({ forceRender = false } = {}) {
    const data = await fetchJson(`/api/claim/${CLAIM_ID}/detail`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authPayload()),
    });
    state.user = data.user || null;
    applyClaimUpdate(data.claim, { forceRender });
    return data.claim;
}


function isExpectedClaimDetailError(err) {
    const status = Number(err?.status || 0);
    const code = String(err?.data?.code || '');

    // These are normal user-facing outcomes for claim links, not JavaScript bugs.
    return (
        (status === 404 && ['claim_missing', 'spot_missing'].includes(code))
        || (status === 403 && code === 'not_allowed')
        || code === 'wallet_unavailable'
    );
}

function claimNeedsLiveRefresh(claim) {
    if (!claim) return false;
    const status = String(claim.status_label || '').toLowerCase();
    if (status === 'pending') {
        const durationRequired = Number(claim.duration_required || 0);
        const serverRemaining = Number(claim.duration_remaining || 0);
        // Trust the server when it has already reached the verification phase.
        // A phone clock that is a few seconds slow must not stop status polling.
        if (durationRequired > 0 && serverRemaining > 0 && !durationGoalReached(claim)) return false;
        return true;
    }
    if (!claim.is_prizedraw) {
        return status === 'success' && Number(claim.payout_confirmed_count || 0) <= 0;
    }
    return ['waiting', 'pending', 'won_pending', 'won_retrying'].includes(
        String(claim.display_status_label || '').toLowerCase()
    );
}

function scheduleStatusPoll(delayMs = 5000) {
    if (state.statusPollTimerId) window.clearTimeout(state.statusPollTimerId);
    state.statusPollTimerId = null;
    if (!claimNeedsLiveRefresh(state.currentClaim) || document.visibilityState !== 'visible') return;
    state.statusPollTimerId = window.setTimeout(refreshClaimStatus, delayMs);
}

function syncStatusPolling(claim) {
    if (!claimNeedsLiveRefresh(claim)) {
        stopStatusPolling();
        return;
    }
    scheduleStatusPoll();
}

async function refreshClaimStatus() {
    if (state.statusPollInFlight || document.visibilityState !== 'visible') {
        scheduleStatusPoll();
        return;
    }
    state.statusPollInFlight = true;
    try {
        await fetchClaimDetail();
    } catch (err) {
        console.warn('NimHunt could not refresh this claim yet.', err);
    } finally {
        state.statusPollInFlight = false;
        scheduleStatusPoll();
    }
}

function handleDurationGoalReached() {
    const claim = state.currentClaim;
    if (!claim || String(claim.status_label || '').toLowerCase() !== 'pending') return;
    if (claim.location_monitoring_required && claim.viewer_is_recipient) {
        void sendLocationHeartbeat();
    } else {
        void refreshClaimStatus();
    }
    scheduleStatusPoll(1800);
}

function isWinningClaim(claim) {
    return String(claim?.display_status_label || '').toLowerCase().startsWith('won');
}

function isSuccessfulClaim(claim) {
    return String(claim?.status_label || '').toLowerCase() === 'success';
}

function maybeCelebrateClaimTransition(previousClaim, claim) {
    if (!previousClaim || state.celebrationShown) return;
    const becameWinner = isWinningClaim(claim) && !isWinningClaim(previousClaim);
    const becameSuccessful = !claim.is_prizedraw
        && isSuccessfulClaim(claim)
        && !isSuccessfulClaim(previousClaim);
    if (!becameWinner && !becameSuccessful) return;
    state.celebrationShown = true;
    window.requestAnimationFrame(() => window.setTimeout(burstConfetti, 120));
}

async function sendLocationHeartbeat() {
    const claim = state.currentClaim;
    if (!claim || !claim.location_monitoring_required || !claim.viewer_is_recipient) {
        stopHeartbeat();
        return;
    }
    if (state.heartbeatInFlight) return;

    state.heartbeatInFlight = true;
    try {
        const location = await requestCurrentLocation();
        const data = await fetchJson(`/api/claim/${CLAIM_ID}/location`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload(location)),
        });
        state.user = data.user || null;
        applyClaimUpdate(data.claim);
    } catch (err) {
        console.error(err);
        if (!state.heartbeatNoticeShown) {
            state.heartbeatNoticeShown = true;
            showNotice({
                title: TEXT.locationNeeded.title,
                body: TEXT.locationNeeded.body,
            });
        }
    } finally {
        state.heartbeatInFlight = false;
    }
}

function syncHeartbeat(claim) {
    const needsHeartbeat = Boolean(claim.location_monitoring_required && claim.viewer_is_recipient);
    if (!needsHeartbeat) {
        stopHeartbeat();
        return;
    }

    if (state.heartbeatTimerId) return;
    const intervalSeconds = Math.max(10, Number(claim.location_check_interval || 60));
    sendLocationHeartbeat();
    state.heartbeatTimerId = window.setInterval(sendLocationHeartbeat, intervalSeconds * 1000);
}

function burstConfetti() {
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

async function initClaimDetail() {
    state.language = getLanguage();
    await identify();
    await fetchClaimDetail({ forceRender: true });

    const params = new URLSearchParams(window.location.search);
    if (params.get('claimed') === '1') {
        params.delete('claimed');
        const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`;
        window.history.replaceState({}, '', next);
        const claim = state.currentClaim;
        if (claim && (!claim.is_prizedraw || isWinningClaim(claim))) {
            state.celebrationShown = true;
            burstConfetti();
        }
    }
}

els.noticeOk?.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        if (claimNeedsLiveRefresh(state.currentClaim)) void refreshClaimStatus();
    } else {
        stopStatusPolling();
    }
});

window.addEventListener('pageshow', () => {
    if (claimNeedsLiveRefresh(state.currentClaim)) void refreshClaimStatus();
});

window.addEventListener('beforeunload', () => {
    stopHeartbeat();
    stopStatusPolling();
    clearStatusTimers();
});

initClaimDetail().catch((err) => {
    const data = err?.data || {};
    if (!isExpectedClaimDetailError(err)) console.error(err);

    if (data.code === 'wallet_unavailable') {
        showNotice({
            ...TEXT.walletUnavailable,
        });
        return;
    }
    els.fallback.hidden = false;
    els.fallback.textContent = data.message || TEXT.loadFailed;
});
