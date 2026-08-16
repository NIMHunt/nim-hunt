import asyncio

import pytest

import constants as const
import database as schema
import public_html


def _detail_spot(*, status_code, spot_id=7, successful=0, max_total=2):
    return {
        schema.SPOT_ID: spot_id,
        schema.SPOT_STATUS: status_code,
        schema.SPOT_CREATED_BY: 1,
        schema.SPOT_LINK: f"spot-{spot_id}",
        schema.SPOT_TITLE: "Historical Spot",
        schema.SPOT_DESC: "Still useful after claiming ends.",
        schema.SPOT_CITY: "Rothesay",
        schema.SPOT_COUNTRY: "United Kingdom",
        schema.SPOT_LAT: 55.836,
        schema.SPOT_LONG: -5.055,
        schema.SPOT_RADIUS: 100,
        schema.SPOT_CLAIM_DURATION: 0,
        schema.SPOT_USE_PASSWORD: 0,
        schema.SPOT_MAX_CLAIMS_PER_USER: 1,
        schema.SPOT_MAX_TOTAL_CLAIMS: max_total,
        schema.SPOT_TOTAL_VALUE: 200 * const.LUNA_PER_NIM,
        schema.SPOT_STARTS_AT: 1_000,
        schema.SPOT_ENDS_AT: 3_600,
        schema.PRIZEDRAW_PRIZE_COUNT: None,
        "claim_count": successful,
        "pending_claim_count": 0,
        "success_claim_count": successful,
        "failed_claim_count": 0,
        "claim_code_count": 0,
        "unused_code_count": 0,
        "used_code_count": 0,
        "creator_display_name": "Creator",
    }


def test_completed_spot_detail_is_resolved_by_numeric_id(monkeypatch):
    completed = _detail_spot(status_code=const.SPOT_STATUS_COMPLETED)

    async def fake_get_spot_owner_summary(_db, *, spot_id):
        return completed if spot_id == 7 else None

    async def unexpected_link_lookup(_db, *, link):
        raise AssertionError(f"link lookup should not be needed: {link}")

    monkeypatch.setattr(
        public_html.db_access,
        "get_spot_owner_summary",
        fake_get_spot_owner_summary,
    )
    monkeypatch.setattr(
        public_html.db_access,
        "get_spot_by_link",
        unexpected_link_lookup,
    )

    result = asyncio.run(
        public_html._get_public_spot_detail_row(object(), spot_ref="7")
    )
    assert result == completed


def test_cancelled_spot_detail_is_resolved_by_public_link(monkeypatch):
    cancelled = _detail_spot(
        status_code=const.SPOT_STATUS_CANCELLED,
        spot_id=8,
    )

    async def fake_get_spot_by_link(_db, *, link):
        assert link == "cancelled-link"
        return {schema.SPOT_ID: 8}

    async def fake_get_spot_owner_summary(_db, *, spot_id):
        return cancelled if spot_id == 8 else None

    monkeypatch.setattr(
        public_html.db_access,
        "get_spot_by_link",
        fake_get_spot_by_link,
    )
    monkeypatch.setattr(
        public_html.db_access,
        "get_spot_owner_summary",
        fake_get_spot_owner_summary,
    )

    result = asyncio.run(
        public_html._get_public_spot_detail_row(
            object(),
            spot_ref="cancelled-link",
        )
    )
    assert result == cancelled


@pytest.mark.parametrize(
    "status_code",
    [const.SPOT_STATUS_DRAFT, const.SPOT_STATUS_BANNED],
)
def test_private_or_moderated_spot_states_are_not_public_detail_pages(
    monkeypatch,
    status_code,
):
    hidden = _detail_spot(status_code=status_code)

    async def fake_get_spot_owner_summary(_db, *, spot_id):
        return hidden if spot_id == 7 else None

    async def fake_get_spot_by_link(_db, *, link):
        return None

    monkeypatch.setattr(
        public_html.db_access,
        "get_spot_owner_summary",
        fake_get_spot_owner_summary,
    )
    monkeypatch.setattr(
        public_html.db_access,
        "get_spot_by_link",
        fake_get_spot_by_link,
    )

    result = asyncio.run(
        public_html._get_public_spot_detail_row(object(), spot_ref="7")
    )
    assert result is None


@pytest.mark.parametrize(
    ("status_code", "expected_label"),
    [
        (const.SPOT_STATUS_COMPLETED, "completed"),
        (const.SPOT_STATUS_CANCELLED, "cancelled"),
    ],
)
def test_historical_spot_detail_preserves_terminal_status_badges(
    status_code,
    expected_label,
):
    spot = _detail_spot(status_code=status_code)
    detail = public_html._serialise_public_spot_for_detail(spot, now=2_000)
    assert detail["status_label"] == expected_label


def test_full_published_standard_spot_detail_is_shown_as_completed():
    spot = _detail_spot(
        status_code=const.SPOT_STATUS_PUBLISHED,
        successful=2,
        max_total=2,
    )
    detail = public_html._serialise_public_spot_for_detail(spot, now=2_000)
    assert detail["status_label"] == "completed"


def test_expired_published_spot_detail_is_shown_as_ended():
    spot = _detail_spot(status_code=const.SPOT_STATUS_PUBLISHED)
    detail = public_html._serialise_public_spot_for_detail(spot, now=5_000)
    assert detail["status_label"] == "ended"
