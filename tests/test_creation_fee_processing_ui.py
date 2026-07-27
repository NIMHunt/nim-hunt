from __future__ import annotations

import json
from pathlib import Path

import constants as const
import database as schema
import public_html


def _transaction(*, trans_type: int, status: int, amount: int, tx_hash: str, from_address: str, to_address: str):
    return {
        schema.TRANS_ID: 1,
        schema.TRANS_TYPE: trans_type,
        schema.TRANS_STATUS: status,
        schema.TRANS_AMOUNT: amount,
        schema.TRANS_TX_HASH: tx_hash,
        schema.TRANS_FROM_ADDRESS: from_address,
        schema.TRANS_TO_ADDRESS: to_address,
        schema.TRANS_CREATED_AT: 1,
    }


def _draft_spot(*, now: int = 1_700_000_000):
    return {
        schema.SPOT_ID: 7,
        schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
        schema.SPOT_TITLE: "Processing Spot",
        schema.SPOT_DESC: None,
        schema.SPOT_CITY: "Rothesay",
        schema.SPOT_COUNTRY: "Scotland",
        schema.SPOT_LAT: 55.836,
        schema.SPOT_LONG: -5.055,
        schema.SPOT_RADIUS: 100,
        schema.SPOT_CLAIM_DURATION: 0,
        schema.SPOT_MAX_CLAIMS_PER_USER: 1,
        schema.SPOT_MAX_TOTAL_CLAIMS: 1,
        schema.SPOT_TOTAL_VALUE: const.MIN_SPOT_TOTAL_VALUE,
        schema.SPOT_CREATION_FEE: const.STANDARD_SPOT_CREATION_FEE,
        schema.SPOT_CREATION_FEE_ADDRESS: const.DEV_PLATFORM_FEE_ADDRESS,
        schema.SPOT_DEPOSIT_ADDRESS: "NQ DEPOSIT",
        schema.SPOT_STARTS_AT: now + 3600,
        schema.SPOT_ENDS_AT: const.MIN_SPOT_ENDS_AFTER_SECONDS,
        schema.SPOT_USE_PASSWORD: 0,
        schema.SPOT_CREATED_AT: now,
        schema.SPOT_UPDATED_AT: now,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
    }


def test_confirmed_creator_deposit_is_processing_until_creation_fee_confirms():
    now = 1_700_000_000
    spot = _draft_spot(now=now)
    required = const.MIN_SPOT_TOTAL_VALUE + const.STANDARD_SPOT_CREATION_FEE
    transactions = [
        _transaction(
            trans_type=const.TRANS_TYPE_FILL_SPOT,
            status=const.TRANS_STATUS_CONFIRMED,
            amount=required,
            tx_hash="deposit",
            from_address="NQ CREATOR",
            to_address="NQ DEPOSIT",
        )
    ]

    result = public_html._serialise_owner_spot(spot, now=now, transactions=transactions)

    assert result["deposit"]["funding_complete"] is True
    assert result["deposit"]["fee_paid"] is False
    assert result["badge_status_label"] == "processing"
    assert result["can_publish"] is False
    assert result["publish_block_reason"] == "creation_fee_processing"
    assert result["publish_block_message"] == public_html._CREATION_FEE_PROCESSING_MESSAGE


def test_deposited_and_publishable_are_reserved_for_confirmed_creation_fee():
    now = 1_700_000_000
    spot = _draft_spot(now=now)
    required = const.MIN_SPOT_TOTAL_VALUE + const.STANDARD_SPOT_CREATION_FEE
    transactions = [
        _transaction(
            trans_type=const.TRANS_TYPE_FILL_SPOT,
            status=const.TRANS_STATUS_CONFIRMED,
            amount=required,
            tx_hash="deposit",
            from_address="NQ CREATOR",
            to_address="NQ DEPOSIT",
        ),
        _transaction(
            trans_type=const.TRANS_TYPE_CREATION_FEE,
            status=const.TRANS_STATUS_CONFIRMED,
            amount=const.STANDARD_SPOT_CREATION_FEE,
            tx_hash="fee",
            from_address="NQ DEPOSIT",
            to_address=const.DEV_PLATFORM_FEE_ADDRESS,
        ),
    ]

    result = public_html._serialise_owner_spot(spot, now=now, transactions=transactions)

    assert result["deposit"]["fee_paid"] is True
    assert result["badge_status_label"] == "deposited"
    assert result["can_publish"] is True
    assert result["publish_block_reason"] is None


def test_defensive_publish_response_uses_the_same_processing_message():
    response = public_html._creation_fee_processing_response({"language": "en"})
    payload = json.loads(response.body)
    assert response.status_code == 409
    assert payload["code"] == "creation_fee_processing"
    assert payload["message"] == public_html._CREATION_FEE_PROCESSING_MESSAGE


def test_locked_publish_tooltip_uses_the_server_message():
    source = Path("static/my_spots.js").read_text(encoding="utf-8")
    assert "tooltip: spot.publish_block_message || TEXT.ownerActions.publishUnavailableTooltip" in source
