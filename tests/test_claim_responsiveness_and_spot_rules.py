import re

import pytest
from fastapi import BackgroundTasks

import constants as const
import database as schema
import db_access
import public_html
import settlement_updater


def test_generated_claim_codes_are_easy_to_type():
    codes = {db_access._make_placeholder_claim_code() for _ in range(100)}
    assert len(codes) == 100
    assert all(re.fullmatch(r"[A-Z0-9]{10}", code) for code in codes)


def test_claim_code_input_is_uppercased_and_rejects_punctuation():
    assert db_access._normalise_claim_code(" ab12 ", required=True) == "AB12"
    with pytest.raises(ValueError, match="letters and numbers only"):
        db_access._normalise_claim_code("AB-12", required=True)


def test_selected_prizedraw_winner_is_shown_before_payout_confirmation():
    display = public_html._claim_display_status(
        claim={schema.CLAIM_STATUS: const.CLAIM_STATUS_PENDING},
        spot={schema.SPOT_STATUS: const.SPOT_STATUS_COMPLETED},
        is_prizedraw=True,
        status_label="pending",
        payout={
            "payout_pending_count": 1,
            "payout_failed_count": 0,
            "payout_confirmed_count": 0,
        },
    )
    assert display == {"label": "won_pending", "text": "Won!", "class": "success"}


def test_standard_claim_payout_is_queued_after_response():
    tasks = BackgroundTasks()
    public_html._queue_claim_settlement(
        tasks,
        claim={
            schema.CLAIM_ID: 7,
            schema.CLAIM_SPOT_ID: 3,
            schema.CLAIM_STATUS: const.CLAIM_STATUS_SUCCESS,
        },
        spot={schema.PRIZEDRAW_PRIZE_COUNT: None},
    )
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is settlement_updater.payout_standard_claim_if_ready
    assert tasks.tasks[0].kwargs == {"claim_id": 7}


def test_prizedraw_settlement_is_queued_without_waiting_for_chain_work():
    tasks = BackgroundTasks()
    public_html._queue_claim_settlement(
        tasks,
        claim={
            schema.CLAIM_ID: 9,
            schema.CLAIM_SPOT_ID: 4,
            schema.CLAIM_STATUS: const.CLAIM_STATUS_SUCCESS,
        },
        spot={schema.PRIZEDRAW_PRIZE_COUNT: 1},
    )
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is settlement_updater.settle_prizedraw_spot_if_ready
    assert tasks.tasks[0].kwargs == {"spot_id": 4}


@pytest.mark.asyncio
async def test_past_start_is_publishable_until_its_end_time(monkeypatch):
    now = 10_000
    spot = {
        schema.SPOT_ID: 1,
        schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
        schema.SPOT_CREATED_BY: 1,
        schema.SPOT_TITLE: "Past Start",
        schema.SPOT_DEPOSIT_ADDRESS: "NQ00 TEST",
        schema.SPOT_LAT: 51.5,
        schema.SPOT_LONG: -0.1,
        schema.SPOT_RADIUS: 100,
        schema.SPOT_MAX_CLAIMS_PER_USER: 1,
        schema.SPOT_MAX_TOTAL_CLAIMS: 1,
        schema.SPOT_TOTAL_VALUE: const.MIN_STANDARD_CLAIM_PAYOUT,
        schema.SPOT_STARTS_AT: now - 60,
        schema.SPOT_ENDS_AT: const.MIN_SPOT_ENDS_AFTER_SECONDS,
        schema.SPOT_USE_PASSWORD: 0,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
        schema.SPOT_CREATION_FEE: 0,
    }

    async def get_spot(_db, *, spot_id):
        assert spot_id == 1
        return spot

    async def true_for_any(*args, **kwargs):
        return True

    async def no_prizedraw(*args, **kwargs):
        return None

    async def unixepoch(_db):
        return now

    async def confirmed_total(*args, **kwargs):
        return db_access.spot_required_deposit_amount(spot)

    monkeypatch.setattr(db_access, "get_spot", get_spot)
    monkeypatch.setattr(db_access, "can_user_create_spot", true_for_any)
    monkeypatch.setattr(db_access, "get_prizedraw", no_prizedraw)
    monkeypatch.setattr(db_access, "spot_meets_minimum_payout", true_for_any)
    monkeypatch.setattr(db_access, "get_unixepoch", unixepoch)
    monkeypatch.setattr(db_access, "get_confirmed_spot_deposit_total", confirmed_total)
    monkeypatch.setattr(db_access, "has_confirmed_spot_creation_fee_transaction", true_for_any)

    assert await db_access.can_publish_spot(object(), spot_id=1) is True

    spot[schema.SPOT_STARTS_AT] = now - const.MIN_SPOT_ENDS_AFTER_SECONDS - 1
    assert await db_access.can_publish_spot(object(), spot_id=1) is False
