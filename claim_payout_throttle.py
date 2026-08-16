"""Global claim-payout throughput guard.

Even strong per-user checks can be evaded by a sufficiently determined Sybil
attacker. This guard limits how quickly NimHunt can automatically send claim
rewards in aggregate. It never rejects or fails a CLAIM: excess payouts remain
in the existing settlement queue and are retried after the rolling window.

The throttle uses durable SQLite reservations in addition to TRANSACTION rows.
That matters because several HTTP/background settlement tasks can reach the
payout boundary concurrently: a read-then-send limit would let every task see
the same remaining slot before any of them creates a transaction. Reserving a
slot under BEGIN IMMEDIATE makes the blast-radius limit atomic across workers
that share the deployment database, without holding a database lock while a
Nimiq transaction is broadcast.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

import claim_security
import constants as const
import database as schema
import db_access
import trans_updater

RowDict = dict[str, Any]
SubmitClaimReward = Callable[..., Awaitable[RowDict]]


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < int(minimum):
        raise ValueError(f"{name} must be at least {minimum}")
    return value


WINDOW_SECONDS = _env_int("NIMHUNT_CLAIM_PAYOUT_THROTTLE_WINDOW_SECONDS", 10 * 60)
MAX_PAYOUT_COUNT = _env_int("NIMHUNT_CLAIM_PAYOUT_THROTTLE_MAX_COUNT", 8)
MAX_PAYOUT_NIM = _env_int("NIMHUNT_CLAIM_PAYOUT_THROTTLE_MAX_NIM", 10_000)
MAX_PAYOUT_LUNA = MAX_PAYOUT_NIM * int(getattr(const, "LUNA_PER_NIM", 100_000))

RESERVATION_KEY = f"{claim_security.METADATA_PREFIX}payout_throttle_reservations"

_DELEGATE: SubmitClaimReward | None = None
_INSTALLED = False


async def payout_window_state(db, *, now: int | None = None) -> RowDict:
    """Return active non-failed claim-payout intents in the rolling window."""
    if now is None:
        now = await db_access.get_unixepoch(db)
    cutoff = int(now) - WINDOW_SECONDS
    cur = await db.execute(
        f"""
        SELECT
            COUNT(*) AS payout_count,
            COALESCE(SUM({schema.TRANS_AMOUNT}), 0) AS payout_amount,
            MIN({schema.TRANS_CREATED_AT}) AS oldest_created_at
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} != ?
          AND {schema.TRANS_CREATED_AT} > ?;
        """,
        (
            const.TRANS_TYPE_CLAIM,
            const.TRANS_STATUS_FAILED,
            cutoff,
        ),
    )
    row = await cur.fetchone()
    return {
        "now": int(now),
        "cutoff": cutoff,
        "payout_count": int(row["payout_count"] or 0) if row is not None else 0,
        "payout_amount": int(row["payout_amount"] or 0) if row is not None else 0,
        "oldest_created_at": (
            int(row["oldest_created_at"])
            if row is not None and row["oldest_created_at"] is not None
            else None
        ),
    }


def throttle_decision(*, state: RowDict, amount: int) -> RowDict:
    """Return whether one more payout may be submitted automatically."""
    amount = max(0, int(amount))
    payout_count = max(0, int(state.get("payout_count") or 0))
    payout_amount = max(0, int(state.get("payout_amount") or 0))
    now = int(state.get("now") or 0)
    oldest = state.get("oldest_created_at")
    retry_at = (
        int(oldest) + WINDOW_SECONDS + 1
        if oldest is not None
        else now + WINDOW_SECONDS
    )

    if payout_count >= MAX_PAYOUT_COUNT:
        return {
            "allow": False,
            "reason": "global_payout_count_limit",
            "retry_at": retry_at,
            "window_payout_count": payout_count,
            "window_payout_amount": payout_amount,
        }

    # Never deadlock a single legitimate large prize merely because it exceeds
    # the rolling aggregate cap on its own. Once one payout exists in the
    # window, however, the aggregate amount cap applies normally.
    if payout_count > 0 and payout_amount + amount > MAX_PAYOUT_LUNA:
        return {
            "allow": False,
            "reason": "global_payout_amount_limit",
            "retry_at": retry_at,
            "window_payout_count": payout_count,
            "window_payout_amount": payout_amount,
        }

    return {
        "allow": True,
        "reason": "within_global_payout_limits",
        "window_payout_count": payout_count,
        "window_payout_amount": payout_amount,
    }


def _clean_reservations(raw: Any, *, now: int) -> list[RowDict]:
    """Return well-formed, still-active payout reservations."""
    if not isinstance(raw, list):
        return []
    cutoff = int(now) - WINDOW_SECONDS
    clean: list[RowDict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            claim_id = int(item.get("claim_id") or 0)
            amount = int(item.get("amount") or 0)
            reserved_at = int(item.get("reserved_at") or 0)
        except (TypeError, ValueError):
            continue
        if claim_id <= 0 or amount <= 0 or reserved_at <= cutoff:
            continue
        clean.append(
            {
                "claim_id": claim_id,
                "amount": amount,
                "reserved_at": reserved_at,
            }
        )
    return clean


async def _recent_payout_rows(db, *, now: int) -> list[RowDict]:
    cutoff = int(now) - WINDOW_SECONDS
    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.TRANS_ID} AS trans_id,
            {schema.TRANS_CLAIM_ID} AS claim_id,
            {schema.TRANS_AMOUNT} AS amount,
            {schema.TRANS_CREATED_AT} AS created_at
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} != ?
          AND {schema.TRANS_CREATED_AT} > ?;
        """,
        (
            const.TRANS_TYPE_CLAIM,
            const.TRANS_STATUS_FAILED,
            cutoff,
        ),
    )
    return [dict(row) for row in rows]


def _combined_window_state(
    *,
    now: int,
    payout_rows: list[RowDict],
    reservations: list[RowDict],
) -> tuple[RowDict, set[int]]:
    """Count materialised payouts plus reservations not yet in TRANS."""
    transaction_claim_ids: set[int] = set()
    timestamps: list[int] = []
    payout_count = 0
    payout_amount = 0

    for row in payout_rows:
        payout_count += 1
        payout_amount += max(0, int(row.get("amount") or 0))
        created_at = int(row.get("created_at") or now)
        timestamps.append(created_at)
        claim_id = int(row.get("claim_id") or 0)
        if claim_id > 0:
            transaction_claim_ids.add(claim_id)

    # Once a reservation has materialised as a TRANSACTION row it must not be
    # counted twice. Until then the reservation is what closes the concurrency
    # gap between the limit check and durable transaction-intent insertion.
    for reservation in reservations:
        claim_id = int(reservation["claim_id"])
        if claim_id in transaction_claim_ids:
            continue
        payout_count += 1
        payout_amount += max(0, int(reservation["amount"]))
        timestamps.append(int(reservation["reserved_at"]))

    return (
        {
            "now": int(now),
            "cutoff": int(now) - WINDOW_SECONDS,
            "payout_count": payout_count,
            "payout_amount": payout_amount,
            "oldest_created_at": min(timestamps) if timestamps else None,
        },
        transaction_claim_ids,
    )


async def reserve_payout_slot(
    db,
    *,
    claim_id: int,
    amount: int,
) -> RowDict:
    """Atomically reserve one rolling-window payout slot for a claim.

    The normal runtime calls this with a fresh connection. If a future caller
    already has a transaction open, an INSERT-or-ignore write acquires SQLite's
    writer lock before we inspect the shared window, preserving the same
    serialisation property without nesting BEGIN statements.
    """
    claim_id = int(claim_id)
    amount = int(amount)
    if claim_id <= 0:
        raise ValueError("claim_id must be positive")
    if amount <= 0:
        raise ValueError("amount must be positive")

    owns_transaction = not bool(getattr(db, "in_transaction", False))
    try:
        if owns_transaction:
            await db.execute("BEGIN IMMEDIATE;")
        else:
            # This harmless write acquires the SQLite writer reservation before
            # any throttle reads when the caller already owns a deferred tx.
            await db.execute(
                f"""
                INSERT INTO {schema.APP_METADATA_TABLE_NAME} (
                    {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}
                ) VALUES (?, '[]')
                ON CONFLICT ({schema.APP_METADATA_KEY}) DO NOTHING;
                """,
                (RESERVATION_KEY,),
            )

        now = await db_access.get_unixepoch(db)
        raw = await claim_security._metadata_get(db, RESERVATION_KEY)
        reservations = _clean_reservations(raw, now=now)
        payout_rows = await _recent_payout_rows(db, now=now)
        state, transaction_claim_ids = _combined_window_state(
            now=now,
            payout_rows=payout_rows,
            reservations=reservations,
        )

        if claim_id in transaction_claim_ids:
            decision: RowDict = {
                "allow": True,
                "reason": "claim_payout_already_materialised",
                "window_payout_count": int(state["payout_count"]),
                "window_payout_amount": int(state["payout_amount"]),
                "reservation_reused": True,
            }
        else:
            existing = next(
                (
                    reservation
                    for reservation in reservations
                    if int(reservation["claim_id"]) == claim_id
                ),
                None,
            )
            if existing is not None:
                decision = {
                    "allow": True,
                    "reason": "existing_payout_reservation",
                    "window_payout_count": int(state["payout_count"]),
                    "window_payout_amount": int(state["payout_amount"]),
                    "reservation_reused": True,
                }
            else:
                decision = throttle_decision(state=state, amount=amount)
                if bool(decision.get("allow")):
                    reservations.append(
                        {
                            "claim_id": claim_id,
                            "amount": amount,
                            "reserved_at": int(now),
                        }
                    )
                    decision = {
                        **decision,
                        "reservation_created": True,
                    }

        # Save even when blocked so expired/malformed reservations are pruned.
        await claim_security._metadata_set(db, RESERVATION_KEY, reservations)
        if owns_transaction:
            await db.commit()
        return decision
    except Exception:
        if owns_transaction:
            await db.rollback()
        raise


async def submit_claim_reward_transaction_with_throttle(
    db,
    *,
    claim_id: int,
    amount: int,
    to_address: str | None = None,
) -> RowDict:
    """Defer aggregate payout bursts while preserving normal settlement retry."""
    delegate = _DELEGATE
    if delegate is None:  # pragma: no cover - install() is required in runtime
        raise RuntimeError("claim payout throttle is not installed")

    # The security wrapper is inside this throttle. Do not consume a scarce
    # global payout slot while a claim is still in its observation/manual-review
    # hold; the inner wrapper remains authoritative and rechecks before sending.
    security_decision = await claim_security._payout_security_decision(
        db,
        claim_id=int(claim_id),
    )
    if not bool(security_decision.get("allow")):
        return await delegate(
            db,
            claim_id=int(claim_id),
            amount=int(amount),
            to_address=to_address,
        )

    decision = await reserve_payout_slot(
        db,
        claim_id=int(claim_id),
        amount=int(amount),
    )
    if not bool(decision.get("allow")):
        return {
            "ok": True,
            "claim_id": int(claim_id),
            "paid": False,
            "skipped": True,
            "deferred": True,
            "payout_throttle": True,
            **decision,
        }

    return await delegate(
        db,
        claim_id=int(claim_id),
        amount=int(amount),
        to_address=to_address,
    )


def install() -> None:
    """Wrap the current claim payout submitter without bypassing prior guards."""
    global _DELEGATE, _INSTALLED
    if _INSTALLED:
        return
    _DELEGATE = trans_updater.submit_claim_reward_transaction
    trans_updater.submit_claim_reward_transaction = submit_claim_reward_transaction_with_throttle
    _INSTALLED = True


__all__ = [
    "MAX_PAYOUT_COUNT",
    "MAX_PAYOUT_LUNA",
    "MAX_PAYOUT_NIM",
    "RESERVATION_KEY",
    "WINDOW_SECONDS",
    "install",
    "payout_window_state",
    "reserve_payout_slot",
    "submit_claim_reward_transaction_with_throttle",
    "throttle_decision",
]
