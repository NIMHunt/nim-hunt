import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';
import {
    appendBulletLine,
    appendDetailDescription,
    appendSpotTitleWithLock,
    buildSpotLinkControl,
    nimFromLunaText,
    unixToText,
} from './spot_ui.js?v=small-polish-v1-20260705';

const APP_NAME = document.body.dataset.appName || 'NimHunt';
const NIMIQ_PAY_URL = document.body.dataset.nimiqPayUrl || 'https://nimpay.app';
const CLAIM_ID = Number.parseInt(document.body.dataset.claimId || '0', 10);
const MAP_TILE_URL = document.body.dataset.mapTileUrl || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAP_TILE_ATTRIBUTION = document.body.dataset.mapTileAttribution || '&copy; OpenStreetMap contributors';

const state = {
    deviceIdHash: null,
    walletAvailable: false,
    language: null,
    user: null,
    currentClaim: null,
    heartbeatTimerId: null,
    heartbeatInFlight: false,
    heartbeatNoticeShown: false,
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
        const id = await requestDeviceIdentifier({ reason: `View this ${APP_NAME} claim from this device.` });
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

function requestCurrentLocation() {
    return new Promise((resolve, reject) => {
        if (!navigator.geolocation) {
            reject(new Error('Geolocation is not available.'));
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
        state.timerIds.push(span._nhTimerId);
    }

    return span;
}

function claimStatusText(claim) {
    if (typeof claim.display_status_text === 'string' && claim.display_status_text.trim()) {
        return claim.display_status_text;
    }
    if (claim.status_label === 'success') return 'Success';
    if (claim.status_label === 'failed') return 'Failed';
    return 'Pending';
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
    fragment.append(buildStatusKeyword(claim));

    if (Number(claim.duration_required || 0) > 0) {
        fragment.append(createDurationTimerText(claim));
    }

    return fragment;
}

function validCoordinatePair(lat, long) {
    return Number.isFinite(Number(lat)) && Number.isFinite(Number(long));
}

function buildSpotMapShell() {
    const map = document.createElement('div');
    map.className = 'spot-detail-map claim-detail-map';
    map.setAttribute('aria-label', 'Claim spot map');
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

    const claimedAt = unixToText(claim.claimed_at) || 'now';
    const attemptedValue = nimFromLunaText(claim.reward_amount || 0);
    appendBulletLine(lines, 'Status: ', buildStatusWithTimer(claim));
    appendBulletLine(lines, `Claimed ${claimedAt} (${attemptedValue})`);

    if (claim.is_prizedraw) {
        const participants = Number(spot.success_claim_count || 0) + Number(spot.pending_claim_count || 0);
        appendBulletLine(lines, `${participants} current participants`);
    }

    if (shouldShowClaimLocation(claim)) {
        const score = Math.round(Number(claim.duration_score || 0) * 100);
        appendBulletLine(lines, `Location score ${score}%`);
    }

    if (spot.href) appendBulletLine(lines, buildSpotLinkControl(spot));

    detail.append(lines);
    return { detail, map };
}

function renderClaim(claim) {
    state.currentClaim = claim;
    clearStatusTimers();

    const spot = claim.spot || {};
    els.fallback.hidden = true;
    els.title.replaceChildren();
    appendSpotTitleWithLock(els.title, { ...spot, title: spot.title || 'Claim' });

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
    meta.textContent = spot.city || spot.country || 'Unknown area';
    bottomRow.append(meta);
    summary.append(topRow, bottomRow);

    const { detail, map } = buildClaimDetail(claim);
    detail.hidden = false;

    item.append(summary, detail);
    els.list.replaceChildren(item);
    requestAnimationFrame(() => renderLockedClaimMap(map, claim));

    syncHeartbeat(claim);
}

async function fetchClaimDetail() {
    const data = await fetchJson(`/api/claim/${CLAIM_ID}/detail`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authPayload()),
    });
    state.user = data.user || null;
    renderClaim(data.claim);
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
        renderClaim(data.claim);
    } catch (err) {
        console.error(err);
        if (!state.heartbeatNoticeShown) {
            state.heartbeatNoticeShown = true;
            showNotice({
                title: 'Location needed',
                body: `Keep ${APP_NAME} open and allow location access until this claim finishes. If no fresh location reaches the server for too long, the claim will fail.`,
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
    await fetchClaimDetail();

    const params = new URLSearchParams(window.location.search);
    if (params.get('claimed') === '1') {
        params.delete('claimed');
        const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`;
        window.history.replaceState({}, '', next);
        burstConfetti();
    }
}

els.noticeOk?.addEventListener('click', () => {
    els.noticeBackdrop.hidden = true;
});

window.addEventListener('beforeunload', () => {
    stopHeartbeat();
    clearStatusTimers();
});

initClaimDetail().catch((err) => {
    const data = err?.data || {};
    if (!isExpectedClaimDetailError(err)) console.error(err);

    if (data.code === 'wallet_unavailable') {
        showNotice({
            title: `Open ${APP_NAME} in Nimiq Pay`,
            body: `${APP_NAME} needs Nimiq Pay to identify this device before showing the claim.`,
            href: NIMIQ_PAY_URL,
            linkText: 'Open Nimiq Pay',
        });
        return;
    }
    els.fallback.hidden = false;
    els.fallback.textContent = data.message || 'This claim could not be loaded.';
});
