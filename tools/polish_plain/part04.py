        for (const item of desired) list.append(item);
        for (const stale of existing.values()) stale.remove();
        list.hidden = !state.sectionExpanded[bucket];
        empty.hidden = !state.sectionExpanded[bucket] || spots.length > 0;
    }

    function renderSpots(spots) {
        els.empty.hidden = true;

        if (spots.length === 0) {
            els.sections.replaceChildren();
            renderEmptyAll();
            return;
        }

        const groups = groupSpots(spots);
        const buckets = ['active', 'upcoming', 'draft', 'previous'];
        const hasCompleteStructure = buckets.every(
            (bucket) => els.sections.querySelector(`[data-bucket="${bucket}"]`),
        );
        if (!hasCompleteStructure) {
            els.sections.replaceChildren(
                ...buckets.map((bucket) => renderSection(bucket, groups[bucket])),
            );
            return;
        }

        for (const bucket of buckets) reconcileSection(bucket, groups[bucket]);
    }
    """),
)


# ---------------------------------------------------------------------------
# Find Spots: no redundant City line when distance exists; keep owner code
# disclosure state through the page's own live list redraws.
# ---------------------------------------------------------------------------
replace_once(
    "static/find_spots.js",
    "    expandedSpotIds: new Set(),\n",
    "    expandedSpotIds: new Set(),\n    expandedClaimCodeSpotIds: new Set(),\n",
)
replace_once(
    "static/find_spots.js",
    "} from './spot_ui.js?v=qol-v1-20260717';",
    "} from './spot_ui.js?v=polish-live-v1-20260720';",
)
replace_once(
    "static/find_spots.js",
    dedent("""\
    function buildOwnerClaimCodesLineForSpot(spot) {
        const control = createOwnerClaimCodesControl();
        const entry = { spot, control, loaded: false, loading: false };
    """),
    dedent("""\
    function buildOwnerClaimCodesLineForSpot(spot) {
        const spotId = Number(spot.id);
        const control = createOwnerClaimCodesControl({}, {
            expanded: state.expandedClaimCodeSpotIds.has(spotId),
            onToggle: (expanded) => {
                if (expanded) state.expandedClaimCodeSpotIds.add(spotId);
                else state.expandedClaimCodeSpotIds.delete(spotId);
            },
        });
        const entry = { spot, control, loaded: false, loading: false };
    """),
)
replace_once(
    "static/find_spots.js",
    dedent("""\
        if (spot.distance_m !== null && spot.distance_m !== undefined) {
            const place = document.createElement('p');
            place.className = 'spot-detail-place';
            place.textContent = spotPlaceText(spot);
            detail.append(place);
        }
    """),
    "",
)


# ---------------------------------------------------------------------------
# Claim Detail: duration claims do not poll/rebuild every five seconds before
# their deadline. At the deadline the local timer says Verifying; unchanged
# server responses no longer rebuild the page while verification is pending.
# ---------------------------------------------------------------------------
replace_once(
    "static/claim_detail.js",
    "} from './spot_ui.js?v=qol-v1-20260717';",
    "} from './spot_ui.js?v=polish-live-v1-20260720';",
)
replace_once(
    "static/claim_detail.js",
    "} from './browser_utils.js?v=qol-v1-20260717';",
    "} from './browser_utils.js?v=polish-live-v1-20260720';",
)
insert_before_once(
    "static/claim_detail.js",
    "function createDurationTimerText(claim) {\n",
    dedent("""\
    function durationGoalReached(claim, now = Math.floor(Date.now() / 1000)) {
        const required = Math.max(0, Number(claim?.duration_required || 0));
        const claimedAt = Math.max(0, Number(claim?.claimed_at || 0));
        return required > 0 && claimedAt > 0 && now >= claimedAt + required;
    }

    function claimPresentationSignature(claim) {
        const spot = claim?.spot || {};
        return JSON.stringify([
            claim?.status_label,
            claim?.display_status_label,
            claim?.display_status_text,
            claim?.display_status_class,
            Boolean(claim?.location_monitoring_required),
            Boolean(claim?.viewer_is_recipient),
            Number(spot.success_claim_count || 0),
            Number(spot.pending_claim_count || 0),
        ]);
    }

    function applyClaimUpdate(claim, { forceRender = false } = {}) {
        if (
            forceRender
            || !state.currentClaim
            || claimPresentationSignature(state.currentClaim) !== claimPresentationSignature(claim)
        ) {
            renderClaim(claim);
            return;
        }

        state.currentClaim = claim;
        syncHeartbeat(claim);
        syncStatusPolling(claim);
    }


    """),
)
replace_once(
    "static/claim_detail.js",
    dedent("""\
            const cappedElapsed = Math.min(elapsed, required);
            span.textContent = ` (${formatSeconds(cappedElapsed)}/${formatSeconds(required)})`;

            if (required > 0 && cappedElapsed >= required && span._nhTimerId) {
                window.clearInterval(span._nhTimerId);
                span._nhTimerId = null;
                handleDurationGoalReached();
            }
    """),
    dedent("""\
            const cappedElapsed = Math.min(elapsed, required);
            const reachedGoal = required > 0 && cappedElapsed >= required;
            span.textContent = reachedGoal
                ? ' (Verifying)'
                : ` (${formatSeconds(cappedElapsed)}/${formatSeconds(required)})`;

            if (reachedGoal && span._nhTimerId) {
                window.clearInterval(span._nhTimerId);
                span._nhTimerId = null;
                handleDurationGoalReached();
            }
    """),
)
replace_once(
    "static/claim_detail.js",
    dedent("""\
    async function fetchClaimDetail() {
        const data = await fetchJson(`/api/claim/${CLAIM_ID}/detail`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });
        state.user = data.user || null;
        renderClaim(data.claim);
        return data.claim;
    }
    """),
    dedent("""\
    async function fetchClaimDetail({ forceRender = false } = {}) {
        const data = await fetchJson(`/api/claim/${CLAIM_ID}/detail`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authPayload()),
        });
        state.user = data.user || null;
        applyClaimUpdate(data.claim, { forceRender });
        return data.claim;
    }
    """),
)
replace_once(
    "static/claim_detail.js",
    dedent("""\
    function claimNeedsLiveRefresh(claim) {
        if (!claim) return false;
        if (String(claim.status_label || '').toLowerCase() === 'pending') return true;
        if (!claim.is_prizedraw) return false;
    """),
    dedent("""\
    function claimNeedsLiveRefresh(claim) {
        if (!claim) return false;
        if (String(claim.status_label || '').toLowerCase() === 'pending') {
            if (Number(claim.duration_required || 0) > 0 && !durationGoalReached(claim)) return false;
            return true;
        }
        if (!claim.is_prizedraw) return false;
    """),
)
replace_once(
    "static/claim_detail.js",
    dedent("""\
    function handleDurationGoalReached() {
