"""Global claim-payout throughput guard.

Even strong per-user checks can be evaded by a sufficiently determined Sybil
attacker. This guard limits how quickly NimHunt can automatically send claim
rewards in aggregate. It never rejects or fails a CLAIM: excess payouts remain
in the existing settlement queue and are retried after the rolling window.

The defaults are intentionally generous for normal use but bound the damage of
a fast scripted sweep. Operators can tune both limits with environment variables
without changing code.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

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
MAX_PAYOUT_COUNT = _env_int("NIMHUNT_CLAIM_PAYOUT_THROTTLE_MAX_COUNT", 30)
MAX_PAYOUT_NIM = _env_int("NIMHUNT_CLAIM_PAYOUT_THROTTLE_MAX_NIM", 25_000)
MAX_PAYOUT_LUNA = MAX_PAYOUT_NIM * int(getattr(const, "LUNA_PER_NIM", 100_000))

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


async def submit_claim_reward_transaction_with_throttle(
    db,
    *,
    claim_id: int,
    amount: int,
    to_address: str | None = None,
) -> RowDict:
    """Defer aggregate payout bursts while preserving normal settlement retry."""
    state = await payout_window_state(db)
    decision = throttle_decision(state=state, amount=int(amount))
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

    delegate = _DELEGATE
    if delegate is None:  # pragma: no cover - install() is required in runtime
        raise RuntimeError("claim payout throttle is not installed")
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
    "WINDOW_SECONDS",
    "install",
    "payout_window_state",
    "submit_claim_reward_transaction_with_throttle",
    "throttle_decision",
]
