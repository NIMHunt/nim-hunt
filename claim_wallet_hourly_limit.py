"""Enforce the verified-wallet hourly claim limit from durable claim rows.

``claim_security`` keeps a bounded recent-event list for behavioural correlation.
That list is intentionally capped, so unrelated traffic must never be able to
rotate away evidence used for a hard per-wallet limit. This module makes the
hourly limit authoritative from the CLAIM table instead:

* every NimHunt user identity bound to the same verified wallet is included;
* an early preclaim check preserves the existing ``wallet_rate_limit`` response;
* the same limit is rechecked inside ``create_claim_attempt``, which NimHunt
  calls under its serialized SQLite claim transaction, closing the last-slot
  concurrency race.

The bounded event list remains useful for impossible-travel and burst detection;
it is simply no longer the source of truth for the wallet hourly quota.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import claim_security
import claim_security_defence_in_depth as defence
import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]
ClaimAttempt = Callable[..., Awaitable[RowDict]]
PreclaimDecision = Callable[..., Awaitable[RowDict]]

_PRECLAIM_DELEGATE: PreclaimDecision | None = None
_CLAIM_ATTEMPT_DELEGATE: ClaimAttempt | None = None
_INSTALLED = False


async def _wallet_recent_claim_window(
    db,
    *,
    wallet_address: str,
    now: int,
) -> tuple[int, int | None]:
    """Return count and oldest timestamp for this wallet in the last hour."""
    user_ids = await defence._verified_wallet_user_ids(
        db,
        wallet_address=wallet_address,
    )
    if not user_ids:
        return 0, None

    placeholders = ",".join("?" for _ in user_ids)
    cutoff = int(now) - 60 * 60
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n, MIN({schema.CLAIM_CLAIMED_AT}) AS oldest
        FROM {schema.CLAIM_TABLE_NAME}
        WHERE {schema.CLAIM_RECIPIENT} IN ({placeholders})
          AND {schema.CLAIM_CLAIMED_AT} > ?;
        """,
        (*user_ids, cutoff),
    )
    row = await cur.fetchone()
    count = int(row["n"] or 0)
    oldest = row["oldest"]
    return count, int(oldest) if oldest is not None else None


async def _durable_wallet_rate_decision(
    db,
    *,
    wallet_address: str,
    now: int,
) -> RowDict:
    count, oldest = await _wallet_recent_claim_window(
        db,
        wallet_address=wallet_address,
        now=int(now),
    )
    limit = max(1, int(claim_security.WALLET_HOURLY_CLAIM_LIMIT))
    if count < limit:
        return {"blocked": False, "reason": "allow"}

    retry_at = int(oldest if oldest is not None else now) + 60 * 60 + 1
    return {
        "blocked": True,
        "reason": "wallet_rate_limit",
        "signal": "verified wallet",
        "retry_at": retry_at,
    }


async def _preclaim_decision_with_durable_wallet_limit(
    *,
    spot_id: int,
    session: RowDict,
    request_body: RowDict,
    ip_fingerprint: str,
) -> RowDict:
    delegate = _PRECLAIM_DELEGATE
    if delegate is None:  # pragma: no cover - runtime requires install().
        raise RuntimeError("durable wallet preclaim limiter is not installed")

    decision = await delegate(
        spot_id=int(spot_id),
        session=session,
        request_body=request_body,
        ip_fingerprint=ip_fingerprint,
    )
    if bool(decision.get("blocked")):
        return decision

    wallet_address = claim_security._canonical_optional_address(
        session.get("wallet_address")
    )
    if wallet_address is None:
        return decision

    async with claim_security.get_db() as db:
        now = await db_access.get_unixepoch(db)
        durable = await _durable_wallet_rate_decision(
            db,
            wallet_address=wallet_address,
            now=now,
        )
    return durable if durable.get("blocked") else decision


async def _create_claim_attempt_with_durable_wallet_limit(
    db,
    *,
    spot_id: int,
    user_id: int,
    lat: float,
    long: float,
    location_accuracy_metres: float | None = None,
    claim_code: str | None = None,
    payout_address: str | None = None,
) -> RowDict:
    delegate = _CLAIM_ATTEMPT_DELEGATE
    if delegate is None:  # pragma: no cover - runtime requires install().
        raise RuntimeError("durable wallet claim limiter is not installed")

    if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        binding = await claim_security._metadata_get(
            db,
            claim_security._user_binding_key(int(user_id)),
        )
        wallet_address = (
            claim_security._canonical_optional_address(binding.get("wallet_address"))
            if isinstance(binding, dict)
            else None
        )
        if wallet_address is not None:
            now = await db_access.get_unixepoch(db)
            decision = await _durable_wallet_rate_decision(
                db,
                wallet_address=wallet_address,
                now=now,
            )
            if decision.get("blocked"):
                raise ValueError("Verified wallet hourly claim limit reached.")

    return await delegate(
        db,
        spot_id=int(spot_id),
        user_id=int(user_id),
        lat=float(lat),
        long=float(long),
        location_accuracy_metres=location_accuracy_metres,
        claim_code=claim_code,
        payout_address=payout_address,
    )


def install() -> None:
    """Install durable wallet-limit checks around the current security chain."""
    global _PRECLAIM_DELEGATE, _CLAIM_ATTEMPT_DELEGATE, _INSTALLED
    if _INSTALLED:
        return

    _PRECLAIM_DELEGATE = claim_security._preclaim_decision
    claim_security._preclaim_decision = _preclaim_decision_with_durable_wallet_limit

    # Defence-in-depth has already wrapped create_claim_attempt by the time this
    # installer runs. Capture it here so own-Spot, per-Spot and payout binding
    # protections remain underneath this hourly limiter.
    _CLAIM_ATTEMPT_DELEGATE = db_access.create_claim_attempt
    db_access.create_claim_attempt = _create_claim_attempt_with_durable_wallet_limit
    _INSTALLED = True


__all__ = [
    "_create_claim_attempt_with_durable_wallet_limit",
    "_durable_wallet_rate_decision",
    "_preclaim_decision_with_durable_wallet_limit",
    "_wallet_recent_claim_window",
    "install",
]
