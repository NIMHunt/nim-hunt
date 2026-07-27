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
import logging
import secrets
from contextlib import suppress
from typing import Any

import cache
import constants as const
import database as schema
import db_access
import trans_updater
from database import get_db

RowDict = dict[str, Any]

logger = logging.getLogger(__name__)

DEFAULT_SETTLEMENT_INTERVAL_SECONDS = int(getattr(const, "SETTLEMENT_INTERVAL_SECONDS", 60))
DEFAULT_MAX_SETTLEMENTS_PER_RUN = int(getattr(const, "MAX_SETTLEMENTS_PER_RUN", 50))
DEFAULT_MAX_DURATION_CLAIMS_PER_RUN = int(getattr(const, "MAX_DURATION_CLAIMS_PER_RUN", 200))
DEFAULT_MAX_STANDARD_PAYOUTS_PER_RUN = int(getattr(const, "MAX_STANDARD_PAYOUTS_PER_RUN", 200))
DEFAULT_MAX_REMAINDER_REFUNDS_PER_RUN = int(getattr(const, "MAX_REMAINDER_REFUNDS_PER_RUN", 50))

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
    """Return Prizedraw payouts for the actual selected winners.

    If fewer eligible winners exist than configured prize slots, the full prize
    pool is paid across the actual winners. Any indivisible Luna remainder goes
    to the first deterministic winner.
    """
    winner_count = max(0, int(winner_count))
    if winner_count <= 0:
        return []

    # prize_count is still accepted for call-site clarity and coerced here to
    # keep invalid historical values from bubbling out of this helper. Payout
    # division must use actual winners so undersubscribed Prizedraws pay the
    # full pool.
    prize_count = max(1, int(prize_count))
    base = int(total_value) // winner_count
    remainder = int(total_value) % winner_count

    amounts = [base for _ in range(winner_count)]
    amounts[0] += remainder
    return amounts


def _prize_amounts_by_claim_id(*, total_value: int, prize_count: int, winner_claim_ids: list[int]) -> dict[int, int]:
    """Return deterministic payout amounts for a persisted winner set."""
    clean_ids = sorted({int(claim_id) for claim_id in winner_claim_ids if int(claim_id) > 0})
    amounts = _prize_amounts(
        total_value=int(total_value),
        prize_count=max(1, int(prize_count)),
        winner_count=len(clean_ids),
    )
    return {
        claim_id: int(amount)
        for claim_id, amount in zip(clean_ids, amounts, strict=True)
    }


def _standard_claim_payout_amount(spot: RowDict) -> int:
    """Return the fixed Luna reward for one standard Spot claim."""
    max_total_claims = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if max_total_claims <= 0:
        return 0
    return int(spot.get(schema.SPOT_TOTAL_VALUE) or 0) // max_total_claims


async def payout_standard_claim_if_ready(*, claim_id: int) -> RowDict:
    """Create/broadcast the missing payout for one successful standard claim.

    The query and the TRANSACTION partial unique index make this safe to call
    from the HTTP route and background recovery workers at the same time.
    """
    claim_id = int(claim_id)
    try:
        async with get_db() as db:
            claim = await db_access.get_claim(db, claim_id=claim_id)
            if claim is None:
                return {"ok": False, "claim_id": claim_id, "paid": False, "reason": "claim_missing"}
            if int(claim.get(schema.CLAIM_STATUS) or -1) != const.CLAIM_STATUS_SUCCESS:
                return {"ok": True, "claim_id": claim_id, "paid": False, "reason": "claim_not_successful"}

            spot = await db_access.get_spot(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
            if spot is None:
                return {"ok": False, "claim_id": claim_id, "paid": False, "reason": "spot_missing"}
            if await db_access.is_prizedraw(db, spot_id=int(spot[schema.SPOT_ID])):
                return {"ok": True, "claim_id": claim_id, "paid": False, "reason": "prizedraw_managed_separately"}
            if await db_access.has_nonfailed_claim_payout_transaction(db, claim_id=claim_id):
                return {"ok": True, "claim_id": claim_id, "paid": False, "already_exists": True}

            retry_amount = await db_access.latest_failed_claim_payout_amount(db, claim_id=claim_id)
            amount = int(retry_amount or _standard_claim_payout_amount(spot))
            if amount <= 0:
                return {"ok": False, "claim_id": claim_id, "paid": False, "reason": "invalid_payout_amount"}

        async with get_db() as send_db:
            try:
                result = await trans_updater.submit_claim_reward_transaction(
                    send_db,
                    claim_id=claim_id,
                    amount=amount,
                )
            except RuntimeError as exc:
                # Only a uniqueness-guard failure proves that another worker
                # won the race. A helper/broadcast failure may leave our own
                # local intent pending and must remain visible as an error.
                duplicate_guard_hit = "already has a non-failed payout transaction" in str(exc)
                if (
                    duplicate_guard_hit
                    and await db_access.has_nonfailed_claim_payout_transaction(send_db, claim_id=claim_id)
                ):
                    return {
                        "ok": True,
                        "claim_id": claim_id,
                        "paid": False,
                        "already_exists": True,
                        "reason": "concurrent_payout_already_recorded",
                    }
                raise

        return {
            **result,
            "ok": bool(result.get("ok")),
            "claim_id": claim_id,
            "paid": bool(result.get("ok") and not result.get("already_exists")),
        }
    except Exception as exc:
        return {"ok": False, "claim_id": claim_id, "paid": False, "reason": repr(exc)}


async def retry_unpaid_standard_claim_payouts(
    *,
    max_claims: int = DEFAULT_MAX_STANDARD_PAYOUTS_PER_RUN,
) -> RowDict:
    """Recover successful standard claims left unpaid by a crash or send failure."""
    async with get_db() as db:
        claim_ids = await db_access.get_unpaid_successful_standard_claim_ids(
            db,
            limit=int(max_claims),
        )

    results = [await payout_standard_claim_if_ready(claim_id=claim_id) for claim_id in claim_ids]
    return {
        "ok": all(bool(result.get("ok")) for result in results),
        "checked_count": len(claim_ids),
        "submitted_count": sum(1 for result in results if result.get("paid")),
        "failed_count": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


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
    """Close one ready Prizedraw and persist its winner set.

    A completed Prizedraw now uses CLAIM status this way:
    - SUCCESS without a payout transaction: valid losing entry
    - PENDING on a completed Prizedraw: selected winner awaiting payout confirmation
    - SUCCESS with a confirmed payout transaction: paid winner

    This function commits the draw result before trying to send winner payouts,
    so a send failure cannot cause a later pass to redraw different winners.
    """
    spot_id = int(spot_id)
    spot: RowDict | None = None
    ready_reason: str | None = None
    failed_pending_count = 0
    successful_claims: list[RowDict] = []
    winner_claim_ids: list[int] = []

    try:
        async with get_db() as db:
            # Draw readiness, winner selection and winner persistence are one
            # competing terminal decision. Reserve the write lock before reading
            # eligibility so two settlement workers cannot select against the
            # same stale published state.
            async with db_access.transaction(db, immediate=True):
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
                winner_claim_ids = sorted(int(claim[schema.CLAIM_ID]) for claim in winners)

                persisted = await db_access.mark_prizedraw_winners_pending(
                    db,
                    spot_id=spot_id,
                    winner_claim_ids=winner_claim_ids,
                )
                if int(persisted.get("updated_count") or 0) != len(winner_claim_ids):
                    raise RuntimeError("not all selected Prizedraw winners were persisted")
                await db_access.set_spot_status_to_completed(db, spot_id=spot_id)

            await cache.notify_spot_changed(db, spot_id=spot_id)
            if winner_claim_ids:
                await cache.notify_claim_changed(db, spot_id=spot_id, user_id=None)
            owner_id = int(spot.get(schema.SPOT_CREATED_BY) or 0) if spot is not None else 0
            if owner_id:
                await cache.notify_user_changed(db, user_id=owner_id)

        payout_result = await retry_pending_prizedraw_payouts_for_spot(spot_id=spot_id)

        return {
            "ok": True,
            "spot_id": spot_id,
            "settled": True,
            "reason": ready_reason,
            "failed_pending_count": failed_pending_count,
            "eligible_claim_count": len(successful_claims),
            "winner_count": len(winner_claim_ids),
            "winner_claim_ids": winner_claim_ids,
            "payout_retry": payout_result,
            "payouts": payout_result.get("payouts", []) if isinstance(payout_result, dict) else [],
        }
    except Exception as exc:
        return {"ok": False, "spot_id": spot_id, "settled": False, "reason": repr(exc)}


async def retry_pending_prizedraw_payouts_for_spot(*, spot_id: int) -> RowDict:
    """Retry payout sends for selected winners on one completed Prizedraw."""
    spot_id = int(spot_id)
    try:
        payout_jobs: list[RowDict] = []
        spot: RowDict | None = None

        async with get_db() as db:
            # Reserve before reading payout jobs so concurrent settlement/retry
            # workers wait instead of upgrading a stale read transaction into SQLITE_BUSY.
            async with db_access.transaction(db, immediate=True):
                spot = await db_access.get_spot(db, spot_id=spot_id)
                if spot is None:
                    return {"ok": False, "spot_id": spot_id, "reason": "spot_missing", "payouts": []}
                if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_COMPLETED:
                    return {"ok": True, "spot_id": spot_id, "retried": False, "reason": "not_completed", "payouts": []}
                if not await db_access.is_prizedraw(db, spot_id=spot_id):
                    return {"ok": True, "spot_id": spot_id, "retried": False, "reason": "not_prizedraw", "payouts": []}

                pending_winners = await db_access.get_pending_claims_for_spot(db, spot_id=spot_id)
                if not pending_winners:
                    return {"ok": True, "spot_id": spot_id, "retried": False, "reason": "no_pending_winners", "payouts": []}

                prizedraw = await db_access.get_prizedraw(db, spot_id=spot_id)
                configured_prize_count = int(prizedraw.get(schema.PRIZEDRAW_PRIZE_COUNT) if prizedraw else 1)
                winner_claim_ids = await db_access.get_prizedraw_winner_claim_ids(db, spot_id=spot_id)
                amount_by_claim_id = _prize_amounts_by_claim_id(
                    total_value=int(spot.get(schema.SPOT_TOTAL_VALUE) or 0),
                    prize_count=max(1, configured_prize_count),
                    winner_claim_ids=winner_claim_ids,
                )

                pending_winners.sort(key=lambda row: int(row.get(schema.CLAIM_ID) or 0))
                for claim in pending_winners:
                    claim_id = int(claim[schema.CLAIM_ID])
                    if await db_access.has_confirmed_claim_payout_transaction(db, claim_id=claim_id):
                        await db_access.set_claim_status_to_success(db, claim_id=claim_id)
                        continue
                    if await db_access.has_nonfailed_claim_payout_transaction(db, claim_id=claim_id):
                        payout_jobs.append({
                            "ok": True,
                            "claim_id": claim_id,
                            "already_exists": True,
                            "reason": "pending_payout_already_recorded",
                            "send": False,
                        })
                        continue

                    retry_amount = await db_access.latest_failed_claim_payout_amount(db, claim_id=claim_id)
                    amount = int(retry_amount or amount_by_claim_id.get(claim_id, 0))
                    if amount <= 0:
                        payout_jobs.append({
                            "ok": False,
                            "claim_id": claim_id,
                            "reason": "missing_payout_amount",
                            "send": False,
                        })
                        continue

                    payout_jobs.append({
                        "ok": True,
                        "claim_id": claim_id,
                        "amount": amount,
                        "send": True,
                    })

        payout_results: list[RowDict] = []
        for job in payout_jobs:
            if not job.get("send"):
                cleaned = dict(job)
                cleaned.pop("send", None)
                payout_results.append(cleaned)
                continue

            async with get_db() as send_db:
                payout_results.append(
                    await trans_updater.submit_claim_reward_transaction(
                        send_db,
                        claim_id=int(job["claim_id"]),
                        amount=int(job["amount"]),
                    )
                )

        async with get_db() as db:
            await cache.notify_spot_changed(db, spot_id=spot_id)
            await cache.notify_claim_changed(db, spot_id=spot_id, user_id=None)
            owner_id = int(spot.get(schema.SPOT_CREATED_BY) or 0) if spot is not None else 0
            if owner_id:
                await cache.notify_user_changed(db, user_id=owner_id)

        return {
            "ok": all(bool(result.get("ok")) for result in payout_results),
            "spot_id": spot_id,
            "retried": any(bool(job.get("send")) for job in payout_jobs),
            "pending_winner_count": len(payout_jobs),
            "payouts": payout_results,
        }
    except Exception as exc:
        return {"ok": False, "spot_id": spot_id, "retried": False, "reason": repr(exc), "payouts": []}


async def retry_pending_prizedraw_payouts(*, max_spots: int = DEFAULT_MAX_SETTLEMENTS_PER_RUN) -> RowDict:
    """Retry payout sends for completed Prizedraws with pending winner claims."""
    async with get_db() as db:
        spot_ids = await db_access.get_completed_prizedraw_spot_ids_with_pending_winners(
            db,
            limit=int(max_spots),
        )

    results: list[RowDict] = []
    for spot_id in spot_ids:
        results.append(await retry_pending_prizedraw_payouts_for_spot(spot_id=int(spot_id)))

    return {
        "ok": all(bool(result.get("ok")) for result in results),
        "checked_count": len(spot_ids),
        "retried_count": sum(1 for result in results if result.get("retried")),
        "failed_count": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


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


async def settle_spot_remainder_if_ready(*, spot_id: int) -> RowDict:
    """Complete one terminal Spot and return every safely-accounted unspent Luna.

    The refund waits until all in-progress duration claims have resolved and all
    claim/creation-fee obligations have confirmed. This preserves the existing
    rule that a duration claim begun while the Spot was active may finish after
    the public entry period closes.
    """
    spot_id = int(spot_id)
    prepared: RowDict | None = None
    spot_was_completed = False
    should_complete = False

    try:
        async with get_db() as db:
            async with db_access.transaction(db, immediate=True):
                spot = await db_access.get_spot(db, spot_id=spot_id)
                if spot is None:
                    return {"ok": False, "spot_id": spot_id, "reason": "spot_missing"}
                status = int(spot.get(schema.SPOT_STATUS) or -1)
                if status in {const.SPOT_STATUS_CANCELLED, const.SPOT_STATUS_BANNED}:
                    return {"ok": True, "spot_id": spot_id, "refunded": False, "reason": "terminal_without_refund"}
                if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
                    return {"ok": True, "spot_id": spot_id, "refunded": False, "reason": "cancellation_managed_separately"}

                pending_claims = await db_access.count_claims_by_status_for_spot(
                    db,
                    spot_id=spot_id,
                    status=const.CLAIM_STATUS_PENDING,
                )
                if pending_claims > 0:
                    return {
                        "ok": True,
                        "spot_id": spot_id,
                        "refunded": False,
                        "deferred": True,
                        "reason": "pending_claims",
                        "pending_claim_count": int(pending_claims),
                    }

                spot_is_prizedraw = await db_access.is_prizedraw(db, spot_id=spot_id)
                if status == const.SPOT_STATUS_PUBLISHED:
                    if spot_is_prizedraw:
                        return {
                            "ok": True,
                            "spot_id": spot_id,
                            "refunded": False,
                            "deferred": True,
                            "reason": "prizedraw_not_settled",
                        }
                    now = await _get_unixepoch(db)
                    ends_at = _spot_absolute_ends_at(spot)
                    max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
                    success_count = await db_access.count_claims_by_status_for_spot(
                        db,
                        spot_id=spot_id,
                        status=const.CLAIM_STATUS_SUCCESS,
                    )
                    period_ended = ends_at is not None and int(ends_at) <= int(now)
                    capacity_reached = max_total > 0 and int(success_count) >= max_total
                    if not period_ended and not capacity_reached:
                        return {"ok": True, "spot_id": spot_id, "refunded": False, "reason": "not_terminal"}
                    should_complete = True
                    spot[schema.SPOT_STATUS] = const.SPOT_STATUS_COMPLETED
                elif status != const.SPOT_STATUS_COMPLETED:
                    return {"ok": True, "spot_id": spot_id, "refunded": False, "reason": "not_terminal"}

                if not spot_is_prizedraw:
                    unpaid_claim_count = await db_access.count_successful_standard_claims_without_confirmed_payout(
                        db,
                        spot_id=spot_id,
                    )
                    if unpaid_claim_count > 0:
                        return {
                            "ok": True,
                            "spot_id": spot_id,
                            "refunded": False,
                            "deferred": True,
                            "reason": "claim_payouts_unconfirmed",
                            "unconfirmed_claim_payout_count": int(unpaid_claim_count),
                        }

                transactions = await db_access.get_transactions_by_spot(
                    db,
                    spot_id=spot_id,
                    limit=db_access.MAX_LIMIT,
                )
                blocking_types = {
                    const.TRANS_TYPE_FILL_SPOT,
                    const.TRANS_TYPE_CLAIM,
                    const.TRANS_TYPE_CREATION_FEE,
                    const.TRANS_TYPE_REMAINDER_REFUND,
                }
                pending_outgoing = [
                    row
                    for row in transactions
                    if int(row.get(schema.TRANS_TYPE) or -1) in blocking_types
                    and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
                    == const.TRANS_STATUS_PENDING
                ]
                if pending_outgoing:
                    return {
                        "ok": True,
                        "spot_id": spot_id,
                        "refunded": False,
                        "deferred": True,
                        "reason": "financial_transaction_pending",
                        "pending_transaction_count": len(pending_outgoing),
                    }

                failed_deposits = [
                    row
                    for row in transactions
                    if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
                    and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_FAILED
                ]
                manual_review_required = bool(failed_deposits)

                confirmed_deposits = [
                    row
                    for row in transactions
                    if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
                    and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
                ]
                confirmed_deposit_total = sum(int(row.get(schema.TRANS_AMOUNT) or 0) for row in confirmed_deposits)
                confirmed_claim_total = sum(
                    int(row.get(schema.TRANS_AMOUNT) or 0)
                    for row in transactions
                    if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CLAIM
                    and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
                )
                confirmed_creation_fee_total = sum(
                    int(row.get(schema.TRANS_AMOUNT) or 0)
                    for row in transactions
                    if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CREATION_FEE
                    and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
                )
                expected_creation_fee = int(db_access.spot_creation_fee_amount(spot))
                if confirmed_creation_fee_total < expected_creation_fee:
                    return {
                        "ok": True,
                        "spot_id": spot_id,
                        "refunded": False,
                        "deferred": True,
                        "reason": "creation_fee_unconfirmed",
                        "expected_creation_fee": expected_creation_fee,
                        "confirmed_creation_fee_total": confirmed_creation_fee_total,
                    }

                confirmed_refund_total = sum(
                    int(row.get(schema.TRANS_AMOUNT) or 0)
                    for row in transactions
                    if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_REMAINDER_REFUND
                    and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
                )
                remainder_amount = max(
                    0,
                    confirmed_deposit_total
                    - confirmed_claim_total
                    - confirmed_creation_fee_total
                    - confirmed_refund_total,
                )
                if should_complete:
                    await db_access.set_spot_status_to_completed(db, spot_id=spot_id)
                    spot_was_completed = True

                if remainder_amount <= 0:
                    await db_access.mark_spot_remainder_settled(db, spot_id=spot_id)
                    prepared = {
                        "ok": True,
                        "spot_id": spot_id,
                        "refunded": False,
                        "reason": "no_remainder",
                        "remainder_amount": 0,
                        "manual_review_required": manual_review_required,
                        "failed_deposit_count": len(failed_deposits),
                    }
                else:
                    source = next(
                        (
                            row
                            for row in confirmed_deposits
                            if str(row.get(schema.TRANS_FROM_ADDRESS) or "").strip()
                        ),
                        None,
                    )
                    if source is None:
                        return {
                            "ok": False,
                            "spot_id": spot_id,
                            "refunded": False,
                            "reason": "confirmed_deposit_sender_missing",
                        }
                    prepared = {
                        "ok": True,
                        "spot_id": spot_id,
                        "refunded": False,
                        "remainder_amount": int(remainder_amount),
                        "refund_address": str(source.get(schema.TRANS_FROM_ADDRESS) or "").strip(),
                        "refund_source_tx_hash": str(source.get(schema.TRANS_TX_HASH) or "").strip(),
                        "confirmed_deposit_total": confirmed_deposit_total,
                        "confirmed_claim_total": confirmed_claim_total,
                        "confirmed_creation_fee_total": confirmed_creation_fee_total,
                        "confirmed_remainder_refund_total": confirmed_refund_total,
                        "manual_review_required": manual_review_required,
                        "failed_deposit_count": len(failed_deposits),
                    }

            if spot_was_completed:
                await cache.notify_spot_changed(db, spot_id=spot_id)

        if prepared is None or int(prepared.get("remainder_amount") or 0) <= 0:
            return prepared or {"ok": True, "spot_id": spot_id, "refunded": False, "reason": "not_ready"}

        try:
            resolved_address = await trans_updater.resolve_nimiq_pay_payout_address(
                str(prepared["refund_address"]),
                source_tx_hash=str(prepared.get("refund_source_tx_hash") or "") or None,
            )
        except Exception as exc:
            return {
                **prepared,
                "ok": True,
                "refunded": False,
                "deferred": True,
                "reason": "refund_address_resolution_pending",
                "error": repr(exc),
            }

        async with get_db() as send_db:
            try:
                send_result = await trans_updater.submit_spot_remainder_refund_transaction(
                    send_db,
                    spot_id=spot_id,
                    to_address=resolved_address,
                    amount=int(prepared["remainder_amount"]),
                )
            except RuntimeError as exc:
                duplicate_guard_hit = "already has a non-failed remainder refund transaction" in str(exc)
                if duplicate_guard_hit and await db_access.has_nonfailed_spot_remainder_refund_transaction(
                    send_db,
                    spot_id=spot_id,
                ):
                    return {
                        **prepared,
                        "ok": True,
                        "refunded": False,
                        "already_exists": True,
                        "reason": "concurrent_remainder_refund_already_recorded",
                    }
                raise

        return {
            **prepared,
            **send_result,
            "ok": bool(send_result.get("ok")),
            "refunded": bool(send_result.get("ok") and not send_result.get("already_exists")),
            "refund_address": resolved_address,
        }
    except Exception as exc:
        return {"ok": False, "spot_id": spot_id, "refunded": False, "reason": repr(exc)}


async def settle_spot_remainders(
    *,
    max_spots: int = DEFAULT_MAX_REMAINDER_REFUNDS_PER_RUN,
) -> RowDict:
    """Complete terminal Spots and safely return their unspent funds."""
    async with get_db() as db:
        spot_ids = await db_access.get_spot_ids_ready_for_remainder_refund(
            db,
            limit=int(max_spots),
        )

    results = [await settle_spot_remainder_if_ready(spot_id=spot_id) for spot_id in spot_ids]
    return {
        "ok": all(bool(result.get("ok")) for result in results),
        "checked_count": len(spot_ids),
        "submitted_count": sum(1 for result in results if result.get("refunded")),
        "deferred_count": sum(1 for result in results if result.get("deferred")),
        "manual_review_count": sum(1 for result in results if result.get("manual_review_required")),
        "failed_count": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


async def retry_pending_spot_cancellations(*, limit: int = 50) -> RowDict:
    """Resume durable cancellation requests after their blockers resolve."""
    async with get_db() as db:
        spot_ids = await db_access.get_pending_cancellation_spot_ids(db, limit=int(limit))

    results: list[RowDict] = []
    for spot_id in spot_ids:
        try:
            async with get_db() as db:
                result = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=int(spot_id),
                    cancellation_fee=getattr(const, "SPOT_CANCELLATION_FEE", 0),
                    fee_address=getattr(const, "SPOT_FEE_ADDRESS", ""),
                )
            results.append(result)
        except Exception as exc:
            results.append({
                "ok": False,
                "spot_id": int(spot_id),
                "reason": repr(exc),
            })

    return {
        "ok": all(bool(result.get("ok")) for result in results),
        "checked_count": len(spot_ids),
        "completed_count": sum(1 for result in results if result.get("cancelled")),
        "pending_count": sum(1 for result in results if result.get("cancellation_pending")),
        "failed_count": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


async def run_settlement_pass() -> RowDict:
    """Run all app-level settlement work once."""
    duration_result = await settle_pending_duration_claims()
    standard_payout_result = await retry_unpaid_standard_claim_payouts()
    prizedraw_result = await settle_ready_prizedraws()
    payout_retry_result = await retry_pending_prizedraw_payouts()
    remainder_result = await settle_spot_remainders()
    cancellation_result = await retry_pending_spot_cancellations()
    return {
        "ok": (
            bool(duration_result.get("ok"))
            and bool(standard_payout_result.get("ok"))
            and bool(prizedraw_result.get("ok"))
            and bool(payout_retry_result.get("ok"))
            and bool(remainder_result.get("ok"))
            and bool(cancellation_result.get("ok"))
        ),
        "duration_claims": duration_result,
        "standard_claim_payouts": standard_payout_result,
        "prizedraws": prizedraw_result,
        "prizedraw_payout_retries": payout_retry_result,
        "spot_remainder_refunds": remainder_result,
        "spot_cancellations": cancellation_result,
    }


async def _settlement_loop(interval_seconds: int) -> None:
    global _SETTLEMENT_LAST_RESULT, _SETTLEMENT_LAST_ERROR

    stop_event = _SETTLEMENT_STOP_EVENT
    if stop_event is None:  # Defensive: the loop is normally created only by start_settlement_refresher().
        return
    while not stop_event.is_set():
        try:
            _SETTLEMENT_LAST_RESULT = await run_settlement_pass()
            if bool(_SETTLEMENT_LAST_RESULT.get("ok", True)):
                _SETTLEMENT_LAST_ERROR = None
            else:
                _SETTLEMENT_LAST_ERROR = repr(_SETTLEMENT_LAST_RESULT)
                logger.error(
                    "Background settlement pass reported failure: %s",
                    _SETTLEMENT_LAST_RESULT,
                )
        except Exception as exc:  # pragma: no cover - defensive loop guard
            _SETTLEMENT_LAST_ERROR = repr(exc)
            logger.exception("Background settlement pass failed")

        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=max(1, int(interval_seconds)))


async def start_settlement_refresher(
    *,
    run_immediately: bool = False,
    interval_seconds: int = DEFAULT_SETTLEMENT_INTERVAL_SECONDS,
    fail_on_initial_error: bool = False,
) -> None:
    """Start the background settlement loop once.

    Development may continue after a failed initial pass so local UI work is
    still possible without chain access. Production sets
    ``fail_on_initial_error`` so the app cannot quietly serve while settlement
    is unavailable.
    """
    global _SETTLEMENT_TASK, _SETTLEMENT_STOP_EVENT, _SETTLEMENT_LAST_RESULT, _SETTLEMENT_LAST_ERROR

    if _SETTLEMENT_TASK is not None and not _SETTLEMENT_TASK.done():
        return

    _SETTLEMENT_STOP_EVENT = asyncio.Event()

    if run_immediately:
        try:
            _SETTLEMENT_LAST_RESULT = await run_settlement_pass()
            if not bool(_SETTLEMENT_LAST_RESULT.get("ok", True)):
                _SETTLEMENT_LAST_ERROR = repr(_SETTLEMENT_LAST_RESULT)
                if fail_on_initial_error:
                    raise RuntimeError("Initial settlement pass reported failure")
                logger.error("Initial settlement pass reported failure: %s", _SETTLEMENT_LAST_RESULT)
            else:
                _SETTLEMENT_LAST_ERROR = None
        except Exception as exc:
            _SETTLEMENT_LAST_ERROR = repr(exc)
            logger.exception("Initial background settlement pass failed")
            if fail_on_initial_error:
                raise

    _SETTLEMENT_TASK = asyncio.create_task(_settlement_loop(int(interval_seconds)))


def settlement_refresher_status() -> RowDict:
    """Return a secret-free snapshot of the settlement worker."""
    return {
        "running": _SETTLEMENT_TASK is not None and not _SETTLEMENT_TASK.done(),
        "last_error": _SETTLEMENT_LAST_ERROR,
        "last_result": _SETTLEMENT_LAST_RESULT,
        "interval_seconds": DEFAULT_SETTLEMENT_INTERVAL_SECONDS,
    }


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
