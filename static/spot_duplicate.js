const CREATE_DRAFT_PATH = '/api/create-spot/draft';
const DUPLICATE_BUTTON_CLASS = 'spot-duplicate-button';

export function duplicateSpotEndpoint(sourceSpotId) {
    const spotId = Number(sourceSpotId);
    if (!Number.isSafeInteger(spotId) || spotId <= 0) {
        throw new TypeError('A positive source Spot id is required.');
    }
    return `/api/my-spots/${spotId}/duplicate`;
}

function requestUrl(input, baseUrl) {
    const value = typeof input === 'string' ? input : input?.url;
    if (!value) return null;
    try {
        return new URL(value, baseUrl);
    } catch (_err) {
        return null;
    }
}

function requestMethod(input, init) {
    return String(init?.method || input?.method || 'GET').toUpperCase();
}

export function requestTargetsOrdinaryDraftCreation(input, init, baseUrl) {
    const url = requestUrl(input, baseUrl);
    return Boolean(
        url
        && url.pathname === CREATE_DRAFT_PATH
        && requestMethod(input, init) === 'POST'
    );
}

export function createDuplicateAwareFetch(
    originalFetch,
    {
        sourceSpotId = () => null,
        baseUrl = 'https://nimhunt.invalid',
        onSuccess = () => {},
    } = {},
) {
    if (typeof originalFetch !== 'function') {
        throw new TypeError('A fetch function is required.');
    }

    return async function duplicateAwareFetch(input, init) {
        const sourceId = Number(sourceSpotId());
        if (
            Number.isSafeInteger(sourceId)
            && sourceId > 0
            && requestTargetsOrdinaryDraftCreation(input, init, baseUrl)
        ) {
            const response = await originalFetch(
                duplicateSpotEndpoint(sourceId),
                init,
            );
            if (response?.ok) onSuccess();
            return response;
        }
        return originalFetch(input, init);
    };
}

export function spotFromListItem(item) {
    const raw = item?.dataset?.renderSignature;
    if (!raw) return null;
    try {
        const spot = JSON.parse(raw);
        const id = Number(spot?.id);
        return Number.isSafeInteger(id) && id > 0 ? spot : null;
    } catch (_err) {
        return null;
    }
}

export function installSpotDuplication({ windowObj = window, documentObj = document } = {}) {
    const createOpen = documentObj.getElementById('create-spot-open');
    const createBackdrop = documentObj.getElementById('create-spot-backdrop');
    const titleInput = documentObj.getElementById('create-spot-title-input');
    const standardInput = documentObj.getElementById('create-spot-type-standard');
    const prizedrawInput = documentObj.getElementById('create-spot-type-prizedraw');
    const cancelButton = documentObj.getElementById('create-spot-cancel');
    const sections = documentObj.getElementById('my-spots-sections');
    if (!createOpen || !createBackdrop || !titleInput || !sections) return () => {};

    let selectedSpot = null;
    let openingDuplicate = false;

    function setTypeInputsDisabled(disabled) {
        if (standardInput) standardInput.disabled = Boolean(disabled);
        if (prizedrawInput) prizedrawInput.disabled = Boolean(disabled);
    }

    function clearSelection() {
        selectedSpot = null;
        setTypeInputsDisabled(false);
    }

    const originalFetch = windowObj.fetch.bind(windowObj);
    windowObj.fetch = createDuplicateAwareFetch(originalFetch, {
        sourceSpotId: () => selectedSpot?.id,
        baseUrl: windowObj.location.origin,
        onSuccess: clearSelection,
    });

    function openDuplicateModal(spot) {
        selectedSpot = spot;
        openingDuplicate = true;
        createOpen.click();
        openingDuplicate = false;

        if (createBackdrop.hidden) {
            clearSelection();
            return;
        }

        titleInput.value = String(spot.title || '');
        if (standardInput) standardInput.checked = !Boolean(spot.is_prizedraw);
        if (prizedrawInput) prizedrawInput.checked = Boolean(spot.is_prizedraw);
        setTypeInputsDisabled(true);
        titleInput.dispatchEvent(new Event('input', { bubbles: true }));
        titleInput.focus();
    }

    function duplicateButton(spot) {
        const button = documentObj.createElement('button');
        button.type = 'button';
        button.className = (
            `nq-button light-blue spot-owner-action-button ${DUPLICATE_BUTTON_CLASS}`
        );
        button.textContent = 'Duplicate';
        button.setAttribute(
            'aria-label',
            `Duplicate ${String(spot.title || 'Spot')}`,
        );
        button.addEventListener('click', () => openDuplicateModal(spot));
        return button;
    }

    function enhanceItem(item) {
        if (!item || item.querySelector(`.${DUPLICATE_BUTTON_CLASS}`)) return;
        const spot = spotFromListItem(item);
        const detail = item.querySelector('.spot-list-detail');
        if (!spot || !detail) return;

        let actions = detail.querySelector('.spot-owner-actions');
        if (!actions) {
            actions = documentObj.createElement('div');
            actions.className = 'spot-owner-actions';
            detail.append(actions);
        }
        actions.append(duplicateButton(spot));
    }

    function enhanceAll() {
        for (const item of sections.querySelectorAll('.spot-list-item')) {
            enhanceItem(item);
        }
    }

    createOpen.addEventListener('click', () => {
        if (!openingDuplicate) clearSelection();
    }, true);
    cancelButton?.addEventListener('click', clearSelection);

    const observer = new MutationObserver(enhanceAll);
    observer.observe(sections, { childList: true, subtree: true });
    enhanceAll();

    return () => {
        observer.disconnect();
        windowObj.fetch = originalFetch;
        clearSelection();
    };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    installSpotDuplication();
}
