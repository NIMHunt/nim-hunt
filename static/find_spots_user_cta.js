function identifiedUser(runtime) {
    return runtime?.walletUserId !== null && runtime?.walletUserId !== undefined;
}

function requestUrl(input, origin) {
    const raw = typeof input === 'string' || input instanceof URL ? input : input?.url;
    try {
        return raw ? new URL(raw, origin) : null;
    } catch (_err) {
        return null;
    }
}

function requestBodyJson(options) {
    try {
        return options?.body ? JSON.parse(options.body) : {};
    } catch (_err) {
        return {};
    }
}

function emptyState(runtime) {
    return runtime?.document?.getElementById?.('empty-spots') || null;
}

function emptyLinks(runtime) {
    const empty = emptyState(runtime);
    return empty?.querySelectorAll ? [...empty.querySelectorAll('a.welcome-link')] : [];
}

function lineForLink(link) {
    return link?.closest?.('span') || link?.parentElement || null;
}

function linkByText(runtime, text) {
    const wanted = String(text || '').trim().toLowerCase();
    return emptyLinks(runtime).find(
        (link) => String(link?.textContent || '').trim().toLowerCase() === wanted,
    ) || null;
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

function createSpotLink(runtime) {
    const origin = runtime?.window?.location?.origin || 'http://localhost';
    const expectedPath = createSpotPath(runtime);
    for (const link of emptyLinks(runtime)) {
        if (link?.dataset?.nimHuntCreateSpot === '1') return link;
        const rawHref = link.getAttribute?.('href') || link.href || '';
        let path = rawHref;
        try {
            path = new URL(rawHref, origin).pathname;
        } catch (_err) {}
        if (path === expectedPath) return link;
    }
    return null;
}

function replaceLine(runtime, line, link, beforeText, afterText, copyKey) {
    if (!line || !link || !line.replaceChildren) return false;
    if (line.dataset?.nimHuntEmptyCopy === copyKey) return false;

    line.replaceChildren(
        runtime.document.createTextNode(beforeText),
        link,
        runtime.document.createTextNode(afterText),
    );
    if (line.dataset) line.dataset.nimHuntEmptyCopy = copyKey;
    return true;
}

function normaliseGlobalLine(runtime, line, link) {
    if (!line || !link) return;
    replaceLine(runtime, line, link, 'Would you like to ', '', 'global-v1');
}

function normaliseCreateLine(runtime, line, link) {
    if (!line || !link) return;
    // MutationObserver watches child-list changes throughout #empty-spots.
    // Reassigning textContent even to the same string rebuilds the text node,
    // which would trigger the observer again indefinitely. Only write when the
    // visible copy actually needs changing.
    if (String(link.textContent || '') !== 'make a spot') {
        link.textContent = 'make a spot';
    }
    if (link.dataset && link.dataset.nimHuntCreateSpot !== '1') {
        link.dataset.nimHuntCreateSpot = '1';
    }
    replaceLine(runtime, line, link, 'Be the first to ', ' here.', 'create-v2');
}

function demoCandidate(runtime, demoLine) {
    return Boolean(
        demoLine
        && identifiedUser(runtime)
        && runtime?.userLocation
        && !runtime?.demoSpot
        && !runtime?.completed
    );
}

export function syncFindSpotsEmptyChoices(runtime) {
    const demoLink = linkByText(runtime, 'try a Demo Spot?');
    const globalLink = linkByText(runtime, 'check out global spots?');
    const createLink = createSpotLink(runtime);
    const demoLine = lineForLink(demoLink);
    const globalLine = lineForLink(globalLink);
    const createLine = lineForLink(createLink);

    normaliseCreateLine(runtime, createLine, createLink);

    const canConsiderDemo = demoCandidate(runtime, demoLine);
    const waitingForHistory = canConsiderDemo && runtime?.claimHistoryKnown !== true;
    const showDemo = canConsiderDemo
        && runtime?.claimHistoryKnown === true
        && runtime?.hasExistingClaims === false;

    if (demoLine) demoLine.hidden = !showDemo;
    if (globalLine) {
        globalLine.hidden = waitingForHistory || showDemo;
        if (!globalLine.hidden) normaliseGlobalLine(runtime, globalLine, globalLink);
    }
    if (createLine) {
        createLine.hidden = waitingForHistory || showDemo || !identifiedUser(runtime);
    }

    return {
        demoVisible: Boolean(demoLine && !demoLine.hidden),
        globalVisible: Boolean(globalLine && !globalLine.hidden),
        createVisible: Boolean(createLine && !createLine.hidden),
        waitingForHistory,
    };
}

// Backwards-compatible name retained for the focused tests and any cached caller.
export function syncFindSpotsCreateCta(runtime) {
    return syncFindSpotsEmptyChoices(runtime).createVisible;
}

async function loadClaimHistoryFlag(runtime, fetchImpl) {
    if (!runtime || runtime.claimHistoryKnown || runtime.claimHistoryInFlight) return false;
    if (!identifiedUser(runtime)
        || !runtime.userLocation
        || runtime.demoSpot
        || runtime.completed
        || runtime.lastRealSpotCount !== 0) {
        return false;
    }

    const sessionPayload = runtime.lastSessionPayload;
    if (!sessionPayload?.device_id_hash || typeof fetchImpl !== 'function') return false;

    runtime.claimHistoryInFlight = true;
    syncFindSpotsEmptyChoices(runtime);
    try {
        const response = await fetchImpl('/api/my-claims?limit=1', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(sessionPayload),
        });
        const data = await response.json().catch(() => null);
        runtime.hasExistingClaims = Boolean(
            !response.ok
            || data?.ok === false
            || !Array.isArray(data?.claims)
            || data.claims.length > 0
        );
    } catch (_err) {
        // Fail closed: the Demo is only for users we can verify have no real claims.
        runtime.hasExistingClaims = true;
    } finally {
        runtime.claimHistoryKnown = true;
        runtime.claimHistoryInFlight = false;
        syncFindSpotsEmptyChoices(runtime);
    }
    return true;
}

export function installFindSpotsCreateCtaGuard(
    runtime,
    { MutationObserverCtor = globalThis.MutationObserver } = {},
) {
    const empty = emptyState(runtime);
    if (!runtime || !empty || runtime.__nimHuntCreateCtaGuard) {
        return runtime?.__nimHuntCreateCtaGuard || null;
    }

    runtime.claimHistoryKnown = Boolean(runtime.claimHistoryKnown);
    runtime.claimHistoryInFlight = false;
    runtime.hasExistingClaims = runtime.claimHistoryKnown
        ? Boolean(runtime.hasExistingClaims)
        : null;
    runtime.lastSessionPayload = runtime.lastSessionPayload || null;

    const previousFetch = typeof runtime.window?.fetch === 'function'
        ? runtime.window.fetch.bind(runtime.window)
        : null;

    const scheduleClaimHistoryCheck = () => {
        if (!previousFetch) return;
        const queue = runtime.window?.queueMicrotask || ((callback) => Promise.resolve().then(callback));
        queue(() => { void loadClaimHistoryFlag(runtime, previousFetch); });
    };

    if (previousFetch) {
        runtime.window.fetch = async (input, options = {}) => {
            const url = requestUrl(input, runtime.window.location?.origin || 'http://localhost');
            if (url?.pathname === '/api/home/session') {
                runtime.lastSessionPayload = requestBodyJson(options);
            }

            const response = await previousFetch(input, options);
            if (url?.pathname === '/api/home/session' || url?.pathname === '/api/spots/search') {
                scheduleClaimHistoryCheck();
                const queue = runtime.window?.queueMicrotask || ((callback) => Promise.resolve().then(callback));
                queue(() => syncFindSpotsEmptyChoices(runtime));
            }
            return response;
        };
    }

    // Find Spots resolves Nimiq Pay identity asynchronously. Wrap the existing
    // runtime field so the empty-state choices update as soon as identity changes.
    let walletUserId = runtime.walletUserId;
    Object.defineProperty(runtime, 'walletUserId', {
        configurable: true,
        enumerable: true,
        get() {
            return walletUserId;
        },
        set(value) {
            const changedUser = value !== walletUserId;
            walletUserId = value;
            if (changedUser) {
                runtime.claimHistoryKnown = false;
                runtime.claimHistoryInFlight = false;
                runtime.hasExistingClaims = null;
            }
            syncFindSpotsEmptyChoices(runtime);
            scheduleClaimHistoryCheck();
        },
    });

    const observer = MutationObserverCtor
        ? new MutationObserverCtor(() => {
            syncFindSpotsEmptyChoices(runtime);
            scheduleClaimHistoryCheck();
        })
        : null;
    observer?.observe(empty, { childList: true, subtree: true });

    syncFindSpotsEmptyChoices(runtime);
    scheduleClaimHistoryCheck();

    runtime.__nimHuntCreateCtaGuard = observer || { disconnect() {} };
    return runtime.__nimHuntCreateCtaGuard;
}
