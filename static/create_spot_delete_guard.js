/*
 * iOS/Nimiq Pay draft-deletion navigation guard.
 *
 * Some WKWebView builds complete the DELETE request but ignore the first
 * asynchronous `window.location.href` navigation afterwards. That leaves the
 * deleted draft page visible with its confirmation modal locked in the busy
 * state. Observe only the successful DELETE request for the draft currently
 * being edited, remove the blocking modal immediately, and retry the same-origin
 * navigation using progressively simpler mechanisms.
 */

const CREATE_PATH_PATTERN = /^\/(?:create|create-spot)\/(\d+)\/?$/;
const DELETE_API_PATTERN = /^\/api\/create-spot\/(\d+)\/?$/;

let installed = false;
let pendingNavigation = null;

function currentSpotId() {
    const value = Number.parseInt(document.body?.dataset?.spotId || '0', 10);
    return Number.isFinite(value) && value > 0 ? value : null;
}

function currentCreatePathMatches(spotId) {
    const match = CREATE_PATH_PATTERN.exec(window.location.pathname);
    return Boolean(match && Number.parseInt(match[1], 10) === Number(spotId));
}

function requestDetails(input, init = {}) {
    const rawUrl = typeof input === 'string' || input instanceof URL
        ? String(input)
        : String(input?.url || '');
    const method = String(init?.method || input?.method || 'GET').toUpperCase();

    try {
        return {
            method,
            url: new URL(rawUrl, window.location.href),
        };
    } catch (_err) {
        return { method, url: null };
    }
}

function isCurrentDraftDelete(input, init, spotId) {
    const { method, url } = requestDetails(input, init);
    if (method !== 'DELETE' || !url || url.origin !== window.location.origin) return false;

    const match = DELETE_API_PATTERN.exec(url.pathname);
    return Boolean(match && Number.parseInt(match[1], 10) === Number(spotId));
}

function safeDestination(value) {
    try {
        const url = new URL(value || '/my-spots', window.location.origin);
        if (url.origin !== window.location.origin) return '/my-spots';
        return `${url.pathname}${url.search}${url.hash}`;
    } catch (_err) {
        return '/my-spots';
    }
}

function releaseDeleteOverlay() {
    const backdrop = document.getElementById('delete-spot-backdrop');
    if (backdrop) backdrop.hidden = true;

    const form = document.getElementById('create-spot-form');
    if (form) form.setAttribute('aria-busy', 'true');
}

function anchorFallback(destination) {
    const link = document.createElement('a');
    link.href = destination;
    link.target = '_self';
    link.rel = 'noopener';
    link.hidden = true;
    document.body.append(link);
    link.click();
    window.setTimeout(() => link.remove(), 1000);
}

function navigateAway(destination, spotId) {
    const target = safeDestination(destination);
    pendingNavigation = { destination: target, spotId: Number(spotId) };
    releaseDeleteOverlay();

    try {
        window.location.replace(target);
    } catch (_err) {
        // The timed fallbacks below still get a chance to navigate.
    }

    // WebKit occasionally ignores a navigation issued immediately after the
    // awaited fetch. Retry only while we are visibly still on the deleted draft.
    window.setTimeout(() => {
        if (!currentCreatePathMatches(spotId)) return;
        try {
            window.location.assign(target);
        } catch (_err) {
            anchorFallback(target);
        }
    }, 180);

    window.setTimeout(() => {
        if (!currentCreatePathMatches(spotId)) return;
        anchorFallback(target);
    }, 650);
}

function recoverFromBackForwardCache() {
    if (!pendingNavigation) return;
    if (!currentCreatePathMatches(pendingNavigation.spotId)) {
        pendingNavigation = null;
        return;
    }
    navigateAway(pendingNavigation.destination, pendingNavigation.spotId);
}

export function installCreateSpotDeleteNavigationGuard() {
    if (installed) return;

    const spotId = currentSpotId();
    if (!spotId) return;

    installed = true;
    const nativeFetch = window.fetch.bind(window);

    window.fetch = async function guardedFetch(input, init) {
        const watchesDelete = isCurrentDraftDelete(input, init, spotId);
        const response = await nativeFetch(input, init);

        if (watchesDelete && response.ok) {
            let destination = '/my-spots';
            try {
                const payload = await response.clone().json();
                if (payload?.ok !== false) {
                    destination = payload?.redirect_url || destination;
                }
            } catch (_err) {
                // A successful response is enough; the documented fallback is My Spots.
            }
            navigateAway(destination, spotId);
        }

        return response;
    };

    // A page restored from Safari/WKWebView's back-forward cache retains the old
    // disabled modal and JavaScript state. Redirect it away again on restoration.
    window.addEventListener('pageshow', recoverFromBackForwardCache);
}
