const originalFetch = window.fetch.bind(window);
const fundedDraftIds = new Set();

let spotDetailAuthPayload = null;
let spotDetailUser = null;
let spotDetailActionBusy = false;

function requestUrl(input) {
    try {
        return new URL(input instanceof Request ? input.url : String(input), window.location.origin);
    } catch (err) {
        return null;
    }
}

function requestMethod(input, init = {}) {
    return String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
}

function requestBodyJson(input, init = {}) {
    const body = init.body ?? (input instanceof Request ? null : null);
    if (typeof body !== 'string' || !body.trim()) return null;
    try {
        return JSON.parse(body);
    } catch (err) {
        return null;
    }
}

async function replaceJsonResponse(response, transform) {
    if (!response.ok) return response;
    const data = await response.clone().json().catch(() => null);
    if (!data || typeof data !== 'object') return response;

    const transformed = transform(data) || data;
    const headers = new Headers(response.headers);
    headers.delete('content-length');
    headers.set('content-type', 'application/json');
    return new Response(JSON.stringify(transformed), {
        status: response.status,
        statusText: response.statusText,
        headers,
    });
}

function spotIdFromCreateApi(url) {
    const match = url?.pathname?.match(/^\/api\/create-spot\/(\d+)$/);
    return match ? Number.parseInt(match[1], 10) : 0;
}

function spotDataFromPage() {
    const dataElement = document.getElementById('spot-data');
    if (!dataElement) return null;
    try {
        return JSON.parse(dataElement.textContent || '{}');
    } catch (err) {
        return null;
    }
}

function userOwnsSpot(user, spot) {
    const userId = Number(user?.id);
    const creatorId = Number(spot?.created_by);
    return Number.isFinite(userId) && Number.isFinite(creatorId) && userId === creatorId;
}

function spotCanBeCancelled(spot) {
    if (!spot || spot.is_prizedraw) return false;
    if (!['active', 'upcoming'].includes(String(spot.status_label || '').toLowerCase())) return false;

    const maximum = Number(spot.max_total_claims || 0);
    const successful = Number(spot.success_claim_count || 0);
    return maximum <= 0 || successful < maximum;
}

async function cancelSpotFromDetail(spot, button) {
    if (!spot?.id || !spotDetailAuthPayload || spotDetailActionBusy) return;
    const title = spot.title || 'this Spot';
    const confirmed = window.confirm(
        `Cancel '${title}'? Remaining refundable funds will be returned minus the cancellation fee.`
    );
    if (!confirmed) return;

    spotDetailActionBusy = true;
    button.disabled = true;
    button.textContent = 'Cancelling…';

    try {
        const response = await originalFetch(`/api/my-spots/${spot.id}/cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(spotDetailAuthPayload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || 'This Spot could not be cancelled.');
        }
        window.location.href = data.redirect_url || '/my-spots';
    } catch (err) {
        spotDetailActionBusy = false;
        button.disabled = false;
        button.textContent = 'Cancel';
        window.alert(err?.message || 'This Spot could not be cancelled.');
    }
}

function renderSpotDetailOwnerActions() {
    if (!window.location.pathname.startsWith('/spot/')) return;
    const spot = spotDataFromPage();
    if (!spot || !userOwnsSpot(spotDetailUser, spot) || !spotCanBeCancelled(spot)) return;

    const detail = document.querySelector('.spot-list-detail');
    if (!detail || detail.querySelector('[data-owner-detail-actions]')) return;

    const actions = document.createElement('div');
    actions.className = 'spot-owner-actions';
    actions.dataset.ownerDetailActions = 'true';

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'nq-button red spot-owner-action-button';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => cancelSpotFromDetail(spot, cancel));

    actions.append(cancel);
    const reportControl = detail.querySelector('.spot-report-line');
    if (reportControl) detail.insertBefore(actions, reportControl);
    else detail.append(actions);
}

function configureSpotBackLink() {
    if (!window.location.pathname.startsWith('/spot/')) return;
    const backLink = document.querySelector('.back-link');
    if (!backLink) return;

    const params = new URLSearchParams(window.location.search);
    const source = String(params.get('back') || params.get('from') || '').toLowerCase();
    let fromMySpots = source === 'my-spots';

    if (!fromMySpots && document.referrer) {
        try {
            const referrer = new URL(document.referrer);
            fromMySpots = referrer.origin === window.location.origin
                && (referrer.pathname === '/my-spots' || referrer.pathname.startsWith('/create/'));
        } catch (err) {
            fromMySpots = false;
        }
    }

    if (!fromMySpots) return;
    backLink.href = '/my-spots';
    backLink.setAttribute('aria-label', 'Back to My Spots');
    const arrow = backLink.querySelector('[aria-hidden="true"]')?.outerHTML || '<span aria-hidden="true">←</span>';
    backLink.innerHTML = `${arrow} My Spots`;
}

function stripPlaceFromMySpotsMeta(root = document) {
    if (window.location.pathname !== '/my-spots') return;
    for (const meta of root.querySelectorAll?.('.spot-list-meta') || []) {
        if (meta.dataset.placeRemoved === 'true') continue;
        const firstElement = Array.from(meta.childNodes).find(
            (node) => node.nodeType === Node.ELEMENT_NODE
        );

        if (firstElement) {
            for (const node of Array.from(meta.childNodes)) {
                if (node === firstElement) break;
                node.remove();
            }
        } else {
            meta.replaceChildren();
        }

        meta.dataset.placeRemoved = 'true';
        meta.hidden = !meta.textContent.trim();
    }
}

function updateFundedDraftDeleteCopy() {
    if (!window.location.pathname.startsWith('/create/')) return;
    const spotId = Number.parseInt(document.body.dataset.spotId || '0', 10);
    if (!fundedDraftIds.has(spotId)) return;

    const deleteButton = document.getElementById('create-spot-delete');
    if (deleteButton) deleteButton.hidden = false;

    const body = document.getElementById('delete-spot-body');
    if (body) {
        body.textContent = 'Cancel this draft and return its refundable balance to the funding wallet?';
    }
}

function installDomObservers() {
    configureSpotBackLink();
    stripPlaceFromMySpotsMeta();
    renderSpotDetailOwnerActions();
    updateFundedDraftDeleteCopy();

    const observer = new MutationObserver((records) => {
        for (const record of records) {
            for (const node of record.addedNodes) {
                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                stripPlaceFromMySpotsMeta(node);
            }
        }
        renderSpotDetailOwnerActions();
        updateFundedDraftDeleteCopy();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    document.addEventListener('click', (event) => {
        if (event.target?.closest?.('#create-spot-delete')) {
            window.setTimeout(updateFundedDraftDeleteCopy, 0);
        }
    }, true);
}

export function installOwnerUiPolish() {
    window.fetch = async (input, init = {}) => {
        const url = requestUrl(input);
        const method = requestMethod(input, init);
        const createSpotId = spotIdFromCreateApi(url);

        if (createSpotId && method === 'DELETE' && fundedDraftIds.has(createSpotId)) {
            return originalFetch(`/api/my-spots/${createSpotId}/cancel`, {
                ...init,
                method: 'POST',
            });
        }

        let response = await originalFetch(input, init);

        if (url?.pathname === '/api/my-spots' && method === 'POST') {
            response = await replaceJsonResponse(response, (data) => {
                for (const spot of data.spots || []) {
                    if (String(spot.status_label || '').toLowerCase() === 'draft') {
                        spot.can_cancel = false;
                    }
                }
                return data;
            });
        }

        if (createSpotId && !['PATCH', 'DELETE'].includes(method)) {
            response = await replaceJsonResponse(response, (data) => {
                if (data.spot?.deposit?.has_any) {
                    fundedDraftIds.add(createSpotId);
                    data.spot.can_delete = true;
                    window.setTimeout(updateFundedDraftDeleteCopy, 0);
                }
                return data;
            });
        }

        if (url?.pathname === '/api/home/session' && window.location.pathname.startsWith('/spot/')) {
            spotDetailAuthPayload = requestBodyJson(input, init);
            response.clone().json().then((data) => {
                spotDetailUser = data.user || null;
                renderSpotDetailOwnerActions();
            }).catch(() => {});
        }

        return response;
    };

    installDomObservers();
}
