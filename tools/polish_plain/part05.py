        const claim = state.currentClaim;
        if (!claim || String(claim.status_label || '').toLowerCase() !== 'pending') return;
        if (claim.location_monitoring_required && claim.viewer_is_recipient) {
            void sendLocationHeartbeat();
            return;
        }
        void refreshClaimStatus();
    }
    """),
    dedent("""\
    function handleDurationGoalReached() {
        const claim = state.currentClaim;
        if (!claim || String(claim.status_label || '').toLowerCase() !== 'pending') return;
        if (claim.location_monitoring_required && claim.viewer_is_recipient) {
            void sendLocationHeartbeat();
        } else {
            void refreshClaimStatus();
        }
        scheduleStatusPoll(1800);
    }
    """),
)
replace_once(
    "static/claim_detail.js",
    "        renderClaim(data.claim);\n",
    "        applyClaimUpdate(data.claim);\n",
)
replace_once(
    "static/claim_detail.js",
    "async function initClaimDetail() {\n    state.language = getLanguage();\n    await identify();\n    await fetchClaimDetail();\n",
    "async function initClaimDetail() {\n    state.language = getLanguage();\n    await identify();\n    await fetchClaimDetail({ forceRender: true });\n",
)
replace_once(
    "static/claim_detail.js",
    dedent("""\
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            void refreshClaimStatus();
        } else {
            stopStatusPolling();
        }
    });

    window.addEventListener('pageshow', () => {
        if (state.currentClaim) void refreshClaimStatus();
    });
    """),
    dedent("""\
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            if (claimNeedsLiveRefresh(state.currentClaim)) void refreshClaimStatus();
        } else {
            stopStatusPolling();
        }
    });

    window.addEventListener('pageshow', () => {
        if (claimNeedsLiveRefresh(state.currentClaim)) void refreshClaimStatus();
    });
    """),
)


# Cache-bust direct module imports on the affected pages.
replace_once(
    "static/my_spots.js",
    "import { getCommonText, getSpotText, makeMySpotsText } from './interface_text.js?v=spot-fee-copy-v1-20260718';",
    "import { getCommonText, getSpotText, makeMySpotsText } from './interface_text.js?v=polish-live-v1-20260720';",
)
replace_once(
    "static/find_spots.js",
    "import { getReportReasonOptions, makeFindSpotsText, makeSpotDetailText } from './interface_text.js?v=qol-v1-20260717';",
    "import { getReportReasonOptions, makeFindSpotsText, makeSpotDetailText } from './interface_text.js?v=polish-live-v1-20260720';",
)
replace_once(
    "static/claim_detail.js",
    "import { makeClaimDetailText } from './interface_text.js?v=qol-v1-20260717';",
    "import { makeClaimDetailText } from './interface_text.js?v=polish-live-v1-20260720';",
)


# Focused regression tests for server-derived owner states and the chain-facing
# complete-Spot guard. Existing CI also compiles/lints every modified JS file.
test_path = ROOT / "tests" / "test_ui_polish_regressions.py"
test_path.write_text(
    dedent("""\
    from __future__ import annotations

    from unittest.mock import AsyncMock

    import pytest

    import constants as const
    import database as schema
    import public_html
    import trans_updater


    def owner_spot(*, success_claim_count: int = 0, max_total_claims: int = 2) -> dict:
        return {
            schema.SPOT_ID: 7,
            schema.SPOT_CREATED_BY: 1,
            schema.SPOT_LINK: "polish-test",
            schema.SPOT_TITLE: "Polish Test",
            schema.SPOT_DESC: "",
            schema.SPOT_LAT: 51.5,
            schema.SPOT_LONG: -0.1,
            schema.SPOT_RADIUS: 25,
            schema.SPOT_CLAIM_DURATION: 0,
            schema.SPOT_MAX_CLAIMS_PER_USER: 1,
            schema.SPOT_MAX_TOTAL_CLAIMS: max_total_claims,
            schema.SPOT_TOTAL_VALUE: 200_000_000,
            schema.SPOT_CREATION_FEE: 0,
            schema.SPOT_CREATION_FEE_ADDRESS: "",
            schema.SPOT_DEPOSIT_ADDRESS: "NQ00 TEST",
            schema.SPOT_STARTS_AT: 1_700_000_000,
            schema.SPOT_ENDS_AT: 86_400,
            schema.SPOT_USE_PASSWORD: 0,
            schema.SPOT_CREATED_AT: 1_700_000_000,
            schema.SPOT_UPDATED_AT: 1_700_000_000,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            "claim_count": success_claim_count,
            "pending_claim_count": 0,
            "success_claim_count": success_claim_count,
            "failed_claim_count": 0,
            "claim_code_count": 0,
            "unused_code_count": 0,
            "used_code_count": 0,
            "report_count": 0,
            "pending_report_count": 0,
            "trans_count": 0,
            "trans_total_amount": 0,
        }


    def draft_spot() -> dict:
        spot = owner_spot()
        spot[schema.SPOT_STATUS] = const.SPOT_STATUS_DRAFT
        return spot


    def fill_transaction(*, status: int) -> dict:
        return {
            schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
            schema.TRANS_STATUS: status,
            schema.TRANS_AMOUNT: 200_000_000,
            schema.TRANS_TX_HASH: "test-hash",
            schema.TRANS_CREATED_AT: 1_700_000_001,
        }


    def test_pending_and_confirmed_deposits_have_distinct_badges() -> None:
        pending = public_html._serialise_owner_spot(
            draft_spot(),
            now=1_700_000_010,
            transactions=[fill_transaction(status=const.TRANS_STATUS_PENDING)],
        )
        confirmed = public_html._serialise_owner_spot(
            draft_spot(),
            now=1_700_000_010,
            transactions=[fill_transaction(status=const.TRANS_STATUS_CONFIRMED)],
        )

        assert pending["badge_status_label"] == "depositing"
        assert confirmed["badge_status_label"] == "deposited"


    def test_exhausted_standard_spot_is_complete_and_not_cancellable() -> None:
        serialised = public_html._serialise_owner_spot(
            owner_spot(success_claim_count=2, max_total_claims=2),
            now=1_700_000_010,
            transactions=[],
        )

        assert serialised["status_label"] == "completed"
        assert serialised["badge_status_label"] == "completed"
        assert serialised["bucket"] == "previous"
        assert serialised["can_cancel"] is False


    @pytest.mark.asyncio
    async def test_chain_cancellation_guard_rejects_complete_standard_spot(monkeypatch) -> None:
        summary = owner_spot(success_claim_count=2, max_total_claims=2)
        monkeypatch.setattr(
            trans_updater.db_access,
            "get_spot_owner_summary",
            AsyncMock(return_value=summary),
        )

        assert await trans_updater._published_standard_spot_is_complete(object(), spot_id=7)
    """),
    encoding="utf-8",
)

print("Applied NimHunt live-status and claim-flow polish patch.")
