"""User-facing funding status and publish-readiness rules."""

from __future__ import annotations

from typing import Any

import constants as const
import database as schema
import db_access
import public_html
import trans_updater

RowDict = dict[str, Any]
LOCAL_INTENT_PREFIX = str(
    getattr(trans_updater, "LOCAL_TRANSACTION_INTENT_PREFIX", "NIMHUNT_INTENT:")
)
_ORIGINAL_CAN_PUBLISH_SPOT = db_access.can_publish_spot
_INSTALLED = False


def address_key(value: Any) -> str:
    return "".join(str(value or "").strip().upper().split())


def is_real_chain_hash(value: Any) -> bool:
    tx_hash = str(value or "").strip()
    return bool(tx_hash) and not tx_hash.startswith(LOCAL_INTENT_PREFIX)


def transaction_status(row: RowDict) -> int:
    value = row.get(schema.TRANS_STATUS)
    return int(value if value is not None else -1)


def matching_creation_fee_rows(
    transactions: list[RowDict],
    *,
    creation_fee: int,
    deposit_address: str,
    creation_fee_address: str,
) -> list[RowDict]:
    expected_from = address_key(deposit_address)
    expected_to = address_key(creation_fee_address)
    return [
        row
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CREATION_FEE
        and int(row.get(schema.TRANS_AMOUNT) or 0) == int(creation_fee)
        and address_key(row.get(schema.TRANS_FROM_ADDRESS)) == expected_from
        and address_key(row.get(schema.TRANS_TO_ADDRESS)) == expected_to
    ]


async def has_broadcast_spot_creation_fee_transaction(db, *, spot_id: int) -> bool:
    """Return true once the exact internal fee send has a real chain hash."""
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        return False
    fee_amount = db_access.spot_creation_fee_amount(spot)
    if fee_amount <= 0:
        return True

    rows = await db_access.get_transactions_by_spot(
        db,
        spot_id=int(spot_id),
        limit=db_access.MAX_LIMIT,
    )
    matching = matching_creation_fee_rows(
        rows,
        creation_fee=fee_amount,
        deposit_address=str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        creation_fee_address=str(spot.get(schema.SPOT_CREATION_FEE_ADDRESS) or ""),
    )
    return any(
        transaction_status(row)
        in {const.TRANS_STATUS_PENDING, const.TRANS_STATUS_CONFIRMED}
        and is_real_chain_hash(row.get(schema.TRANS_TX_HASH))
        for row in matching
    )


async def can_publish_spot_after_fee_broadcast(db, *, spot_id: int) -> bool:
    """Require all ordinary checks and an exact confirmed creation fee."""
    if not await _ORIGINAL_CAN_PUBLISH_SPOT(db, spot_id=int(spot_id)):
        return False
    return await db_access.has_confirmed_spot_creation_fee_transaction(
        db,
        spot_id=int(spot_id),
    )


def deposit_summary(
    transactions: list[RowDict],
    *,
    total_value: int = 0,
    creation_fee: int = 0,
    deposit_address: str = "",
    creation_fee_address: str = "",
) -> RowDict:
    """Describe only the creator's deposit; keep the internal fee invisible."""
    fills = [
        row
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
    ]
    fees = [
        row
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CREATION_FEE
    ]

    confirmed_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in fills
        if transaction_status(row) == const.TRANS_STATUS_CONFIRMED
    )
    pending_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in fills
        if transaction_status(row) == const.TRANS_STATUS_PENDING
    )
    failed_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in fills
        if transaction_status(row) == const.TRANS_STATUS_FAILED
    )

    total_value = max(0, int(total_value or 0))
    creation_fee = max(0, int(creation_fee or 0))
    required_total = total_value + creation_fee
    submitted_amount = confirmed_amount + pending_amount
    recorded_amount = submitted_amount + failed_amount
    amount_due = max(0, required_total - submitted_amount)
    funding_submitted = required_total > 0 and submitted_amount >= required_total
    funding_complete = required_total > 0 and confirmed_amount >= required_total

    matching = matching_creation_fee_rows(
        fees,
        creation_fee=creation_fee,
        deposit_address=deposit_address,
        creation_fee_address=creation_fee_address,
    )
    confirmed_matches = [
        row
        for row in matching
        if transaction_status(row) == const.TRANS_STATUS_CONFIRMED
    ]
    broadcast_matches = [
        row
        for row in matching
        if transaction_status(row)
        in {const.TRANS_STATUS_PENDING, const.TRANS_STATUS_CONFIRMED}
        and is_real_chain_hash(row.get(schema.TRANS_TX_HASH))
    ]
    local_intents = [
        row
        for row in matching
        if transaction_status(row) == const.TRANS_STATUS_PENDING
        and not is_real_chain_hash(row.get(schema.TRANS_TX_HASH))
    ]

    confirmed_fee_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in fees
        if transaction_status(row) == const.TRANS_STATUS_CONFIRMED
    )
    pending_fee_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in fees
        if transaction_status(row) == const.TRANS_STATUS_PENDING
    )
    failed_fee_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in fees
        if transaction_status(row) == const.TRANS_STATUS_FAILED
    )
    matching_confirmed_fee_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0) for row in confirmed_matches
    )
    matching_broadcast_fee_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0) for row in broadcast_matches
    )
    local_intent_fee_amount = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0) for row in local_intents
    )

    fee_confirmed = creation_fee <= 0 or matching_confirmed_fee_amount >= creation_fee
    fee_submitted = creation_fee <= 0 or matching_broadcast_fee_amount >= creation_fee
    if creation_fee <= 0:
        fee_status = "not_due"
    elif fee_confirmed:
        fee_status = "confirmed"
    elif fee_submitted:
        fee_status = "pending"
    elif local_intent_fee_amount > 0:
        fee_status = "attention_required"
    elif confirmed_fee_amount > 0:
        fee_status = "verification_mismatch"
    elif failed_fee_amount > 0:
        fee_status = "retrying"
    elif funding_complete:
        fee_status = "preparing"
    else:
        fee_status = "waiting_for_funding"

    if submitted_amount <= 0:
        status_value, status_label = "missing", "No Deposit"
    elif pending_amount > 0:
        status_value, status_label = "processing", "Deposit Processing"
    elif not funding_complete:
        status_value, status_label = "partial", "Partial Deposit"
    else:
        status_value, status_label = "ready", "Ready"

    latest = fills[0] if fills else None
    return {
        "status": status_value,
        "status_label": status_label,
        "paid": status_value == "ready",
        "amount": confirmed_amount if status_value == "partial" else submitted_amount,
        "recorded_amount": recorded_amount,
        "submitted_amount": submitted_amount,
        "amount_due": amount_due,
        "required_total": required_total,
        "spot_value": total_value,
        "creation_fee": creation_fee,
        "creation_fee_address": creation_fee_address,
        "funding_submitted": funding_submitted,
        "funding_complete": funding_complete,
        # Existing owner serialisation uses fee_paid as its publish-readiness flag.
        # Publication now waits for the exact snapshotted fee transfer to confirm.
        "fee_paid": fee_confirmed,
        "fee_submitted": fee_submitted,
        "fee_confirmed": fee_confirmed,
        "fee_status": fee_status,
        "requires_attention": local_intent_fee_amount > 0,
        "has_any": bool(fills),
        "has_submitted": submitted_amount > 0,
        "has_pending": pending_amount > 0,
        "confirmed_amount": confirmed_amount,
        "pending_amount": pending_amount,
        "failed_amount": failed_amount,
        "confirmed_fee_amount": confirmed_fee_amount,
        "matching_confirmed_fee_amount": matching_confirmed_fee_amount,
        "matching_broadcast_fee_amount": matching_broadcast_fee_amount,
        "local_intent_fee_amount": local_intent_fee_amount,
        "pending_fee_amount": pending_fee_amount,
        "failed_fee_amount": failed_fee_amount,
        "tx_hash": latest.get(schema.TRANS_TX_HASH) if latest else None,
        "created_at": latest.get(schema.TRANS_CREATED_AT) if latest else None,
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    db_access.can_publish_spot = can_publish_spot_after_fee_broadcast
    public_html._deposit_summary = deposit_summary
    _INSTALLED = True
