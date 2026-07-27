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
import wallet

RowDict = dict[str, Any]
CancellationSubmitter = Callable[..., Awaitable[RowDict]]

_GUARD_TABLE = "CANCELLATION_SEND_GUARD"
_POLICY_TABLE = "CANCELLATION_POLICY"
_RETRYABLE_FAILED_REFUND_GUARD_REASON = "existing failed or ambiguous cancellation transaction"
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
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_POLICY_TABLE} (
            spot_id INTEGER PRIMARY KEY,
            fee_amount INTEGER NOT NULL CHECK (fee_amount >= 0),
            fee_address TEXT NOT NULL CHECK (TRIM(fee_address) != ''),
            created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            FOREIGN KEY (spot_id)
                REFERENCES {schema.SPOT_TABLE_NAME}({schema.SPOT_ID})
                ON DELETE CASCADE
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



async def _policy_row(db, *, spot_id: int) -> RowDict | None:
    cur = await db.execute(
        f"SELECT * FROM {_POLICY_TABLE} WHERE spot_id = ?;",
        (int(spot_id),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _snapshot_cancellation_policy(
    db,
    *,
    spot_id: int,
    cancellation_fee: int | None,
    fee_address: str | None,
) -> tuple[RowDict, bool]:
    existing = await _policy_row(db, spot_id=int(spot_id))
    if existing is not None:
        return existing, False

    fee_amount = max(
        0,
        int(
            getattr(const, "SPOT_CANCELLATION_FEE", 0)
            if cancellation_fee is None
            else cancellation_fee
        ),
    )
    configured_address = str(
        fee_address
        or getattr(const, "SPOT_FEE_ADDRESS", "")
    ).strip()
    clean_address = wallet.normalise_nimiq_address(
        configured_address,
        field_name="cancellation fee address",
        allow_dev_placeholder=bool(
            getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)
        ),
    )

    cur = await db.execute(
        f"""
        INSERT OR IGNORE INTO {_POLICY_TABLE} (
            spot_id,
            fee_amount,
            fee_address
        ) VALUES (?, ?, ?);
        """,
        (int(spot_id), fee_amount, clean_address),
    )
    created = int(cur.rowcount or 0) == 1
    await db.commit()
    policy = await _policy_row(db, spot_id=int(spot_id))
    if policy is None:
        raise RuntimeError("cancellation policy could not be persisted")
    return policy, created


async def _delete_cancellation_policy(db, *, spot_id: int) -> None:
    await db.execute(
        f"DELETE FROM {_POLICY_TABLE} WHERE spot_id = ?;",
        (int(spot_id),),
    )
    await db.commit()


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


def _single_retryable_failed_refund(transactions: list[RowDict]) -> RowDict | None:
    """Return the sole failed refund eligible for one proven-safe retry."""
    failed_refunds = [
        row for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CANCEL_SPOT
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        == const.TRANS_STATUS_FAILED
    ]
    active_refunds = [
        row for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CANCEL_SPOT
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        != const.TRANS_STATUS_FAILED
    ]
    failed_fees = [
        row for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_PLAT_FEE
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        == const.TRANS_STATUS_FAILED
    ]
    if len(failed_refunds) != 1 or active_refunds or failed_fees:
        return None
    return failed_refunds[0]


async def _failed_refund_is_definitively_failed(row: RowDict) -> bool:
    """Re-check that a stored failed refund really executed unsuccessfully."""
    tx_hash = str(row.get(schema.TRANS_TX_HASH) or "").strip()
    if not trans_updater._NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        return False
    status = await trans_updater.get_chain_transaction_status(tx_hash)
    return (
        status.status == "failed"
        and trans_updater._execution_result_is_failure(status.raw)
    )


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

        transactions = await db_access.get_transactions_by_spot(
            db,
            spot_id=spot_id,
            limit=db_access.MAX_LIMIT,
        )
        retryable_refund = _single_retryable_failed_refund(transactions)
        refund_failure_proven = False
        if retryable_refund is not None:
            try:
                refund_failure_proven = await _failed_refund_is_definitively_failed(
                    retryable_refund
                )
            except Exception:
                refund_failure_proven = False

        existing_guard = await _guard_row(db, spot_id=spot_id)
        if existing_guard is not None:
            guard_reason = str(existing_guard.get("reason") or existing_guard.get("state"))
            recoverable_failed_refund_guard = (
                str(existing_guard.get("state") or "") == "blocked"
                and guard_reason == _RETRYABLE_FAILED_REFUND_GUARD_REASON
                and refund_failure_proven
            )
            if recoverable_failed_refund_guard:
                await _release_guard(db, spot_id=spot_id)
            else:
                return _blocked_result(spot_id=spot_id, reason=guard_reason)

        failed_legs = _failed_cancellation_legs(transactions)
        if failed_legs and not refund_failure_proven:
            await _block_guard(
                db,
                spot_id=spot_id,
                reason=_RETRYABLE_FAILED_REFUND_GUARD_REASON,
            )
            return _blocked_result(
                spot_id=spot_id,
                reason=_RETRYABLE_FAILED_REFUND_GUARD_REASON,
            )

        policy, policy_created = await _snapshot_cancellation_policy(
            db,
            spot_id=spot_id,
            cancellation_fee=cancellation_fee,
            fee_address=fee_address,
        )

        if not await _acquire_guard(db, spot_id=spot_id):
            current = await _guard_row(db, spot_id=spot_id)
            return _blocked_result(
                spot_id=spot_id,
                reason=str(
                    (current or {}).get("reason")
                    or "another cancellation worker acquired the lease"
                ),
            )

        try:
            result = await original(
                db,
                spot_id=spot_id,
                cancellation_fee=int(policy["fee_amount"]),
                fee_address=str(policy["fee_address"]),
            )
        except ValueError:
            # Validation failures happen before chain submission and are safe to
            # correct and retry normally. A policy created solely for that invalid
            # attempt is removed so a corrected request can snapshot fresh values.
            await _release_guard(db, spot_id=spot_id)
            if policy_created:
                await _delete_cancellation_policy(db, spot_id=spot_id)
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
