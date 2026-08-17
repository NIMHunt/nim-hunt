function identifiedUser(runtime) {
    return runtime?.walletUserId !== null && runtime?.walletUserId !== undefined;
}

function createSpotPath(runtime) {
    const origin = runtime?.window?.location?.origin || 'http://localhost';
    const configured = runtime?.document?.body?.dataset?.createSpotUrl || '/create';
    try {
        return new URL(configured, origin).pathname;
    } catch (_err) {
        return '/create';
    }
}

function findCreateSpotLine(runtime) {
    const empty = runtime?.document?.getElementById?.('empty-spots');
    if (!empty?.querySelectorAll) return null;

    const origin = runtime?.window?.location?.origin || 'http://localhost';
    const expectedPath = createSpotPath(runtime);
    for (const link of empty.querySelectorAll('a.welcome-link')) {
        const rawHref = link.getAttribute?.('href') || link.href || '';
        let path = rawHref;
        try {
            path = new URL(rawHref, origin).pathname;
        } catch (_err) {}
        if (path !== expectedPath) continue;
        if (String(link.textContent || '').trim().toLowerCase() !== 'make one') continue;
        return link.closest?.('span') || link.parentElement || null;
    }
    return null;
}

export function syncFindSpotsCreateCta(runtime) {
    const line = findCreateSpotLine(runtime);
    if (!line) return false;
    const visible = identifiedUser(runtime);
    line.hidden = !visible;
    return visible;
}

export function installFindSpotsCreateCtaGuard(
    runtime,
    { MutationObserverCtor = globalThis.MutationObserver } = {},
) {
    const empty = runtime?.document?.getElementById?.('empty-spots');
    if (!runtime || !empty || runtime.__nimHuntCreateCtaGuard) return runtime?.__nimHuntCreateCtaGuard || null;

    // Find Spots resolves Nimiq Pay identity asynchronously. Wrap the existing
    // plain runtime field so the CTA updates even when identity changes without
    // causing the empty-state renderer itself to replace any DOM.
    let walletUserId = runtime.walletUserId;
    Object.defineProperty(runtime, 'walletUserId', {
        configurable: true,
        enumerable: true,
        get() {
            return walletUserId;
        },
        set(value) {
            walletUserId = value;
            syncFindSpotsCreateCta(runtime);
        },
    });

    const observer = MutationObserverCtor
        ? new MutationObserverCtor(() => syncFindSpotsCreateCta(runtime))
        : null;
    observer?.observe(empty, { childList: true, subtree: true });
    syncFindSpotsCreateCta(runtime);

    runtime.__nimHuntCreateCtaGuard = observer || { disconnect() {} };
    return runtime.__nimHuntCreateCtaGuard;
}
