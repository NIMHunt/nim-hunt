"""Safe, idempotent recording for user-submitted Spot deposits.

Nimiq Pay is the authority that signs and broadcasts a creator deposit.  The
browser then tells NimHunt the returned transaction hash so the backend can
track it.  Losing that HTTP response must never encourage the user to send the
same funding payment again.

The authoritative sender is read from the blockchain when the transaction is
confirmed.  For the first deposit, a missing client-reported sender is therefore
stored as an empty placeholder and replaced during chain verification.  Later
top-ups still require the established funding wallet to be identified before
the transaction is recorded.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import constants as const
import database as schema
import db_access
import trans_updater
import wallet

RowDict = dict[str, Any]

_INSTALLED = False
_ORIGINAL_RECORD = None


def _normalise_optional_sender(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return wallet.normalise_nimiq_address(
            raw,
            field_name="deposit from_address",
            allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
        )
    except ValueError:
        # The browser-supplied sender is not trusted for deposits.  A wrapper or
        # stale SDK may return an account object rather than a plain address;
        # chain verification replaces this placeholder with the real sender.
        return ""


async def _existing_transaction_by_hash(db, *, tx_hash: str) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_TX_HASH} = ?
        LIMIT 1;
        """,
        (str(tx_hash).strip(),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


def _same_deposit(existing: RowDict, *, user_id: int, spot_id: int) -> bool:
    return (
        int(existing.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(existing.get(schema.TRANS_USER_ID) or -1) == int(user_id)
        and int(existing.get(schema.TRANS_SPOT_ID) or -1) == int(spot_id)
    )


def _existing_result(existing: RowDict, *, spot_id: int) -> RowDict:
    return {
        "ok": True,
        "already_recorded": True,
        "trans_id": int(existing[schema.TRANS_ID]),
        "spot_id": int(spot_id),
        "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
        "status": int(existing.get(schema.TRANS_STATUS) or const.TRANS_STATUS_PENDING),
    }


async def record_spot_deposit_transaction_safely(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str | None,
    tx_hash: str,
    to_address: str | None = None,
) -> RowDict:
    """Record one Nimiq Pay deposit without requiring a second blockchain send."""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    clean_hash = str(tx_hash or "").strip()
    if not clean_hash:
        raise ValueError("tx_hash must not be empty")

    existing = await _existing_transaction_by_hash(db, tx_hash=clean_hash)
    if existing is not None:
        if not _same_deposit(existing, user_id=user_id, spot_id=spot_id):
            raise ValueError("this transaction hash is already attached to a different record")
        return _existing_result(existing, spot_id=spot_id)

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("only draft spots can receive creator deposits")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("this draft is being cancelled and cannot receive another deposit")

    clean_to_address = wallet.normalise_nimiq_address(
        str(to_address or spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="deposit to_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )
    clean_from_address = _normalise_optional_sender(from_address)

    funding_address = await db_access.get_confirmed_spot_funding_address(
        db,
        spot_id=int(spot_id),
    )
    if funding_address is not None:
        if not clean_from_address:
            raise ValueError(
                "Nimiq Pay did not expose the funding wallet for this top-up. "
                "Reopen Deposit and approve wallet access before sending."
            )
        established_sender = wallet.normalise_nimiq_address(
            funding_address,
            field_name="established funding address",
            allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
        )
        if clean_from_address != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    try:
        trans_id = await db_access.create_spot_deposit_transaction(
            db,
            user_id=int(user_id),
            spot_id=int(spot_id),
            amount=amount,
            from_address=clean_from_address,
            to_address=clean_to_address,
            tx_hash=clean_hash,
        )
    except sqlite3.IntegrityError:
        # A response retry can race with the original request.  The hash's UNIQUE
        # constraint decides the winner; returning the existing row makes the
        # HTTP operation idempotent without touching the blockchain again.
        existing = await _existing_transaction_by_hash(db, tx_hash=clean_hash)
        if existing is None or not _same_deposit(existing, user_id=user_id, spot_id=spot_id):
            raise
        return _existing_result(existing, spot_id=spot_id)

    return {
        "ok": True,
        "already_recorded": False,
        "trans_id": int(trans_id),
        "spot_id": int(spot_id),
        "amount": amount,
    }


def install() -> None:
    """Install the safer recorder once for all deposit-submission routes."""
    global _INSTALLED, _ORIGINAL_RECORD
    if _INSTALLED:
        return
    _ORIGINAL_RECORD = trans_updater.record_spot_deposit_transaction
    trans_updater.record_spot_deposit_transaction = record_spot_deposit_transaction_safely
    _INSTALLED = True


__all__ = ["install", "record_spot_deposit_transaction_safely"]
