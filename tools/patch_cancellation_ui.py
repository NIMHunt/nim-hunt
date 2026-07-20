from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "public_html.py",
    '_ASSET_VERSION = "polish-live-status-v1-20260720"',
    '_ASSET_VERSION = "cancellation-safety-v1-20260720"',
)
replace_once(
    "public_html.py",
    dedent('''\
    def _serialise_public_spot_for_detail(spot: dict[str, Any], *, now: int) -> dict[str, Any]:
        """Shape one public SPOT for the standalone Spot detail page."""
        link = spot.get(schema.SPOT_LINK)
        spot_id = int(spot[schema.SPOT_ID])

        return {
    '''),
    dedent('''\
    def _serialise_public_spot_for_detail(spot: dict[str, Any], *, now: int) -> dict[str, Any]:
        """Shape one public SPOT for the standalone Spot detail page."""
        link = spot.get(schema.SPOT_LINK)
        spot_id = int(spot[schema.SPOT_ID])
        is_prizedraw = _spot_is_prizedraw_row(spot)
        cancellation_started = spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
        effectively_complete = _owner_spot_effectively_complete(spot)

        return {
    '''),
)
replace_once(
    "public_html.py",
    '        "status_label": _spot_status_label(spot, now=now),\n        "is_prizedraw": _spot_is_prizedraw_row(spot),\n        "prize_count": spot.get(schema.PRIZEDRAW_PRIZE_COUNT),\n',
    '        "status_label": _spot_status_label(spot, now=now),\n        "is_prizedraw": is_prizedraw,\n        "prize_count": spot.get(schema.PRIZEDRAW_PRIZE_COUNT),\n',
)
replace_once(
    "public_html.py",
    dedent('''\
            "creator_display_name": spot.get("creator_display_name"),
            "distance_m": None,
            "href": f"{const.SPOT_PAGE_URL_PREFIX}/{link or spot_id}",
    '''),
    dedent('''\
            "creator_display_name": spot.get("creator_display_name"),
            "distance_m": None,
            "cancellation_started": cancellation_started,
            "can_cancel": (
                int(spot.get(schema.SPOT_STATUS) or -1) == const.SPOT_STATUS_PUBLISHED
                and not is_prizedraw
                and not effectively_complete
                and not cancellation_started
            ),
            "href": f"{const.SPOT_PAGE_URL_PREFIX}/{link or spot_id}",
    '''),
)
replace_once(
    "public_html.py",
    dedent('''\
            "can_delete": (
                status_label == "draft"
                and not cancellation_started
                and not bool(deposit.get("has_any"))
            ),
    '''),
    dedent('''\
            "can_delete": status_label == "draft" and not cancellation_started,
    '''),
)
replace_once(
    "public_html.py",
    dedent('''\
        spot = _serialise_public_spot_for_detail(spot_row, now=now)
        source = str(request.query_params.get("from") or "").strip().lower()
        if source == "my-spots":
    '''),
    dedent('''\
        spot = _serialise_public_spot_for_detail(spot_row, now=now)
        source = str(
            request.query_params.get("back")
            or request.query_params.get("from")
            or ""
        ).strip().lower()
        if not source:
            referer = str(request.headers.get("referer") or "")
            referer_path = urllib.parse.urlparse(referer).path
            if referer_path == "/my-spots" or referer_path.startswith("/create/"):
                source = "my-spots"
        if source == "my-spots":
    '''),
)
replace_once(
    "templates/spot.html",
    '            <a class="back-link" href="/spots" aria-label="Back to Find Spots"><span aria-hidden="true">←</span> Find Spots</a>',
    '            <a class="back-link" href="{{ back_href }}" aria-label="{{ back_aria_label }}"><span aria-hidden="true">←</span> {{ back_label }}</a>',
)
replace_once(
    "static/my_spots.js",
    dedent('''\
    function buildMySpotMeta(spot) {
        const fragment = document.createDocumentFragment();
        fragment.append(document.createTextNode(spotPlaceText(spot)));

        if (spot.status_label === 'draft') {
            if (['depositing', 'deposited'].includes(spot.badge_status_label)) return fragment;
            fragment.append(document.createTextNode(' - '));
            const notice = document.createElement('span');
            notice.className = `spot-list-meta-notice ${draftDepositClass(spot)}`;
            notice.textContent = draftDepositText(spot);
            fragment.append(notice);
            return fragment;
        }

        fragment.append(document.createTextNode(' - '), scheduleNode(spot));
        return fragment;
    }
    '''),
    dedent('''\
    function buildMySpotMeta(spot) {
        const fragment = document.createDocumentFragment();
        fragment.append(scheduleNode(spot));

        if (spot.status_label === 'draft'
            && !['depositing', 'deposited'].includes(spot.badge_status_label)) {
            fragment.append(document.createTextNode(' - '));
            const notice = document.createElement('span');
            notice.className = `spot-list-meta-notice ${draftDepositClass(spot)}`;
            notice.textContent = draftDepositText(spot);
            fragment.append(notice);
        }

        return fragment;
    }
    '''),
)
replace_once(
    "static/my_spots.js",
    "    if (spot.can_cancel) {\n",
    "    if (spot.can_cancel && spot.status_label !== 'draft') {\n",
)
replace_once(
    "static/my_spots.js",
    "    nextUrl.searchParams.set('from', 'my-spots');\n",
    "    nextUrl.searchParams.set('back', 'my-spots');\n    nextUrl.searchParams.set('from', 'my-spots');\n",
)
replace_once(
    "static/my_spots.js",
    "    nextUrl.searchParams.set('from', 'my-spots');\n    nextUrl.searchParams.set('published', '1');\n",
    "    nextUrl.searchParams.set('back', 'my-spots');\n    nextUrl.searchParams.set('from', 'my-spots');\n    nextUrl.searchParams.set('published', '1');\n",
)
replace_once(
    "static/create_spot.js",
    dedent('''\
        try {
            const data = await fetchJson(`/api/create-spot/${SPOT_ID}`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(authPayload()),
            });
            window.location.href = data.redirect_url || '/my-spots';
    '''),
    dedent('''\
        try {
            const hasDepositHistory = Boolean(state.spot?.deposit?.has_any);
            const data = await fetchJson(
                hasDepositHistory
                    ? `/api/my-spots/${SPOT_ID}/cancel`
                    : `/api/create-spot/${SPOT_ID}`,
                {
                    method: hasDepositHistory ? 'POST' : 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(authPayload()),
                },
            );
            window.location.href = data.redirect_url || '/my-spots';
    '''),
)
replace_once(
    "static/create_spot.js",
    dedent('''\
    function openDeleteConfirmation() {
        if (!els.deleteBackdrop || state.deleteInProgress) return;
        const title = els.title?.value?.trim() || state.spot?.title || 'this draft';
        if (els.deleteBody) els.deleteBody.textContent = TEXT.notices.deleteConfirm.body(title);
    '''),
    dedent('''\
    function openDeleteConfirmation() {
        if (!els.deleteBackdrop || state.deleteInProgress) return;
        const title = els.title?.value?.trim() || state.spot?.title || 'this draft';
        if (els.deleteBody) {
            els.deleteBody.textContent = state.spot?.deposit?.has_any
                ? `Cancel '${title}' and return its refundable balance to the funding wallet?`
                : TEXT.notices.deleteConfirm.body(title);
        }
    '''),
)
replace_once(
    "static/spot_detail.js",
    "    statusTimerId: null,\n",
    "    statusTimerId: null,\n    ownerActions: null,\n    ownerActionInProgress: false,\n",
)
replace_once(
    "static/spot_detail.js",
    dedent('''\
            updateReportControlVisibility();
            updateReportConfirmState();
            maybeLoadOwnerClaimCodes();
    '''),
    dedent('''\
            updateReportControlVisibility();
            updateReportConfirmState();
            updateOwnerActions();
            maybeLoadOwnerClaimCodes();
    '''),
)
replace_once(
    "static/spot_detail.js",
    dedent('''\
    function updateReportControlVisibility() {
        for (const { line, spot } of state.reportControls) {
            line.hidden = !state.reportIdentityReady || currentUserOwnsSpot(spot);
        }
    }

    function authPayload() {
    '''),
    dedent('''\
    function updateReportControlVisibility() {
        for (const { line, spot } of state.reportControls) {
            line.hidden = !state.reportIdentityReady || currentUserOwnsSpot(spot);
        }
    }

    async function cancelOwnedSpot(spot, button) {
        if (!spot?.id || state.ownerActionInProgress) return;
        const title = spot.title || 'this Spot';
        if (!window.confirm(
            `Cancel '${title}'? Remaining refundable funds will be returned minus the cancellation fee.`
        )) return;

        state.ownerActionInProgress = true;
        button.disabled = true;
        button.textContent = 'Cancelling…';
        try {
            await fetchJson(`/api/my-spots/${spot.id}/cancel`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(authPayload()),
            });
            window.location.href = '/my-spots';
        } catch (err) {
            state.ownerActionInProgress = false;
            button.disabled = false;
            button.textContent = 'Cancel';
            showNotice({
                title: 'Cancellation failed',
                body: err?.message || 'This Spot could not be cancelled.',
            });
        }
    }

    function updateOwnerActions() {
        const actions = state.ownerActions;
        const spot = state.spot;
        if (!actions || !spot) return;
        actions.replaceChildren();
        actions.hidden = true;
        if (!state.reportIdentityReady || !currentUserOwnsSpot(spot) || !spot.can_cancel) return;

        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'nq-button red spot-owner-action-button';
        cancel.textContent = 'Cancel';
        cancel.addEventListener('click', () => cancelOwnedSpot(spot, cancel));
        actions.append(cancel);
        actions.hidden = false;
    }

    function authPayload() {
    '''),
)
replace_once(
    "static/spot_detail.js",
    dedent('''\
        lines.append(buildOwnerClaimCodesLine());
        detail.append(lines);
        detail.append(buildReportControl(spot));

        return { detail, map };
    '''),
    dedent('''\
        lines.append(buildOwnerClaimCodesLine());
        detail.append(lines);

        const ownerActions = document.createElement('div');
        ownerActions.className = 'spot-owner-actions';
        ownerActions.hidden = true;
        state.ownerActions = ownerActions;
        detail.append(ownerActions);
        detail.append(buildReportControl(spot));

        return { detail, map };
    '''),
)
replace_once(
    "static/spot_detail.js",
    dedent('''\
        state.claimCodesLoaded = false;
        state.claimCodesLoading = false;

        const item = document.createElement('li');
    '''),
    dedent('''\
        state.claimCodesLoaded = false;
        state.claimCodesLoading = false;
        state.ownerActions = null;
        state.ownerActionInProgress = false;

        const item = document.createElement('li');
    '''),
)

print("Applied owner UI patch.")
