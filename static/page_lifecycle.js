/*
 * Shared browser lifecycle repairs.
 *
 * Safari and WKWebView may restore a page from the back-forward cache with its
 * previous DOM and JavaScript memory intact. If a modal initiated navigation,
 * that can revive both the visible card and page-specific "in progress" flags.
 * Reload only when a history traversal restores an actually open backdrop; an
 * ordinary initial load and deliberately open server-rendered cards are left
 * untouched.
 */

const OPEN_BACKDROP_SELECTOR = '.notice-backdrop:not([hidden])';

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

    const openBackdrops = [...documentObj.querySelectorAll(OPEN_BACKDROP_SELECTOR)];
    if (openBackdrops.length === 0) return false;

    // Hide first so the stale card does not flash while the fresh page loads.
    for (const backdrop of openBackdrops) backdrop.hidden = true;
    windowObj.location.reload();
    return true;
}

export function installHistoryCardRestoreGuard({
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
} = {}) {
    if (!windowObj || !documentObj) return () => {};

    const handler = (event) => repairOpenCardsAfterHistoryRestore({
        event,
        windowObj,
        documentObj,
        performanceObj,
    });
    windowObj.addEventListener('pageshow', handler);
    return () => windowObj.removeEventListener('pageshow', handler);
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
