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
DAILY_WINDOW_SECONDS = _env_int("NIMHUNT_CLAIM_PAYOUT_DAILY_WINDOW_SECONDS", 24 * 60 * 60)
DAILY_MAX_PAYOUT_COUNT = _env_int("NIMHUNT_CLAIM_PAYOUT_DAILY_MAX_COUNT", 100)
DAILY_MAX_PAYOUT_NIM = _env_int("NIMHUNT_CLAIM_PAYOUT_DAILY_MAX_NIM", 50_000)
DAILY_MAX_PAYOUT_LUNA = DAILY_MAX_PAYOUT_NIM * int(getattr(const, "LUNA_PER_NIM", 100_000))
MAX_AUTOMATIC_PAYOUT_NIM = _env_int("NIMHUNT_CLAIM_MAX_AUTOMATIC_PAYOUT_NIM", 10_000)
MAX_AUTOMATIC_PAYOUT_LUNA = MAX_AUTOMATIC_PAYOUT_NIM * int(getattr(const, "LUNA_PER_NIM", 100_000))
# Open Standard Spots have no creator-controlled admission secret. Limit their
# independent automatic blast radius even when every identity/location signal
# is attacker-controlled. Ten minimum-sized claims still supports a small
# legitimate crowd; the amount ceiling prevents fewer high-value claims from
# consuming more than 5,000 NIM automatically in any rolling day.
SPOT_WINDOW_SECONDS = _env_int("NIMHUNT_OPEN_SPOT_PAYOUT_WINDOW_SECONDS", 24 * 60 * 60)
SPOT_MAX_PAYOUT_COUNT = _env_int("NIMHUNT_OPEN_SPOT_PAYOUT_MAX_COUNT", 10)
SPOT_MAX_PAYOUT_NIM = _env_int("NIMHUNT_OPEN_SPOT_PAYOUT_MAX_NIM", 5_000)
SPOT_MAX_PAYOUT_LUNA = SPOT_MAX_PAYOUT_NIM * int(getattr(const, "LUNA_PER_NIM", 100_000))
SPOT_LIFETIME_AUTOMATIC_PERCENT = _env_int(
    "NIMHUNT_OPEN_SPOT_LIFETIME_AUTOMATIC_PERCENT", 50, minimum=0
)
if SPOT_LIFETIME_AUTOMATIC_PERCENT > 100:
    raise ValueError("NIMHUNT_OPEN_SPOT_LIFETIME_AUTOMATIC_PERCENT must be at most 100")

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


def throttle_decision(*, state: RowDict, amount: int, daily_state: RowDict | None = None) -> RowDict:
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

    if amount > MAX_AUTOMATIC_PAYOUT_LUNA:
        return {
            "allow": False,
            "reason": "individual_automatic_payout_limit",
            # This requires an operator decision or configuration change rather
            # than a tight retry loop. The CLAIM remains valid and pending.
            "manual_review": True,
            "window_payout_count": payout_count,
            "window_payout_amount": payout_amount,
        }

    if daily_state is not None:
        daily_count = max(0, int(daily_state.get("payout_count") or 0))
        daily_amount = max(0, int(daily_state.get("payout_amount") or 0))
        daily_oldest = daily_state.get("oldest_created_at")
        daily_retry = (
            int(daily_oldest) + DAILY_WINDOW_SECONDS + 1
            if daily_oldest is not None
            else int(daily_state.get("now") or now) + DAILY_WINDOW_SECONDS
        )
        if daily_count >= DAILY_MAX_PAYOUT_COUNT:
            return {"allow": False, "reason": "daily_payout_count_limit", "retry_at": daily_retry,
                    "daily_payout_count": daily_count, "daily_payout_amount": daily_amount}
        if daily_amount + amount > DAILY_MAX_PAYOUT_LUNA:
            return {"allow": False, "reason": "daily_payout_amount_limit", "retry_at": daily_retry,
                    "daily_payout_count": daily_count, "daily_payout_amount": daily_amount}

    if payout_count >= MAX_PAYOUT_COUNT:
        return {
            "allow": False,
            "reason": "global_payout_count_limit",
            "retry_at": retry_at,
            "window_payout_count": payout_count,
            "window_payout_amount": payout_amount,
        }

    if payout_amount + amount > MAX_PAYOUT_LUNA:
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
    clean: list[RowDict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            claim_id = int(item.get("claim_id") or 0)
            amount = int(item.get("amount") or 0)
            reserved_at = int(item.get("reserved_at") or 0)
            short_reserved_at = int(item.get("short_reserved_at") or reserved_at)
            daily_reserved_at = int(item.get("daily_reserved_at") or reserved_at)
            spot_reserved_at = int(item.get("spot_reserved_at") or reserved_at)
            spot_id = int(item.get("spot_id") or 0)
        except (TypeError, ValueError):
            continue
        if claim_id <= 0 or amount <= 0 or reserved_at <= 0:
            continue
        clean.append(
            {
                "claim_id": claim_id,
                "amount": amount,
                "reserved_at": reserved_at,
                "short_reserved_at": short_reserved_at,
                "daily_reserved_at": daily_reserved_at,
                "spot_reserved_at": spot_reserved_at,
                "spot_id": spot_id,
            }
        )
    return clean


async def _recent_payout_rows(db, *, now: int) -> list[RowDict]:
    cutoff = int(now) - max(WINDOW_SECONDS, DAILY_WINDOW_SECONDS, SPOT_WINDOW_SECONDS)
    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.TRANS_ID} AS trans_id,
            {schema.TRANS_CLAIM_ID} AS claim_id,
            {schema.TRANS_AMOUNT} AS amount,
            {schema.TRANS_CREATED_AT} AS created_at,
            {schema.TRANS_SPOT_ID} AS spot_id
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


async def _lifetime_spot_payout_rows(db, *, spot_id: int) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.TRANS_ID} AS trans_id,
            {schema.TRANS_CLAIM_ID} AS claim_id,
            {schema.TRANS_AMOUNT} AS amount,
            {schema.TRANS_CREATED_AT} AS created_at,
            {schema.TRANS_SPOT_ID} AS spot_id
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} != ?
          AND {schema.TRANS_SPOT_ID} = ?;
        """,
        (const.TRANS_TYPE_CLAIM, const.TRANS_STATUS_FAILED, int(spot_id)),
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


def _spot_window_decision(*, state: RowDict, amount: int, spot_id: int) -> RowDict:
    count = max(0, int(state.get("payout_count") or 0))
    exposure = max(0, int(state.get("payout_amount") or 0))
    details = {
        "spot_id": int(spot_id),
        "spot_window_seconds": int(SPOT_WINDOW_SECONDS),
        "spot_payout_count": count,
        "spot_payout_amount": exposure,
        "spot_max_payout_count": int(SPOT_MAX_PAYOUT_COUNT),
        "spot_max_payout_amount": int(SPOT_MAX_PAYOUT_LUNA),
        "manual_review": True,
    }
    if count >= SPOT_MAX_PAYOUT_COUNT:
        return {"allow": False, "reason": "open_spot_payout_count_limit", **details}
    if exposure + int(amount) > SPOT_MAX_PAYOUT_LUNA:
        return {"allow": False, "reason": "open_spot_payout_amount_limit", **details}
    return {"allow": True, "reason": "within_open_spot_payout_limits", **details}


def _spot_lifetime_decision(
    *, state: RowDict, amount: int, spot: RowDict
) -> RowDict:
    spot_id = int(spot[schema.SPOT_ID])
    capacity = max(1, int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0))
    reward_pool = max(0, int(spot.get(schema.SPOT_TOTAL_VALUE) or 0))
    max_count = capacity * SPOT_LIFETIME_AUTOMATIC_PERCENT // 100
    max_amount = reward_pool * SPOT_LIFETIME_AUTOMATIC_PERCENT // 100
    count = max(0, int(state.get("payout_count") or 0))
    exposure = max(0, int(state.get("payout_amount") or 0))
    details = {
        "spot_id": spot_id,
        "spot_lifetime_automatic_percent": int(SPOT_LIFETIME_AUTOMATIC_PERCENT),
        "spot_lifetime_payout_count": count,
        "spot_lifetime_payout_amount": exposure,
        "spot_lifetime_max_payout_count": max_count,
        "spot_lifetime_max_payout_amount": max_amount,
        "manual_review": True,
    }
    if count >= max_count:
        return {"allow": False, "reason": "open_spot_lifetime_count_limit", **details}
    if exposure + int(amount) > max_amount:
        return {"allow": False, "reason": "open_spot_lifetime_amount_limit", **details}
    return {"allow": True, "reason": "within_open_spot_lifetime_limits", **details}


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
        # Reservations written by the immediately preceding release did not
        # carry a Spot id. Resolve and persist it before applying the new cap so
        # a rolling deployment cannot reset in-flight exposure accounting.
        for reservation in reservations:
            if int(reservation.get("spot_id") or 0) > 0:
                continue
            reserved_claim = await db_access.get_claim(
                db, claim_id=int(reservation["claim_id"])
            )
            if reserved_claim is not None:
                reservation["spot_id"] = int(
                    reserved_claim.get(schema.CLAIM_SPOT_ID) or 0
                )
        payout_rows = await _recent_payout_rows(db, now=now)
        short_cutoff = int(now) - WINDOW_SECONDS
        short_rows = [row for row in payout_rows if int(row.get("created_at") or 0) > short_cutoff]
        short_reservations = [
            {**item, "reserved_at": int(item["short_reserved_at"])}
            for item in reservations
            if int(item["short_reserved_at"]) > short_cutoff
        ]
        state, transaction_claim_ids = _combined_window_state(
            now=now,
            payout_rows=short_rows,
            reservations=short_reservations,
        )
        daily_cutoff = int(now) - DAILY_WINDOW_SECONDS
        daily_rows = [
            row for row in payout_rows
            if int(row.get("created_at") or 0) > daily_cutoff
        ]
        daily_reservations = [
            {**item, "reserved_at": int(item["daily_reserved_at"])}
            for item in reservations
            if int(item["daily_reserved_at"]) > daily_cutoff
        ]
        daily_state, daily_transaction_claim_ids = _combined_window_state(
            now=now, payout_rows=daily_rows, reservations=daily_reservations
        )
        transaction_claim_ids.update(daily_transaction_claim_ids)

        claim = await db_access.get_claim(db, claim_id=claim_id)
        spot_id = int(claim.get(schema.CLAIM_SPOT_ID) or 0) if claim is not None else 0
        spot = await db_access.get_spot(db, spot_id=spot_id) if spot_id > 0 else None
        is_open_standard = bool(
            spot is not None
            and not bool(spot.get(schema.SPOT_USE_PASSWORD))
            and not await db_access.is_prizedraw(db, spot_id=spot_id)
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
                # A lifetime reservation preserves only this claim's Spot
                # allocation. Its short/daily authority expires independently.
                # Exclude the claim's own reservation before rechecking so it
                # is neither double-counted nor treated as a permanent permit.
                without_existing = [
                    item for item in reservations
                    if int(item["claim_id"]) != claim_id
                ]
                short_valid = int(existing["short_reserved_at"]) > short_cutoff
                daily_valid = int(existing["daily_reserved_at"]) > daily_cutoff
                short_state, _ = _combined_window_state(
                    now=now,
                    payout_rows=short_rows,
                    reservations=[
                        {**item, "reserved_at": int(item["short_reserved_at"])}
                        for item in without_existing
                        if int(item["short_reserved_at"]) > short_cutoff
                    ],
                )
                current_daily_state, _ = _combined_window_state(
                    now=now,
                    payout_rows=daily_rows,
                    reservations=[
                        {**item, "reserved_at": int(item["daily_reserved_at"])}
                        for item in without_existing
                        if int(item["daily_reserved_at"]) > daily_cutoff
                    ],
                )
                decision = throttle_decision(
                    state=short_state,
                    daily_state=current_daily_state,
                    amount=amount,
                )
                if bool(decision.get("allow")) and is_open_standard:
                    record = await claim_security.get_claim_security_record(
                        db, claim_id=claim_id
                    )
                    explicitly_released = bool(
                        isinstance(record, dict)
                        and record.get("manual_review_released_at")
                    )
                    if not explicitly_released:
                        spot_cutoff = int(now) - SPOT_WINDOW_SECONDS
                        spot_rows = [
                            row for row in payout_rows
                            if int(row.get("spot_id") or 0) == spot_id
                            and int(row.get("created_at") or 0) > spot_cutoff
                        ]
                        spot_state, _ = _combined_window_state(
                            now=now,
                            payout_rows=spot_rows,
                            reservations=[
                                {
                                    **item,
                                    "reserved_at": int(item["spot_reserved_at"]),
                                }
                                for item in without_existing
                                if int(item.get("spot_id") or 0) == spot_id
                                and int(item["spot_reserved_at"]) > spot_cutoff
                            ],
                        )
                        decision = _spot_window_decision(
                            state=spot_state, amount=amount, spot_id=spot_id
                        )
                if bool(decision.get("allow")):
                    if not short_valid:
                        existing["short_reserved_at"] = int(now)
                    if not daily_valid:
                        existing["daily_reserved_at"] = int(now)
                    if int(existing["spot_reserved_at"]) <= int(now) - SPOT_WINDOW_SECONDS:
                        existing["spot_reserved_at"] = int(now)
                    decision = {
                        **decision,
                        "reason": "existing_payout_reservation",
                        "reservation_reused": True,
                    }
                elif decision.get("manual_review"):
                    record = await claim_security.get_claim_security_record(
                        db, claim_id=claim_id
                    )
                    if isinstance(record, dict):
                        record["manual_review"] = True
                        record["manual_review_reason"] = str(decision["reason"])
                        record["manual_review_marked_at"] = int(now)
                        await claim_security._metadata_set(
                            db, claim_security._claim_record_key(claim_id), record
                        )
            else:
                decision = throttle_decision(state=state, daily_state=daily_state, amount=amount)
                # Global circuit breakers remain authoritative. Only after they
                # allow a payout do we apply the independent Open-Spot budget.
                if bool(decision.get("allow")) and is_open_standard:
                    lifetime_rows = await _lifetime_spot_payout_rows(db, spot_id=spot_id)
                    lifetime_transaction_ids = {
                        int(row.get("claim_id") or 0) for row in lifetime_rows
                    }
                    # Materialised intents are authoritative; discard their
                    # now-redundant reservations. Unmaterialised reservations
                    # remain for the Spot lifetime so a crash after reservation
                    # cannot silently restore automatic allowance.
                    reservations = [
                        item for item in reservations
                        if not (
                            int(item.get("spot_id") or 0) == spot_id
                            and int(item["claim_id"]) in lifetime_transaction_ids
                        )
                    ]
                    lifetime_reservations = [
                        item for item in reservations
                        if int(item.get("spot_id") or 0) == spot_id
                    ]
                    lifetime_state, _ = _combined_window_state(
                        now=now,
                        payout_rows=lifetime_rows,
                        reservations=lifetime_reservations,
                    )
                    spot_cutoff = int(now) - SPOT_WINDOW_SECONDS
                    spot_rows = [
                        row for row in payout_rows
                        if int(row.get("spot_id") or 0) == spot_id
                        and int(row.get("created_at") or 0) > spot_cutoff
                    ]
                    spot_reservations = [
                        {**item, "reserved_at": int(item["spot_reserved_at"])}
                        for item in reservations
                        if int(item.get("spot_id") or 0) == spot_id
                        and int(item["spot_reserved_at"]) > spot_cutoff
                    ]
                    spot_state, _ = _combined_window_state(
                        now=now,
                        payout_rows=spot_rows,
                        reservations=spot_reservations,
                    )
                    record = await claim_security.get_claim_security_record(db, claim_id=claim_id)
                    explicitly_released = bool(
                        isinstance(record, dict) and record.get("manual_review_released_at")
                    )
                    if not explicitly_released:
                        decision = _spot_lifetime_decision(
                            state=lifetime_state, amount=amount, spot=spot
                        )
                        if bool(decision.get("allow")):
                            decision = _spot_window_decision(
                                state=spot_state, amount=amount, spot_id=spot_id
                            )
                if decision.get("manual_review"):
                    record = await claim_security.get_claim_security_record(
                        db, claim_id=claim_id
                    )
                    if isinstance(record, dict):
                        record["manual_review"] = True
                        record["manual_review_reason"] = str(decision["reason"])
                        record["manual_review_marked_at"] = int(now)
                        record["manual_review_details"] = {
                            key: decision[key]
                            for key in (
                                "spot_id", "spot_window_seconds", "spot_payout_count",
                                "spot_payout_amount", "spot_max_payout_count",
                                "spot_max_payout_amount",
                                "spot_lifetime_automatic_percent",
                                "spot_lifetime_payout_count",
                                "spot_lifetime_payout_amount",
                                "spot_lifetime_max_payout_count",
                                "spot_lifetime_max_payout_amount",
                            )
                            if key in decision
                        }
                        await claim_security._metadata_set(
                            db, claim_security._claim_record_key(claim_id), record
                        )
                if bool(decision.get("allow")):
                    reservations.append(
                        {
                            "claim_id": claim_id,
                            "amount": amount,
                            "reserved_at": int(now),
                            "short_reserved_at": int(now),
                            "daily_reserved_at": int(now),
                            "spot_reserved_at": int(now),
                            "spot_id": spot_id,
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
    "DAILY_WINDOW_SECONDS",
    "DAILY_MAX_PAYOUT_COUNT",
    "DAILY_MAX_PAYOUT_LUNA",
    "MAX_AUTOMATIC_PAYOUT_LUNA",
    "SPOT_WINDOW_SECONDS",
    "SPOT_MAX_PAYOUT_COUNT",
    "SPOT_MAX_PAYOUT_LUNA",
    "SPOT_LIFETIME_AUTOMATIC_PERCENT",
    "RESERVATION_KEY",
    "WINDOW_SECONDS",
    "install",
    "payout_window_state",
    "reserve_payout_slot",
    "submit_claim_reward_transaction_with_throttle",
    "throttle_decision",
]
