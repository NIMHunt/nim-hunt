"""
settlement_updater.py

Prizedraw and claim-settlement workflow for NimHunt.

This module deliberately does not talk to wallet.py directly. When settlement
needs to create a reward payment, it calls trans_updater.py so chain-facing
behaviour stays in one place.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress
from typing import Any

import constants as const
import database as schema
from database import get_db

import cache
import db_access
import trans_updater


RowDict = dict[str, Any]

DEFAULT_SETTLEMENT_INTERVAL_SECONDS = int(getattr(const, "SETTLEMENT_INTERVAL_SECONDS", 60))
DEFAULT_MAX_SETTLEMENTS_PER_RUN = int(getattr(const, "MAX_SETTLEMENTS_PER_RUN", 50))
DEFAULT_MAX_DURATION_CLAIMS_PER_RUN = int(getattr(const, "MAX_DURATION_CLAIMS_PER_RUN", 200))

_SETTLEMENT_TASK: asyncio.Task | None = None
_SETTLEMENT_STOP_EVENT: asyncio.Event | None = None
_SETTLEMENT_LAST_RESULT: RowDict | None = None
_SETTLEMENT_LAST_ERROR: str | None = None


async def _get_unixepoch(db) -> int:
    return await db_access.get_unixepoch(db)


def _spot_absolute_ends_at(spot: RowDict) -> int | None:
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    ends_after = spot.get(schema.SPOT_ENDS_AT)
    if starts_at is None or ends_after is None:
        return None
    return int(starts_at) + int(ends_after)


def _prize_amounts(*, total_value: int, prize_count: int, winner_count: int) -> list[int]:
    """Return prize amounts, putting any indivisible remainder on first prize."""
    winner_count = max(0, int(winner_count))
    if winner_count <= 0:
        return []

    prize_count = max(1, int(prize_count))
    base = int(total_value) // prize_count
    remainder = int(total_value) % prize_count

    amounts = [base for _ in range(winner_count)]
    if amounts:
        amounts[0] += remainder
    return amounts


async def _ready_prizedraw_spot_ids(db, *, limit: int = DEFAULT_MAX_SETTLEMENTS_PER_RUN) -> list[int]:
    """Return published Prizedraw spots whose draw should now be settled."""
    rows = await db.execute_fetchall(
        f"""
        WITH claim_counts AS (
            SELECT
                {schema.CLAIM_SPOT_ID} AS spot_id,
                SUM(CASE WHEN {schema.CLAIM_STATUS} = {const.CLAIM_STATUS_PENDING} THEN 1 ELSE 0 END) AS pending_claim_count,
                SUM(CASE WHEN {schema.CLAIM_STATUS} = {const.CLAIM_STATUS_SUCCESS} THEN 1 ELSE 0 END) AS success_claim_count
            FROM {schema.CLAIM_TABLE_NAME}
            GROUP BY {schema.CLAIM_SPOT_ID}
        )
        SELECT s.{schema.SPOT_ID} AS spot_id
        FROM {schema.SPOT_TABLE_NAME} s
        JOIN {schema.PRIZEDRAW_TABLE_NAME} pd
            ON pd.{schema.PRIZEDRAW_SPOT_ID} = s.{schema.SPOT_ID}
        LEFT JOIN claim_counts cc
            ON cc.spot_id = s.{schema.SPOT_ID}
        WHERE s.{schema.SPOT_STATUS} = ?
          AND (
                (
                    s.{schema.SPOT_MAX_TOTAL_CLAIMS} > 0
                    AND (
                        COALESCE(cc.pending_claim_count, 0)
                        + COALESCE(cc.success_claim_count, 0)
                    ) >= s.{schema.SPOT_MAX_TOTAL_CLAIMS}
                )
                OR (
                    s.{schema.SPOT_STARTS_AT} IS NOT NULL
                    AND (s.{schema.SPOT_STARTS_AT} + s.{schema.SPOT_ENDS_AT}) <= unixepoch()
                )
          )
        ORDER BY s.{schema.SPOT_STARTS_AT} ASC, s.{schema.SPOT_ID} ASC
        LIMIT ?;
        """,
        (const.SPOT_STATUS_PUBLISHED, max(1, int(limit))),
    )
    return [int(row["spot_id"]) for row in rows]


async def _settlement_ready_reason(db, *, spot: RowDict, now: int) -> str | None:
    """Return the reason a Prizedraw is ready, or None if it is not."""
    spot_id = int(spot[schema.SPOT_ID])
    pending_count = await db_access.count_claims_by_status_for_spot(
        db,
        spot_id=spot_id,
        status=const.CLAIM_STATUS_PENDING,
    )
    success_count = await db_access.count_claims_by_status_for_spot(
        db,
        spot_id=spot_id,
        status=const.CLAIM_STATUS_SUCCESS,
    )
    max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if max_total > 0 and pending_count + success_count >= max_total:
        return "capacity_reached"

    ends_at = _spot_absolute_ends_at(spot)
    if ends_at is not None and ends_at <= int(now):
        return "spot_ended"

    return None


async def settle_prizedraw_spot_if_ready(*, spot_id: int) -> RowDict:
    """Settle one Prizedraw if its capacity or end-time trigger has fired."""
    spot_id = int(spot_id)

    try:
        async with get_db() as db:
            async with db_access.transaction(db):
                spot = await db_access.get_spot(db, spot_id=spot_id)
                if spot is None:
                    return {"ok": False, "spot_id": spot_id, "reason": "spot_missing"}
                if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_PUBLISHED:
                    return {"ok": True, "spot_id": spot_id, "settled": False, "reason": "not_published"}
                if not await db_access.is_prizedraw(db, spot_id=spot_id):
                    return {"ok": True, "spot_id": spot_id, "settled": False, "reason": "not_prizedraw"}

                now = await _get_unixepoch(db)
                ready_reason = await _settlement_ready_reason(db, spot=spot, now=now)
                if ready_reason is None:
                    return {"ok": True, "spot_id": spot_id, "settled": False, "reason": "not_ready"}

                # Pending duration entries that have not passed by draw time are
                # excluded from the draw by failing them first.
                failed_pending_count = await db_access.fail_pending_claims_for_spot(db, spot_id=spot_id)

                successful_claims = await db_access.get_successful_claims_for_spot(db, spot_id=spot_id)
                prizedraw = await db_access.get_prizedraw(db, spot_id=spot_id)
                configured_prize_count = int(prizedraw.get(schema.PRIZEDRAW_PRIZE_COUNT) if prizedraw else 1)
                winner_count = min(max(1, configured_prize_count), len(successful_claims))

                rng = secrets.SystemRandom()
                winners = rng.sample(successful_claims, winner_count) if winner_count > 0 else []
                amounts = _prize_amounts(
                    total_value=int(spot.get(schema.SPOT_TOTAL_VALUE) or 0),
                    prize_count=max(1, configured_prize_count),
                    winner_count=len(winners),
                )

                payout_results: list[RowDict] = []
                for claim, amount in zip(winners, amounts):
                    if int(amount) <= 0:
                        continue
                    payout_results.append(
                        await trans_updater.submit_claim_reward_transaction(
                            db,
                            claim_id=int(claim[schema.CLAIM_ID]),
                            amount=int(amount),
                        )
                    )

                await db_access.set_spot_status_to_completed(db, spot_id=spot_id)

            await cache.notify_spot_changed(db, spot_id=spot_id)
            owner_id = int(spot.get(schema.SPOT_CREATED_BY) or 0)
            if owner_id:
                await cache.notify_user_changed(db, user_id=owner_id)

        return {
            "ok": True,
            "spot_id": spot_id,
            "settled": True,
            "reason": ready_reason,
            "failed_pending_count": failed_pending_count,
            "eligible_claim_count": len(successful_claims),
            "winner_count": len(winners),
            "winner_claim_ids": [int(claim[schema.CLAIM_ID]) for claim in winners],
            "payouts": payout_results,
        }
    except Exception as exc:
        return {"ok": False, "spot_id": spot_id, "settled": False, "reason": repr(exc)}


async def settle_ready_prizedraws(*, max_settlements: int = DEFAULT_MAX_SETTLEMENTS_PER_RUN) -> RowDict:
    """Settle all currently ready Prizedraw spots, up to max_settlements."""
    results: list[RowDict] = []
    async with get_db() as db:
        spot_ids = await _ready_prizedraw_spot_ids(db, limit=int(max_settlements))

    for spot_id in spot_ids:
        results.append(await settle_prizedraw_spot_if_ready(spot_id=int(spot_id)))

    return {
        "ok": all(bool(result.get("ok")) for result in results),
        "checked_count": len(spot_ids),
        "settled_count": sum(1 for result in results if result.get("settled")),
        "failed_count": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


async def settle_pending_duration_claims(*, max_claims: int = DEFAULT_MAX_DURATION_CLAIMS_PER_RUN) -> RowDict:
    """Fail stale duration claims and complete duration claims whose wait has passed."""
    async with get_db() as db:
        claim_ids = await db_access.get_pending_duration_claim_ids(db, limit=int(max_claims))

    results: list[RowDict] = []
    for claim_id in claim_ids:
        async with get_db() as db:
            async with db_access.transaction(db):
                before = await db_access.get_claim(db, claim_id=int(claim_id))
                after = await db_access.refresh_claim_status_from_conditions(db, claim_id=int(claim_id))

            if before is None or after is None:
                results.append({"claim_id": int(claim_id), "changed": False, "reason": "missing"})
                continue

            changed = int(before[schema.CLAIM_STATUS]) != int(after[schema.CLAIM_STATUS])
            cleanup = after.get("capacity_cleanup") if isinstance(after, dict) else None
            cleanup_failed_count = int(cleanup.get("failed_count") or 0) if isinstance(cleanup, dict) else 0

            if changed or cleanup_failed_count > 0:
                with suppress(Exception):
                    # A capacity cleanup can fail other users' pending claims,
                    # so mark the broader claim/user cache dirty when it fires.
                    await cache.notify_claim_changed(
                        db,
                        spot_id=int(after[schema.CLAIM_SPOT_ID]),
                        user_id=None if cleanup_failed_count > 0 else int(after[schema.CLAIM_RECIPIENT]),
                    )

            results.append({
                "claim_id": int(claim_id),
                "changed": changed,
                "before_status": int(before[schema.CLAIM_STATUS]),
                "after_status": int(after[schema.CLAIM_STATUS]),
                "capacity_cleanup": cleanup,
            })

    return {
        "ok": True,
        "checked_count": len(claim_ids),
        "changed_count": sum(1 for result in results if result.get("changed")),
        "results": results,
    }


async def run_settlement_pass() -> RowDict:
    """Run all app-level settlement work once."""
    duration_result = await settle_pending_duration_claims()
    prizedraw_result = await settle_ready_prizedraws()
    return {
        "ok": bool(duration_result.get("ok")) and bool(prizedraw_result.get("ok")),
        "duration_claims": duration_result,
        "prizedraws": prizedraw_result,
    }


async def _settlement_loop(interval_seconds: int) -> None:
    global _SETTLEMENT_LAST_RESULT, _SETTLEMENT_LAST_ERROR

    assert _SETTLEMENT_STOP_EVENT is not None
    while not _SETTLEMENT_STOP_EVENT.is_set():
        try:
            _SETTLEMENT_LAST_RESULT = await run_settlement_pass()
            _SETTLEMENT_LAST_ERROR = None
        except Exception as exc:  # pragma: no cover - defensive loop guard
            _SETTLEMENT_LAST_ERROR = repr(exc)

        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_SETTLEMENT_STOP_EVENT.wait(), timeout=max(1, int(interval_seconds)))


async def start_settlement_refresher(
    *,
    run_immediately: bool = False,
    interval_seconds: int = DEFAULT_SETTLEMENT_INTERVAL_SECONDS,
) -> None:
    """Start the lightweight background settlement loop once."""
    global _SETTLEMENT_TASK, _SETTLEMENT_STOP_EVENT, _SETTLEMENT_LAST_RESULT, _SETTLEMENT_LAST_ERROR

    if _SETTLEMENT_TASK is not None and not _SETTLEMENT_TASK.done():
        return

    _SETTLEMENT_STOP_EVENT = asyncio.Event()

    if run_immediately:
        try:
            _SETTLEMENT_LAST_RESULT = await run_settlement_pass()
            _SETTLEMENT_LAST_ERROR = None
        except Exception as exc:  # pragma: no cover - startup should not fail app boot
            _SETTLEMENT_LAST_ERROR = repr(exc)

    _SETTLEMENT_TASK = asyncio.create_task(_settlement_loop(int(interval_seconds)))


async def stop_settlement_refresher() -> None:
    """Stop the background settlement loop if it is running."""
    global _SETTLEMENT_TASK, _SETTLEMENT_STOP_EVENT

    if _SETTLEMENT_STOP_EVENT is not None:
        _SETTLEMENT_STOP_EVENT.set()

    if _SETTLEMENT_TASK is not None:
        _SETTLEMENT_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await _SETTLEMENT_TASK

    _SETTLEMENT_TASK = None
    _SETTLEMENT_STOP_EVENT = None


async def run_once() -> RowDict:
    return await run_settlement_pass()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), indent=2))
