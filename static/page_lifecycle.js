/*
 * Shared browser lifecycle repairs.
 *
 * Safari and WKWebView may restore a page from the back-forward cache with its
 * previous DOM and JavaScript memory intact. If a modal initiated navigation,
 * that can revive both the card and page-specific "in progress" flags. Track a
 * recently active card in the cached DOM, then reload only when browser history
 * restores that marked page. Ordinary initial loads and normal Back navigation
 * from pages that had no active card remain untouched.
 */

const BACKDROP_SELECTOR = '.notice-backdrop';
const OPEN_BACKDROP_SELECTOR = '.notice-backdrop:not([hidden])';
const CARD_NAVIGATION_MARKER = 'data-nimhunt-card-navigation-pending';
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

function openBackdrops(documentObj) {
    return [...(documentObj?.querySelectorAll?.(OPEN_BACKDROP_SELECTOR) || [])];
}

export function isBackForwardRestore(event, performanceObj = globalThis.performance) {
    if (!event?.persisted) return false;
    const type = navigationType(performanceObj);
    // `persisted` is the strongest signal available in older iOS WebViews.
    return type === null || type === 'back_forward';
}

export function repairOpenCardsAfterHistoryRestore({
    event,
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
} = {}) {
    if (!windowObj || !documentObj || !isBackForwardRestore(event, performanceObj)) {
        return false;
    }

    const restoredBackdrops = openBackdrops(documentObj);
    if (!hasCardNavigationMarker(documentObj) && restoredBackdrops.length === 0) {
        return false;
    }

    clearCardNavigationMarker(documentObj);
    // Hide first so the stale card does not flash while the fresh page loads.
    for (const backdrop of restoredBackdrops) backdrop.hidden = true;
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
        // location in the same task. Delay clearing so that navigation preserves
        // the marker in the old history entry. A normal manual close remains on
        // the page long enough for this timer to remove it.
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
    windowObj.addEventListener('pageshow', pageshowHandler);

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
