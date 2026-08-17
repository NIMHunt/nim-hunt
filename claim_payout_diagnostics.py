"""Secret-free diagnostics for successful claims and recent payouts."""

from __future__ import annotations

from collections import Counter
from typing import Any

import claim_security
import constants as const
import database as schema
import db_access
import settlement_updater
from database import get_db

RowDict = dict[str, Any]


def _same_canonical_address(left: Any, right: Any) -> bool | None:
    """Compare two Nimiq addresses without ever returning either address."""
    left_address = claim_security._canonical_optional_address(left)
    right_address = claim_security._canonical_optional_address(right)
    if left_address is None or right_address is None:
        return None
    return left_address == right_address


async def _latest_confirmed_standard_payout(db) -> RowDict | None:
    """Return the latest confirmed Standard-claim payout fields needed for comparisons."""
    cur = await db.execute(
        f"""
        SELECT
            t.{schema.TRANS_CLAIM_ID} AS claim_id,
            t.{schema.TRANS_TO_ADDRESS} AS transaction_recipient,
            COALESCE(
                t.{schema.TRANS_COMPLETED_AT},
                t.{schema.TRANS_CREATED_AT}
            ) AS settled_at
        FROM {schema.TRANS_TABLE_NAME} t
        JOIN {schema.CLAIM_TABLE_NAME} c
          ON c.{schema.CLAIM_ID} = t.{schema.TRANS_CLAIM_ID}
        LEFT JOIN {schema.PRIZEDRAW_TABLE_NAME} pd
          ON pd.{schema.PRIZEDRAW_SPOT_ID} = c.{schema.CLAIM_SPOT_ID}
        WHERE t.{schema.TRANS_TYPE} = ?
          AND t.{schema.TRANS_STATUS} = ?
          AND pd.{schema.PRIZEDRAW_SPOT_ID} IS NULL
        ORDER BY
            COALESCE(
                t.{schema.TRANS_COMPLETED_AT},
                t.{schema.TRANS_CREATED_AT}
            ) DESC,
            t.{schema.TRANS_ID} DESC
        LIMIT 1;
        """,
        (const.TRANS_TYPE_CLAIM, const.TRANS_STATUS_CONFIRMED),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _latest_confirmed_standard_payout_comparison(db, *, now: int) -> RowDict:
    """Compare the three payout identities without exposing their values.

    The three relevant addresses are:
    - the receiving target persisted on CLAIM from Nimiq Pay listAccounts();
    - the recipient persisted on TRANSACTION after payout-address resolution;
    - the wallet that signed NimHunt's anti-abuse challenge.

    Only equality booleans and timing are returned by the public diagnostics.
    """
    latest = await _latest_confirmed_standard_payout(db)
    if latest is None:
        return {"present": False}

    try:
        claim_id = int(latest["claim_id"])
    except (KeyError, TypeError, ValueError):
        return {"present": False}

    claim = await db_access.get_claim(db, claim_id=claim_id)
    if claim is None:
        return {"present": True, "claim_record_present": False}

    security_record = await claim_security._metadata_get(
        db,
        claim_security._claim_record_key(claim_id),
    )
    security_record = security_record if isinstance(security_record, dict) else None

    claim_target = claim.get(schema.CLAIM_PAYOUT_ADDRESS)
    transaction_recipient = latest.get("transaction_recipient")
    security_target = (
        security_record.get("payout_address") if security_record is not None else None
    )
    verified_wallet = (
        security_record.get("verified_wallet") if security_record is not None else None
    )

    settled_at = int(latest.get("settled_at") or now)
    return {
        "present": True,
        "claim_record_present": True,
        "security_record_present": security_record is not None,
        "age_seconds": max(0, int(now) - settled_at),
        "transaction_recipient_matches_claim_target": _same_canonical_address(
            transaction_recipient,
            claim_target,
        ),
        "claim_target_matches_security_target": _same_canonical_address(
            claim_target,
            security_target,
        ),
        "claim_target_matches_verified_wallet": _same_canonical_address(
            claim_target,
            verified_wallet,
        ),
        "transaction_recipient_matches_verified_wallet": _same_canonical_address(
            transaction_recipient,
            verified_wallet,
        ),
    }


async def claim_payout_diagnostics() -> RowDict:
    """Summarise why successful Standard claims have not produced payouts yet.

    This deliberately exposes only aggregate counts, timing, and address-equality
    booleans. It never returns claim IDs, user/device identifiers, wallet
    addresses, transaction hashes, IP fingerprints, signatures, cookies, or
    other authentication material.
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

        latest_confirmed = await _latest_confirmed_standard_payout_comparison(
            db,
            now=int(now),
        )

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
        "latest_confirmed_standard_payout": latest_confirmed,
    }


__all__ = ["claim_payout_diagnostics"]
