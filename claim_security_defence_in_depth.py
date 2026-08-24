"""Second-layer claim safeguards for public NimHunt deployments.

This module builds on ``claim_security.py`` and the Railway-aware network helper
without duplicating either. It closes residual gaps that device-only identity
cannot cover:

* Source IP remains useful for rate limits and correlation, but is never enough
  on its own to reject a claim; carrier NAT, households and VPN exits can be
  shared by unrelated people.
* The signed Nimiq wallet remains the durable anti-abuse identity, while the
  user-facing account returned by Nimiq Pay remains the validated payout target.
* A Spot's per-user claim limit is also enforced across every device account
  bound to the same verified Nimiq wallet, including duration-claim completion.
* When the Spot owner has a verified wallet binding, that same wallet cannot
  evade the own-Spot rule by presenting a fresh device identity.
* A broader coordinated-burst rule catches several brand-new identities
  claiming geographically distant Spots even when submitted coordinates are
  deliberately moved away from the exact Spot centre.
* The default payout observation window is long enough for that broader rule to
  see a fast sweep before the earliest suspicious payout is released.

All rules remain claim-specific. Nothing here globally disables Spot creation
or claiming.
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

import claim_security
import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]
ClaimAttempt = Callable[..., Awaitable[RowDict]]
PromoteClaim = Callable[..., Awaitable[RowDict | None]]

BROAD_BURST_WINDOW_SECONDS = int(
    os.getenv("NIMHUNT_CLAIM_SECURITY_BROAD_BURST_WINDOW_SECONDS", 15 * 60)
)
BROAD_BURST_MIN_IDENTITIES = int(
    os.getenv("NIMHUNT_CLAIM_SECURITY_BROAD_BURST_MIN_IDENTITIES", 5)
)
BROAD_BURST_MIN_SPREAD_METRES = int(
    os.getenv("NIMHUNT_CLAIM_SECURITY_BROAD_BURST_MIN_SPREAD_METRES", 50_000)
)
DEFAULT_PAYOUT_OBSERVATION_SECONDS = 20 * 60

_ORIGINAL_PRECLAIM_RISK = claim_security._preclaim_risk
_ORIGINAL_RECORD_CLAIM_EVENT = claim_security._record_claim_event
_CLAIM_ATTEMPT_DELEGATE: ClaimAttempt | None = None
_PROMOTE_CLAIM_DELEGATE: PromoteClaim | None = None
_INSTALLED = False


def _preclaim_risk_without_ip_only_block(events: list[RowDict], target: RowDict) -> RowDict:
    """Keep strong identity rules but never reject solely because an IP matched."""
    decision = _ORIGINAL_PRECLAIM_RISK(events, target)
    if str(decision.get("reason") or "") == "source_network_impossible_travel":
        return {"blocked": False, "reason": "allow"}
    return decision


def broad_new_identity_burst_claim_ids(events: list[RowDict], *, now: int) -> list[int]:
    """Detect a likely Sybil sweep without relying on exact-centre GPS.

    The rule requires several independent new wallets, devices and Spots plus a
    large geographic spread. A single traveller, household, VPN, or inaccurate
    GPS reading therefore cannot trigger it.
    """
    cutoff = int(now) - max(60, BROAD_BURST_WINDOW_SECONDS)
    candidates: list[RowDict] = []

    for event in events:
        try:
            claimed_at = int(event.get("claimed_at") or 0)
            user_created_at = int(event.get("user_created_at") or 0)
            session_created_at = int(event.get("session_created_at") or 0)
        except (TypeError, ValueError):
            continue

        if claimed_at < cutoff:
            continue
        user_age = claimed_at - user_created_at
        session_age = claimed_at - session_created_at
        if user_age < 0 or session_age < 0:
            continue
        if (
            user_age > claim_security.NEW_IDENTITY_MAX_AGE_SECONDS
            or session_age > claim_security.NEW_IDENTITY_MAX_AGE_SECONDS
        ):
            continue
        candidates.append(event)

    minimum = max(4, BROAD_BURST_MIN_IDENTITIES)
    if len(candidates) < minimum:
        return []

    devices = {
        str(event.get("device_id_hash") or "")
        for event in candidates
        if event.get("device_id_hash")
    }
    wallets = {
        str(event.get("verified_wallet") or "")
        for event in candidates
        if event.get("verified_wallet")
    }
    spots = {
        int(event.get("spot_id") or 0)
        for event in candidates
        if int(event.get("spot_id") or 0) > 0
    }
    if min(len(devices), len(wallets), len(spots)) < minimum:
        return []

    if claim_security._max_spread_metres(candidates) < max(
        10_000,
        BROAD_BURST_MIN_SPREAD_METRES,
    ):
        return []

    return sorted(
        {
            int(event["claim_id"])
            for event in candidates
            if int(event.get("claim_id") or 0) > 0
        }
    )


async def _record_claim_event_with_broad_burst(
    *,
    claim_id: int,
    session: RowDict,
    request_body: RowDict,
    ip_fingerprint: str,
) -> None:
    await _ORIGINAL_RECORD_CLAIM_EVENT(
        claim_id=int(claim_id),
        session=session,
        request_body=request_body,
        ip_fingerprint=ip_fingerprint,
    )

    async with claim_security.get_db() as db:
        async with db_access.transaction(db, immediate=True):
            now = await db_access.get_unixepoch(db)
            events = await claim_security._load_recent_events(db, now=now)
            suspicious_ids = broad_new_identity_burst_claim_ids(events, now=now)
            if suspicious_ids:
                await claim_security._mark_manual_review(
                    db,
                    claim_ids=suspicious_ids,
                    reason="coordinated_new_identity_geographic_burst",
                    now=now,
                )


async def _verified_wallet_user_ids(db, *, wallet_address: str) -> list[int]:
    """Return every NimHunt device-user durably bound to one verified wallet."""
    canonical = claim_security._canonical_optional_address(wallet_address)
    if canonical is None:
        return []

    rows = await db.execute_fetchall(
        f"""
        SELECT {schema.APP_METADATA_VALUE} AS value
        FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} LIKE ?;
        """,
        (f"{claim_security.USER_BINDING_PREFIX}%",),
    )
    user_ids: set[int] = set()
    for row in rows:
        try:
            binding = json.loads(str(row["value"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(binding, dict):
            continue
        bound_wallet = claim_security._canonical_optional_address(binding.get("wallet_address"))
        if bound_wallet != canonical:
            continue
        try:
            user_id = int(binding.get("user_id"))
        except (TypeError, ValueError):
            continue
        if user_id > 0:
            user_ids.add(user_id)
    return sorted(user_ids)


async def _wallet_active_claim_count(
    db,
    *,
    spot_id: int,
    wallet_address: str,
) -> int:
    """Count Spot claims that consume the per-user allowance for one wallet."""
    user_ids = await _verified_wallet_user_ids(db, wallet_address=wallet_address)
    if not user_ids:
        return 0

    statuses = [const.CLAIM_STATUS_SUCCESS]
    if await db_access.is_prizedraw(db, spot_id=int(spot_id)):
        statuses.append(const.CLAIM_STATUS_PENDING)

    user_placeholders = ",".join("?" for _ in user_ids)
    status_placeholders = ",".join("?" for _ in statuses)
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.CLAIM_TABLE_NAME}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_RECIPIENT} IN ({user_placeholders})
          AND {schema.CLAIM_STATUS} IN ({status_placeholders});
        """,
        (int(spot_id), *user_ids, *statuses),
    )
    row = await cur.fetchone()
    return int(row["n"] or 0)


async def _wallet_has_reached_spot_limit(
    db,
    *,
    spot_id: int,
    wallet_address: str,
) -> bool:
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        return True
    max_per_user = int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 0)
    if max_per_user <= 0:
        return False
    return (
        await _wallet_active_claim_count(
            db,
            spot_id=int(spot_id),
            wallet_address=wallet_address,
        )
        >= max_per_user
    )


async def _verified_wallet_owns_spot(
    db,
    *,
    spot_id: int,
    wallet_address: str,
) -> bool:
    """Extend own-Spot protection across fresh device identities."""
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        return False

    owner_id = int(spot.get(schema.SPOT_CREATED_BY) or 0)
    if owner_id <= 0:
        return False

    owner_binding = await claim_security._metadata_get(
        db,
        claim_security._user_binding_key(owner_id),
    )
    owner_wallet = (
        claim_security._canonical_optional_address(owner_binding.get("wallet_address"))
        if isinstance(owner_binding, dict)
        else None
    )
    canonical = claim_security._canonical_optional_address(wallet_address)
    return owner_wallet is not None and canonical is not None and owner_wallet == canonical


async def _create_claim_attempt_bound_to_verified_wallet(
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
    """Bind public claim limits to the signer while preserving the Nimiq Pay payout account."""
    if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        binding = await claim_security._metadata_get(
            db,
            claim_security._user_binding_key(int(user_id)),
        )
        if not isinstance(binding, dict):
            raise ValueError("A verified Nimiq wallet is required before claiming.")

        verified_wallet = claim_security._canonical_optional_address(
            binding.get("wallet_address")
        )
        if verified_wallet is None:
            raise ValueError("The verified Nimiq wallet for this claim is invalid.")

        if await _verified_wallet_owns_spot(
            db,
            spot_id=int(spot_id),
            wallet_address=verified_wallet,
        ):
            raise ValueError("You cannot claim your own spot.")

        if await _wallet_has_reached_spot_limit(
            db,
            spot_id=int(spot_id),
            wallet_address=verified_wallet,
        ):
            raise ValueError("You have already reached your claim limit for this spot.")

        # The signature proves the anti-abuse identity; it does not make the
        # signer-derived address the documented Nimiq Pay receiving account.
        # Keep listAccounts()[0] as the payout target, but validate it server-side
        # before it is persisted to the CLAIM and later used by settlement.
        payout_address = claim_security._canonical_optional_address(payout_address)
        if payout_address is None:
            raise ValueError("A valid Nimiq Pay payout address is required before claiming.")

    delegate = _CLAIM_ATTEMPT_DELEGATE
    if delegate is None:  # pragma: no cover - runtime requires install().
        raise RuntimeError("claim payout identity binding is not installed")
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


async def _promote_claim_with_verified_wallet_limit(db, *, claim_id: int) -> RowDict | None:
    """Recheck wallet-wide limits when a standard duration claim completes."""
    delegate = _PROMOTE_CLAIM_DELEGATE
    if delegate is None:  # pragma: no cover - runtime requires install().
        raise RuntimeError("claim wallet-limit promotion guard is not installed")
    if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        return await delegate(db, claim_id=int(claim_id))

    claim = await db_access.get_claim(db, claim_id=int(claim_id))
    claim_status = claim.get(schema.CLAIM_STATUS) if claim is not None else None
    if claim is None or claim_status is None or int(claim_status) != const.CLAIM_STATUS_PENDING:
        return await delegate(db, claim_id=int(claim_id))

    spot_id = int(claim[schema.CLAIM_SPOT_ID])
    if await db_access.is_prizedraw(db, spot_id=spot_id):
        return await delegate(db, claim_id=int(claim_id))

    binding = await claim_security._metadata_get(
        db,
        claim_security._user_binding_key(int(claim[schema.CLAIM_RECIPIENT])),
    )
    verified_wallet = (
        claim_security._canonical_optional_address(binding.get("wallet_address"))
        if isinstance(binding, dict)
        else None
    )
    if verified_wallet is None:
        # Do not silently promote an unauthenticated post-upgrade duration claim.
        await db_access.set_claim_status_to_failed(db, claim_id=int(claim_id))
        return await db_access.get_claim(db, claim_id=int(claim_id))

    if await _wallet_has_reached_spot_limit(
        db,
        spot_id=spot_id,
        wallet_address=verified_wallet,
    ):
        await db_access.set_claim_status_to_failed(db, claim_id=int(claim_id))
        failed = await db_access.get_claim(db, claim_id=int(claim_id))
        if failed is not None:
            failed["capacity_promotion"] = {
                "ok": False,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "verified_wallet_claim_limit_reached",
            }
        return failed

    return await delegate(db, claim_id=int(claim_id))


def install() -> None:
    """Install the extra claim safeguards after the primary security layer."""
    global _CLAIM_ATTEMPT_DELEGATE, _PROMOTE_CLAIM_DELEGATE, _INSTALLED
    if _INSTALLED:
        return

    claim_security._preclaim_risk = _preclaim_risk_without_ip_only_block
    claim_security._record_claim_event = _record_claim_event_with_broad_burst

    # Capture at install time, after claim_code_policy / location guards have
    # wrapped these functions. Capturing at module import could bypass them.
    _CLAIM_ATTEMPT_DELEGATE = db_access.create_claim_attempt
    db_access.create_claim_attempt = _create_claim_attempt_bound_to_verified_wallet
    _PROMOTE_CLAIM_DELEGATE = db_access.promote_pending_claim_to_success_if_capacity_available
    db_access.promote_pending_claim_to_success_if_capacity_available = _promote_claim_with_verified_wallet_limit

    # Honour an explicit operator setting. Otherwise use a conservative default
    # long enough for the broad coordinated-sweep detector to observe the burst.
    if not os.getenv("NIMHUNT_CLAIM_PAYOUT_SECURITY_HOLD_SECONDS", "").strip():
        claim_security.PAYOUT_HOLD_SECONDS = max(
            int(claim_security.PAYOUT_HOLD_SECONDS),
            DEFAULT_PAYOUT_OBSERVATION_SECONDS,
        )

    _INSTALLED = True


__all__ = [
    "BROAD_BURST_MIN_IDENTITIES",
    "BROAD_BURST_MIN_SPREAD_METRES",
    "BROAD_BURST_WINDOW_SECONDS",
    "DEFAULT_PAYOUT_OBSERVATION_SECONDS",
    "broad_new_identity_burst_claim_ids",
    "install",
]
