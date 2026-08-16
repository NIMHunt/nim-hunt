"""Second-layer claim safeguards for public NimHunt deployments.

This module builds on ``claim_security.py`` and the Railway-aware network helper
without duplicating either. It closes four residual gaps:

* Source IP remains useful for rate limits and correlation, but is never enough
  on its own to reject a claim; carrier NAT, households and VPN exits can be
  shared by unrelated people.
* Public claim payouts are forced to the Nimiq address proven by the signed
  wallet challenge. A hostile or stale browser payout field is ignored.
* A broader coordinated-burst rule catches several brand-new identities
  claiming geographically distant Spots even when submitted coordinates are
  deliberately moved away from the exact Spot centre.
* The default payout observation window is long enough for that broader rule to
  see a fast sweep before the earliest suspicious payout is released.

All rules remain claim-specific. Nothing here globally disables Spot creation
or claiming.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

import claim_security
import constants as const
import db_access

RowDict = dict[str, Any]
ClaimAttempt = Callable[..., Awaitable[RowDict]]

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
    """Force public payouts to the wallet already proven for this device user."""
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

        # listAccounts() and the signing confirmation can legitimately refer to
        # different accounts. The cryptographically proven signer is the only
        # payout destination the server trusts, so ignore the browser field
        # rather than rejecting a legitimate claim because it is stale/different.
        payout_address = verified_wallet

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


def install() -> None:
    """Install the extra claim safeguards after the primary security layer."""
    global _CLAIM_ATTEMPT_DELEGATE, _INSTALLED
    if _INSTALLED:
        return

    claim_security._preclaim_risk = _preclaim_risk_without_ip_only_block
    claim_security._record_claim_event = _record_claim_event_with_broad_burst

    # Capture at install time, after claim_code_policy has wrapped this function.
    # Capturing at module import would silently bypass the one-time-code policy.
    _CLAIM_ATTEMPT_DELEGATE = db_access.create_claim_attempt
    db_access.create_claim_attempt = _create_claim_attempt_bound_to_verified_wallet

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
