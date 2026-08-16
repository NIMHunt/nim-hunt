"""Secret-free diagnostics for successful claims still awaiting payout."""

from __future__ import annotations

from collections import Counter
from typing import Any

import claim_security
import database as schema
import db_access
import settlement_updater
from database import get_db

RowDict = dict[str, Any]


async def claim_payout_diagnostics() -> RowDict:
    """Summarise why successful Standard claims have not produced payouts yet.

    This deliberately exposes only aggregate counts and timing. It never returns
    claim IDs, user/device identifiers, wallet addresses, IP fingerprints,
    signatures, cookies, or other authentication material.
    """
    reason_counts: Counter[str] = Counter()
    oldest_age_seconds = 0

    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        claim_ids = await db_access.get_unpaid_successful_standard_claim_ids(
            db,
            limit=db_access.MAX_LIMIT,
        )
        for claim_id in claim_ids:
            claim = await db_access.get_claim(db, claim_id=int(claim_id))
            if claim is not None:
                claimed_at = int(claim.get(schema.CLAIM_CLAIMED_AT) or now)
                oldest_age_seconds = max(oldest_age_seconds, max(0, int(now) - claimed_at))

            decision = await claim_security._payout_security_decision(
                db,
                claim_id=int(claim_id),
            )
            reason = str(decision.get("reason") or "unknown")
            reason_counts[reason] += 1

    settlement_status = settlement_updater.settlement_refresher_status()
    last_result = settlement_status.get("last_result") if isinstance(settlement_status, dict) else None
    last_result = last_result if isinstance(last_result, dict) else {}
    standard = last_result.get("standard_claim_payouts")
    standard = standard if isinstance(standard, dict) else {}

    last_result_reasons: Counter[str] = Counter()
    for result in list(standard.get("results") or []):
        if not isinstance(result, dict):
            continue
        if result.get("paid"):
            label = "submitted"
        elif result.get("deferred") or result.get("security_hold"):
            label = str(result.get("reason") or "deferred")
        elif result.get("already_exists"):
            label = str(result.get("reason") or "already_exists")
        elif not result.get("ok", True):
            label = "failed"
        else:
            label = str(result.get("reason") or "not_submitted")
        last_result_reasons[label] += 1

    return {
        "effective_security_hold_seconds": int(claim_security.PAYOUT_HOLD_SECONDS),
        "unpaid_successful_standard_count": len(claim_ids),
        "oldest_unpaid_successful_standard_age_seconds": int(oldest_age_seconds),
        "security_decision_counts": dict(sorted(reason_counts.items())),
        "last_standard_pass": {
            "checked_count": int(standard.get("checked_count") or 0),
            "submitted_count": int(standard.get("submitted_count") or 0),
            "failed_count": int(standard.get("failed_count") or 0),
            "result_reason_counts": dict(sorted(last_result_reasons.items())),
        },
    }


__all__ = ["claim_payout_diagnostics"]
