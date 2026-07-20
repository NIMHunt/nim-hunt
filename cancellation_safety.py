"""Crash-safe, at-most-once guard for Spot cancellation money sends.

A cancellation refund or fee must never be broadcast automatically twice merely
because its first attempt later acquired an ambiguous ``FAILED`` status.  This
module wraps the existing cancellation workflow with a durable per-Spot lease.
The lease is committed before any cancellation send can begin and is only
released after the workflow returns safely.  A crash or ambiguous send leaves a
persistent block which requires manual reconciliation rather than another send.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Awaitable, Callable

import constants as const
import database as schema
import db_access
import trans_updater

RowDict = dict[str, Any]
CancellationSubmitter = Callable[..., Awaitable[RowDict]]

_GUARD_TABLE = "CANCELLATION_SEND_GUARD"
_INSTALLED = False
_ORIGINAL_SUBMIT: CancellationSubmitter | None = None
_SPOT_LOCKS: dict[int, asyncio.Lock] = {}


def _spot_lock(spot_id: int) -> asyncio.Lock:
    lock = _SPOT_LOCKS.get(int(spot_id))
    if lock is None:
        lock = asyncio.Lock()
        _SPOT_LOCKS[int(spot_id)] = lock
    return lock


async def _ensure_guard_table(db) -> None:
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_GUARD_TABLE} (
            spot_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('processing', 'blocked')),
            reason TEXT,
            created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at INTEGER NOT NULL DEFAULT (unixepoch())
        );
        """
    )
    await db.commit()


async def _guard_row(db, *, spot_id: int) -> RowDict | None:
    cur = await db.execute(
        f"SELECT * FROM {_GUARD_TABLE} WHERE spot_id = ?;",
        (int(spot_id),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _acquire_guard(db, *, spot_id: int) -> bool:
    try:
        await db.execute(
            f"""
            INSERT INTO {_GUARD_TABLE} (spot_id, state, reason)
            VALUES (?, 'processing', 'cancellation send started');
            """,
            (int(spot_id),),
        )
        await db.commit()
        return True
    except sqlite3.IntegrityError:
        await db.rollback()
        return False


async def _release_guard(db, *, spot_id: int) -> None:
    await db.execute(
        f"DELETE FROM {_GUARD_TABLE} WHERE spot_id = ?;",
        (int(spot_id),),
    )
    await db.commit()


async def _block_guard(db, *, spot_id: int, reason: str) -> None:
    await db.execute(
        f"""
        INSERT INTO {_GUARD_TABLE} (spot_id, state, reason)
        VALUES (?, 'blocked', ?)
        ON CONFLICT(spot_id) DO UPDATE SET
            state = 'blocked',
            reason = excluded.reason,
            updated_at = unixepoch();
        """,
        (int(spot_id), str(reason).strip()[:500]),
    )
    await db.commit()


def _failed_cancellation_legs(transactions: list[RowDict]) -> list[RowDict]:
    cancellation_types = {
        const.TRANS_TYPE_CANCEL_SPOT,
        const.TRANS_TYPE_PLAT_FEE,
    }
    return [
        row
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) in cancellation_types
        and int(
            row.get(schema.TRANS_STATUS)
            if row.get(schema.TRANS_STATUS) is not None
            else -1
        )
        == const.TRANS_STATUS_FAILED
    ]


def _blocked_result(*, spot_id: int, reason: str) -> RowDict:
    return {
        "ok": True,
        "spot_id": int(spot_id),
        "cancelled": False,
        "cancellation_pending": True,
        "deferred": True,
        "manual_review_required": True,
        "reason": "manual_reconciliation_required",
        "message": (
            "Cancellation is paused because a previous refund or fee attempt may "
            "already have reached the chain. No further automatic sends will occur."
        ),
        "guard_reason": str(reason),
    }


async def guarded_submit_spot_cancellation_transactions(
    db,
    *,
    spot_id: int,
    cancellation_fee: int | None = None,
    fee_address: str | None = None,
) -> RowDict:
    """Run one cancellation under a durable, crash-safe send lease."""
    spot_id = int(spot_id)
    original = _ORIGINAL_SUBMIT
    if original is None:
        raise RuntimeError("cancellation safety guard is not installed")

    async with _spot_lock(spot_id):
        await _ensure_guard_table(db)

        existing_guard = await _guard_row(db, spot_id=spot_id)
        if existing_guard is not None:
            return _blocked_result(
                spot_id=spot_id,
                reason=str(existing_guard.get("reason") or existing_guard.get("state")),
            )

        transactions = await db_access.get_transactions_by_spot(
            db,
            spot_id=spot_id,
            limit=db_access.MAX_LIMIT,
        )
        failed_legs = _failed_cancellation_legs(transactions)
        if failed_legs:
            await _block_guard(
                db,
                spot_id=spot_id,
                reason="existing failed or ambiguous cancellation transaction",
            )
            return _blocked_result(
                spot_id=spot_id,
                reason="existing failed or ambiguous cancellation transaction",
            )

        if not await _acquire_guard(db, spot_id=spot_id):
            current = await _guard_row(db, spot_id=spot_id)
            return _blocked_result(
                spot_id=spot_id,
                reason=str((current or {}).get("reason") or "another cancellation worker acquired the lease"),
            )

        try:
            result = await original(
                db,
                spot_id=spot_id,
                cancellation_fee=cancellation_fee,
                fee_address=fee_address,
            )
        except ValueError:
            # Validation failures happen before chain submission and are safe to
            # correct and retry normally.
            await _release_guard(db, spot_id=spot_id)
            raise
        except Exception as exc:
            await _block_guard(
                db,
                spot_id=spot_id,
                reason=f"cancellation raised after lease acquisition: {type(exc).__name__}",
            )
            raise

        if str(result.get("reason") or "") == "send_retry_pending":
            await _block_guard(
                db,
                spot_id=spot_id,
                reason="cancellation helper returned an ambiguous send result",
            )
            return _blocked_result(
                spot_id=spot_id,
                reason="cancellation helper returned an ambiguous send result",
            )

        # Benign blockers, completed cancellations and successfully recorded
        # pending transactions are all represented durably by the existing
        # Spot/TRANSACTION records, so the temporary send lease can be released.
        await _release_guard(db, spot_id=spot_id)
        return result


def install() -> None:
    """Install the guard once for HTTP routes and background settlement alike."""
    global _INSTALLED, _ORIGINAL_SUBMIT
    if _INSTALLED:
        return
    _ORIGINAL_SUBMIT = trans_updater.submit_spot_cancellation_transactions
    trans_updater.submit_spot_cancellation_transactions = (
        guarded_submit_spot_cancellation_transactions
    )
    _INSTALLED = True


__all__ = ["guarded_submit_spot_cancellation_transactions", "install"]
