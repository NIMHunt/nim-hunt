/*
 * Shared browser lifecycle repairs.
 *
 * Safari and embedded WKWebViews may restore a page with its previous DOM and
 * JavaScript memory intact. Some of them report unreliable `pageshow.persisted`
 * and navigation-timing values. Store a short per-page marker during pagehide
 * as well as in the cached DOM, hide open cards before the page is frozen, and
 * reload only the affected history entry when it is restored. This also clears
 * page-specific busy flags such as `creatingSpot` that cannot safely be reset
 * from this shared module.
 */

const BACKDROP_SELECTOR = '.notice-backdrop';
const OPEN_BACKDROP_SELECTOR = '.notice-backdrop:not([hidden])';
const CARD_NAVIGATION_MARKER = 'data-nimhunt-card-navigation-pending';
const STORAGE_KEY_PREFIX = 'nimhunt:card-navigation:';
const MANUAL_CLOSE_GRACE_MILLISECONDS = 250;

function navigationType(performanceObj) {
    try {
        const entry = performanceObj?.getEntriesByType?.('navigation')?.[0];
        if (entry?.type) return String(entry.type);
    } catch (_err) {
        // Older WebViews may not implement PerformanceNavigationTiming.
    }

    const legacyType = Number(performanceObj?.navigation?.type);
    if (legacyType === 2) return 'back_forward';
    if (Number.isFinite(legacyType)) return 'navigate';
    return null;
}

function documentMarkerElement(documentObj) {
    return documentObj?.documentElement || documentObj?.body || null;
}

function hasCardNavigationMarker(documentObj) {
    return Boolean(documentMarkerElement(documentObj)?.hasAttribute?.(CARD_NAVIGATION_MARKER));
}

function setCardNavigationMarker(documentObj) {
    documentMarkerElement(documentObj)?.setAttribute?.(CARD_NAVIGATION_MARKER, '1');
}

function clearCardNavigationMarker(documentObj) {
    documentMarkerElement(documentObj)?.removeAttribute?.(CARD_NAVIGATION_MARKER);
}

function allBackdrops(documentObj) {
    return [...(documentObj?.querySelectorAll?.(BACKDROP_SELECTOR) || [])];
}

function openBackdrops(documentObj) {
    return [...(documentObj?.querySelectorAll?.(OPEN_BACKDROP_SELECTOR) || [])];
}

function storageKey(windowObj) {
    const pathname = String(windowObj?.location?.pathname || '');
    const search = String(windowObj?.location?.search || '');
    return `${STORAGE_KEY_PREFIX}${pathname}${search}`;
}

function sessionStorageObject(windowObj) {
    try {
        return windowObj?.sessionStorage || null;
    } catch (_err) {
        return null;
    }
}

function hasStoredNavigationMarker(windowObj) {
    try {
        return sessionStorageObject(windowObj)?.getItem(storageKey(windowObj)) === '1';
    } catch (_err) {
        return false;
    }
}

function setStoredNavigationMarker(windowObj) {
    try {
        sessionStorageObject(windowObj)?.setItem(storageKey(windowObj), '1');
    } catch (_err) {
        // Private browsing or a restrictive WebView may block sessionStorage.
    }
}

function clearStoredNavigationMarker(windowObj) {
    try {
        sessionStorageObject(windowObj)?.removeItem(storageKey(windowObj));
    } catch (_err) {
        // The DOM marker remains available when storage is unavailable.
    }
}

export function isBackForwardRestore(event, performanceObj = globalThis.performance) {
    const type = navigationType(performanceObj);
    // Either browser signal is useful when no durable page marker is available.
    return type === 'back_forward' || Boolean(event?.persisted);
}

export function prepareCardsForPageHide({
    windowObj = globalThis.window,
    documentObj = globalThis.document,
} = {}) {
    if (!windowObj || !documentObj) return false;

    const visibleBackdrops = openBackdrops(documentObj);
    const cardActionPending = hasCardNavigationMarker(documentObj);
    if (!cardActionPending && visibleBackdrops.length === 0) return false;

    setCardNavigationMarker(documentObj);
    setStoredNavigationMarker(windowObj);

    // Hide before the browser snapshots/freezes the page. A reload on return is
    // still required because page-specific in-progress variables may be stale.
    for (const backdrop of visibleBackdrops) backdrop.hidden = true;
    return true;
}

export function repairOpenCardsAfterHistoryRestore({
    event,
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
} = {}) {
    if (!windowObj || !documentObj) return false;

    const restoredBackdrops = openBackdrops(documentObj);
    const marked = hasCardNavigationMarker(documentObj)
        || hasStoredNavigationMarker(windowObj);

    // A marker written during pagehide is direct evidence that this exact page
    // left while a card action was active. Trust it even if an embedded browser
    // reports both history signals incorrectly. Without a marker, only repair a
    // visibly restored card when the browser supplies a history-return signal.
    if (!marked && (
        restoredBackdrops.length === 0
        || !isBackForwardRestore(event, performanceObj)
    )) {
        return false;
    }

    clearCardNavigationMarker(documentObj);
    clearStoredNavigationMarker(windowObj);

    // Hide every card first so no stale overlay flashes while the fresh page loads.
    for (const backdrop of allBackdrops(documentObj)) backdrop.hidden = true;
    windowObj.location.reload();
    return true;
}

export function installHistoryCardRestoreGuard({
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
    MutationObserverClass = globalThis.MutationObserver,
} = {}) {
    if (!windowObj || !documentObj) return () => {};

    let clearTimer = null;
    const clearScheduledMarker = () => {
        if (clearTimer !== null) windowObj.clearTimeout(clearTimer);
        clearTimer = null;
    };
    const syncMarker = () => {
        if (openBackdrops(documentObj).length > 0) {
            clearScheduledMarker();
            setCardNavigationMarker(documentObj);
            return;
        }

        // A successful card action often hides the backdrop and assigns a new
        // location in the same task. Delay clearing so pagehide can persist the
        // marker. A normal manual close remains on-page long enough to clear it.
        clearScheduledMarker();
        clearTimer = windowObj.setTimeout(() => {
            clearTimer = null;
            if (openBackdrops(documentObj).length === 0) {
                clearCardNavigationMarker(documentObj);
            }
        }, MANUAL_CLOSE_GRACE_MILLISECONDS);
    };

    const pageshowHandler = (event) => repairOpenCardsAfterHistoryRestore({
        event,
        windowObj,
        documentObj,
        performanceObj,
    });
    const pagehideHandler = () => prepareCardsForPageHide({
        windowObj,
        documentObj,
    });
    windowObj.addEventListener('pageshow', pageshowHandler);
    windowObj.addEventListener('pagehide', pagehideHandler);

    let observer = null;
    const root = documentObj.body || documentObj.documentElement;
    if (root && typeof MutationObserverClass === 'function') {
        observer = new MutationObserverClass(syncMarker);
        observer.observe(root, {
            subtree: true,
            attributes: true,
            attributeFilter: ['hidden'],
        });
    }
    if (documentObj.querySelectorAll?.(BACKDROP_SELECTOR)?.length) syncMarker();

    return () => {
        clearScheduledMarker();
        observer?.disconnect();
        windowObj.removeEventListener('pageshow', pageshowHandler);
        windowObj.removeEventListener('pagehide', pagehideHandler);
    };
}

export function localDateTimeValue(nowMilliseconds = Date.now()) {
    const date = new Date(Number(nowMilliseconds));
    if (!Number.isFinite(date.getTime())) return '';
    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fillBlankStartInput(input, nowMilliseconds = Date.now()) {
    if (!input || String(input.value || '').trim()) return false;
    input.value = localDateTimeValue(nowMilliseconds);
    return Boolean(input.value);
}

export function installCreateSpotStartDefault({
    documentObj = globalThis.document,
    MutationObserverClass = globalThis.MutationObserver,
    now = () => Date.now(),
} = {}) {
    if (!documentObj) return () => {};

    const card = documentObj.getElementById('create-spot-card');
    const input = documentObj.getElementById('spot-starts-input');
    if (!card || !input) return () => {};

    const apply = () => {
        if (!card.hidden) fillBlankStartInput(input, now());
    };
    apply();

    if (typeof MutationObserverClass !== 'function') return () => {};
    const observer = new MutationObserverClass(apply);
    observer.observe(card, { attributes: true, attributeFilter: ['hidden'] });
    return () => observer.disconnect();
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    installHistoryCardRestoreGuard();
    installCreateSpotStartDefault();
}
