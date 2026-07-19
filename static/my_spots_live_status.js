const MY_SPOTS_PATH = '/api/my-spots';
const POLL_INTERVAL_MS = 5000;

const nativeFetch = window.fetch.bind(window);
let requestOptions = null;
let latestData = null;
let baseline = new Map();
let pollTimer = null;
let pollInFlight = false;
let reloadStarted = false;

function requestPath(input) {
    try {
        if (input instanceof Request) return new URL(input.url, window.location.origin).pathname;
        return new URL(String(input), window.location.origin).pathname;
    } catch (err) {
        return '';
    }
}

function cloneHeaders(headers) {
    const output = {};
    new Headers(headers || {}).forEach((value, key) => {
        output[key] = value;
    });
    return output;
}

function rememberRequest(input, init = {}) {
    if (requestPath(input) !== MY_SPOTS_PATH) return;
    const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (method !== 'POST') return;

    const body = init.body;
    if (typeof body !== 'string') return;
    requestOptions = {
        method: 'POST',
        headers: cloneHeaders(init.headers || (input instanceof Request ? input.headers : undefined)),
        body,
        cache: 'no-store',
    };
}

function draftSpots(data) {
    return Array.isArray(data?.spots)
        ? data.spots.filter((spot) => String(spot?.status_label || '').toLowerCase() === 'draft')
        : [];
}

function fingerprint(spot) {
    const deposit = spot?.deposit || {};
    return JSON.stringify({
        status: deposit.status || '',
        confirmed: Number(deposit.confirmed_amount || 0),
        pending: Number(deposit.pending_amount || 0),
        amountDue: Number(deposit.amount_due || 0),
        feeStatus: deposit.fee_status || '',
        feeSubmitted: Boolean(deposit.fee_submitted || deposit.fee_paid),
        feeConfirmed: Boolean(deposit.fee_confirmed),
        attention: Boolean(deposit.requires_attention),
        canDeposit: Boolean(spot?.can_deposit),
        canPublish: Boolean(spot?.can_publish),
        blockReason: spot?.publish_block_reason || '',
    });
}

function snapshot(data) {
    return new Map(draftSpots(data).map((spot) => [Number(spot.id), fingerprint(spot)]));
}

function replaceInternalFeeCopy(root = document) {
    root.querySelectorAll('.spot-list-meta-notice').forEach((notice) => {
        if (/creation fee processing/i.test(notice.textContent || '')) {
            notice.textContent = 'Deposit Processing';
        }
    });
}

function applyLiveDepositCopy(data) {
    replaceInternalFeeCopy();

    const cards = [...document.querySelectorAll('.my-spots-section-card.is-draft .spot-list-item')];
    const spots = draftSpots(data);
    for (let index = 0; index < Math.min(cards.length, spots.length); index += 1) {
        const deposit = spots[index]?.deposit || {};
        const notice = cards[index].querySelector('.spot-list-meta-notice');
        if (!notice) continue;
        if (deposit.status === 'processing') {
            if (notice.textContent !== 'Deposit Processing') notice.textContent = 'Deposit Processing';
            notice.classList.remove('is-ready', 'is-missing');
            notice.classList.add('is-partial');
        }
    }
}

function needsReload(previous, next, data) {
    for (const spot of draftSpots(data)) {
        const id = Number(spot.id);
        if (!previous.has(id) || previous.get(id) === next.get(id)) continue;

        const depositStatus = String(spot?.deposit?.status || '');
        if (
            Boolean(spot.can_publish)
            || Boolean(spot.can_deposit)
            || ['ready', 'partial', 'missing'].includes(depositStatus)
        ) {
            return true;
        }
    }
    return false;
}

function hasProcessingDraft(data) {
    return draftSpots(data).some((spot) => {
        const deposit = spot?.deposit || {};
        return deposit.status === 'processing'
            || Number(deposit.pending_amount || 0) > 0;
    });
}

function schedulePoll(delay = POLL_INTERVAL_MS) {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = null;
    if (!requestOptions || !hasProcessingDraft(latestData) || document.visibilityState !== 'visible') return;
    pollTimer = window.setTimeout(poll, delay);
}

async function poll() {
    if (pollInFlight || reloadStarted || !requestOptions || document.visibilityState !== 'visible') {
        schedulePoll();
        return;
    }

    pollInFlight = true;
    try {
        const response = await nativeFetch(MY_SPOTS_PATH, requestOptions);
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.ok || !Array.isArray(data.spots)) return;

        const next = snapshot(data);
        const shouldReload = baseline.size > 0 && needsReload(baseline, next, data);
        latestData = data;
        baseline = next;
        applyLiveDepositCopy(data);

        if (shouldReload) {
            reloadStarted = true;
            window.location.reload();
            return;
        }
    } catch (err) {
        console.warn('NimHunt could not refresh deposit status.', err);
    } finally {
        pollInFlight = false;
        if (!reloadStarted) schedulePoll();
    }
}

window.fetch = async function nimHuntFundingAwareFetch(input, init = {}) {
    rememberRequest(input, init);
    const response = await nativeFetch(input, init);

    if (requestPath(input) === MY_SPOTS_PATH && requestOptions) {
        response.clone().json().then((data) => {
            if (!data?.ok || !Array.isArray(data.spots)) return;
            latestData = data;
            baseline = snapshot(data);
            window.requestAnimationFrame(() => applyLiveDepositCopy(data));
            schedulePoll(1000);
        }).catch(() => {});
    }
    return response;
};

const observer = new MutationObserver(() => {
    replaceInternalFeeCopy();
    if (latestData) applyLiveDepositCopy(latestData);
});
observer.observe(document.documentElement, { childList: true, subtree: true });

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') schedulePoll(0);
    else if (pollTimer) window.clearTimeout(pollTimer);
});

window.addEventListener('pageshow', () => schedulePoll(0));
