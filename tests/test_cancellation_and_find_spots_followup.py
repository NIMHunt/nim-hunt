import constants as const
import database as schema
import public_html


def _public_spot(*, max_total=2, successful=0, pending=0, codes=0, unused=0, cancelled_at=None):
    return {
        "spot": {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_STARTS_AT: 100,
            schema.SPOT_ENDS_AT: 1000,
            schema.SPOT_MAX_TOTAL_CLAIMS: max_total,
            schema.SPOT_CANCELLATION_STARTED_AT: cancelled_at,
        },
        "counts": {
            "success_claim_count": successful,
            "pending_claim_count": pending,
            "claim_code_count": codes,
            "unused_code_count": unused,
        },
    }


def test_find_spots_hides_standard_spot_when_claim_capacity_is_exhausted():
    item = _public_spot(max_total=2, successful=2)
    assert public_html._spot_has_public_claim_capacity(item) is False
    assert public_html._spot_matches_filters(
        item,
        now=200,
        include_active=True,
        include_upcoming=True,
        include_prizedraws=True,
    ) is False


def test_find_spots_hides_password_spot_when_no_codes_remain():
    item = _public_spot(max_total=10, successful=1, codes=2, unused=0)
    assert public_html._spot_has_public_claim_capacity(item) is False


def test_find_spots_hides_spot_as_soon_as_cancellation_is_requested():
    item = _public_spot(cancelled_at=150)
    assert public_html._spot_matches_filters(
        item,
        now=200,
        include_active=True,
        include_upcoming=True,
        include_prizedraws=True,
    ) is False


def test_pending_deposit_replaces_draft_badge_with_deposited():
    spot = {
        schema.SPOT_ID: 3,
        schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
        schema.SPOT_CREATED_BY: 1,
        schema.SPOT_TITLE: "Depositing",
        schema.SPOT_TOTAL_VALUE: 100 * const.LUNA_PER_NIM,
        schema.SPOT_CREATION_FEE: 0,
        schema.SPOT_DEPOSIT_ADDRESS: "NQ00 TEST",
        schema.SPOT_LAT: 51.5,
        schema.SPOT_LONG: -0.1,
        schema.SPOT_RADIUS: 100,
        schema.SPOT_MAX_TOTAL_CLAIMS: 1,
        schema.SPOT_MAX_CLAIMS_PER_USER: 1,
        schema.SPOT_STARTS_AT: 2_000,
        schema.SPOT_ENDS_AT: 3_600,
        schema.SPOT_USE_PASSWORD: 0,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
    }
    transactions = [{
        schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
        schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
        schema.TRANS_AMOUNT: 100 * const.LUNA_PER_NIM,
        schema.TRANS_TX_HASH: "pending-deposit",
        schema.TRANS_CREATED_AT: 1,
    }]

    serialised = public_html._serialise_owner_spot(
        spot,
        now=1_000,
        transactions=transactions,
    )
    assert serialised["status_label"] == "draft"
    assert serialised["badge_status_label"] == "depositing"


def test_upcoming_standard_spot_can_be_cancelled():
    spot = {
        schema.SPOT_ID: 4,
        schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
        schema.SPOT_CREATED_BY: 1,
        schema.SPOT_TITLE: "Upcoming",
        schema.SPOT_TOTAL_VALUE: 100 * const.LUNA_PER_NIM,
        schema.SPOT_CREATION_FEE: 0,
        schema.SPOT_DEPOSIT_ADDRESS: "NQ00 TEST",
        schema.SPOT_LAT: 51.5,
        schema.SPOT_LONG: -0.1,
        schema.SPOT_RADIUS: 100,
        schema.SPOT_MAX_TOTAL_CLAIMS: 1,
        schema.SPOT_MAX_CLAIMS_PER_USER: 1,
        schema.SPOT_STARTS_AT: 2_000,
        schema.SPOT_ENDS_AT: 3_600,
        schema.SPOT_USE_PASSWORD: 0,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
        schema.PRIZEDRAW_PRIZE_COUNT: None,
    }
    serialised = public_html._serialise_owner_spot(spot, now=1_000, transactions=[])
    assert serialised["status_label"] == "upcoming"
    assert serialised["can_cancel"] is True
