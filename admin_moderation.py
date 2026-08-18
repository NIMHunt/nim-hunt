"""Financially conservative moderation workflow for severe Spot bans.

A Spot ban is intentionally different from creator cancellation:
- the Spot becomes BANNED immediately, so it disappears and no new claim payout
  intent may be created;
- already-recorded pending chain transactions are never guessed away;
- once all prior transactions are final, every confirmed unspent Luna is swept
  to NimHunt's fixed operator cancellation address.

The browser never supplies the sweep recipient or amount.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import admin_store
import cache
import constants as const
import database as schema
import db_access
import trans_updater
import wallet
from database import get_db
from transaction_descriptions import build_transaction_description

RowDict = dict[str, Any]

_INSTALLED = False
_ORIGINAL_CREATE_CLAIM_TRANSACTION = None
_ORIGINAL_CHECK_PENDING_TRANSACTIONS = None


def _allow_dev_placeholder() -> bool:
    return bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False))


def _normalise_address(value: str, *, field_name: str) -> str:
    return wallet.normalise_nimiq_address(
        value,
        field_name=field_name,
        allow_dev_placeholder=_allow_dev_placeholder(),
    )


def _financial_state(transactions: list[RowDict]) -> RowDict:
    pending = [
        row
        for row in transactions
        if int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        == const.TRANS_STATUS_PENDING
    ]
    confirmed_deposits = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    confirmed_outgoing = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) != const.TRANS_TYPE_FILL_SPOT
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    return {
        "pending": pending,
        "confirmed_deposits": confirmed_deposits,
        "confirmed_outgoing": confirmed_outgoing,
        "remaining": max(0, confirmed_deposits - confirmed_outgoing),
    }


async def _notify_ban_change(db, *, spot: RowDict) -> None:
    spot_id = int(spot[schema.SPOT_ID])
    owner_id = int(spot[schema.SPOT_CREATED_BY])
    try:
        await cache.notify_spot_changed(db, spot_id=spot_id)
        await cache.notify_claim_changed(db, spot_id=spot_id, user_id=None)
        await cache.notify_user_changed(db, user_id=owner_id)
    except Exception:
        # Durable DB state is authoritative; periodic cache refresh repairs this.
        pass


async def _fail_claims_without_committed_payout(db, *, spot_id: int) -> int:
    """Fail claims/entries that have no pending-or-confirmed payout transaction."""
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME} AS c
        SET {schema.CLAIM_STATUS} = ?,
            {schema.CLAIM_UPDATED_AT} = unixepoch()
        WHERE c.{schema.CLAIM_SPOT_ID} = ?
          AND c.{schema.CLAIM_STATUS} != ?
          AND NOT EXISTS (
                SELECT 1
                FROM {schema.TRANS_TABLE_NAME} t
                WHERE t.{schema.TRANS_CLAIM_ID} = c.{schema.CLAIM_ID}
                  AND t.{schema.TRANS_TYPE} = ?
                  AND t.{schema.TRANS_STATUS} != ?
          );
        """,
        (
            const.CLAIM_STATUS_FAILED,
            int(spot_id),
            const.CLAIM_STATUS_FAILED,
            const.TRANS_TYPE_CLAIM,
            const.TRANS_STATUS_FAILED,
        ),
    )
    return int(cur.rowcount or 0)


async def _create_ban_sweep_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
) -> int:
    """Create a moderation sweep intent only after rechecking every invariant."""
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_BANNED:
        raise ValueError("moderation sweep requires a banned Spot")
    if int(user_id) != int(spot[schema.SPOT_CREATED_BY]):
        raise ValueError("moderation sweep user does not match the Spot owner")

    expected_from = _normalise_address(
        str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="banned Spot deposit address",
    )
    submitted_from = _normalise_address(from_address, field_name="moderation sweep sender")
    if submitted_from != expected_from:
        raise ValueError("moderation sweep sender does not match the Spot deposit address")

    expected_to = _normalise_address(
        str(getattr(const, "SPOT_FEE_ADDRESS", "") or ""),
        field_name="moderation cancellation address",
    )
    submitted_to = _normalise_address(to_address, field_name="moderation sweep recipient")
    if submitted_to != expected_to:
        raise ValueError("moderation sweep recipient must be the configured cancellation address")

    transactions = await db_access.get_transactions_by_spot(
        db,
        spot_id=int(spot_id),
        limit=db_access.MAX_LIMIT,
    )
    state = _financial_state(transactions)
    if state["pending"]:
        raise RuntimeError("moderation sweep cannot start while another Spot transaction is pending")
    if int(amount) != int(state["remaining"]) or int(amount) <= 0:
        raise ValueError("moderation sweep amount no longer matches the confirmed unspent balance")

    # One active PLAT_FEE transaction per Spot is already protected by the core
    # partial unique index. Since creator cancellation is rejected before a ban
    # begins, this type is reserved here for the operator-directed moderation sweep.
    return await db_access._create_transaction(
        db,
        user_id=int(user_id),
        spot_id=int(spot_id),
        claim_id=None,
        trans_type=const.TRANS_TYPE_PLAT_FEE,
        amount=int(amount),
        from_address=expected_from,
        to_address=expected_to,
        tx_hash=tx_hash,
    )


async def attempt_banned_spot_sweep(*, spot_id: int) -> RowDict:
    """Sweep one banned Spot when all earlier chain transactions are final."""
    spot_id = int(spot_id)
    async with get_db() as db:
        await admin_store.ensure_admin_tables(db)
        spot = await db_access.get_spot(db, spot_id=spot_id)
        if spot is None:
            return {"ok": False, "spot_id": spot_id, "reason": "spot_missing"}
        if int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_BANNED:
            return {"ok": False, "spot_id": spot_id, "reason": "spot_not_banned"}

        transactions = await db_access.get_transactions_by_spot(
            db,
            spot_id=spot_id,
            limit=db_access.MAX_LIMIT,
        )
        state = _financial_state(transactions)

        if state["pending"]:
            await admin_store.upsert_ban_record(
                db,
                spot_id=spot_id,
                report_id=None,
                reason=None,
                state="pending_sweep",
            )
            await db.commit()
            return {
                "ok": True,
                "spot_id": spot_id,
                "swept": False,
                "deferred": True,
                "reason": "transactions_pending",
                "pending_transaction_count": len(state["pending"]),
            }

        remaining = int(state["remaining"])
        if remaining <= 0:
            record = await admin_store.get_ban_record(db, spot_id=spot_id)
            was_swept = bool(record and record.get("state") == "swept")
            await admin_store.upsert_ban_record(
                db,
                spot_id=spot_id,
                report_id=None,
                reason=None,
                state="swept",
            )
            if not was_swept:
                await admin_store.record_audit(
                    db,
                    action="spot_ban_sweep_complete",
                    target_type="spot",
                    target_id=spot_id,
                    detail="Banned Spot has no confirmed unspent balance remaining.",
                )
            await db.commit()
            return {
                "ok": True,
                "spot_id": spot_id,
                "swept": True,
                "amount": 0,
                "reason": "no_remaining_balance",
            }

        # A confirmed PLAT_FEE with money still remaining should be impossible in
        # the normal ban path because the whole balance is swept at once. Do not
        # manufacture a second send if historical/manual state breaks that invariant.
        existing_platform_fee = [
            row
            for row in transactions
            if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_PLAT_FEE
            and int(row.get(schema.TRANS_STATUS) or -1) != const.TRANS_STATUS_FAILED
        ]
        if existing_platform_fee:
            await admin_store.upsert_ban_record(
                db,
                spot_id=spot_id,
                report_id=None,
                reason="Existing non-failed platform-fee transaction leaves an unexpected balance.",
                state="blocked",
            )
            await db.commit()
            return {
                "ok": False,
                "spot_id": spot_id,
                "swept": False,
                "manual_review_required": True,
                "reason": "existing_platform_fee_with_remaining_balance",
                "remaining": remaining,
            }

        cancellation_address = _normalise_address(
            str(getattr(const, "SPOT_FEE_ADDRESS", "") or ""),
            field_name="moderation cancellation address",
        )

        try:
            result = await trans_updater._submit_recorded_chain_send(
                db,
                spot=spot,
                to_address=cancellation_address,
                amount=remaining,
                memo=build_transaction_description(
                    "Banned Spot",
                    spot.get(schema.SPOT_TITLE),
                ),
                intent_kind="moderation_sweep",
                intent_primary_id=spot_id,
                create_transaction=_create_ban_sweep_transaction,
                create_transaction_kwargs={
                    "user_id": int(spot[schema.SPOT_CREATED_BY]),
                    "spot_id": spot_id,
                },
                serialize_intent=True,
            )
        except (RuntimeError, sqlite3.IntegrityError) as exc:
            # If a durable outbox intent was created before a helper error, leave
            # it blocked/pending rather than risking a second broadcast.
            current = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )
            pending = _financial_state(current)["pending"]
            await admin_store.upsert_ban_record(
                db,
                spot_id=spot_id,
                report_id=None,
                reason=wallet.redact_secret_values(exc),
                state="blocked" if pending else "pending_sweep",
            )
            await db.commit()
            return {
                "ok": False,
                "spot_id": spot_id,
                "swept": False,
                "manual_review_required": bool(pending),
                "reason": "sweep_send_unresolved" if pending else "sweep_retry_pending",
            }

        await admin_store.upsert_ban_record(
            db,
            spot_id=spot_id,
            report_id=None,
            reason=None,
            state="pending_sweep",
            sweep_trans_id=int(result["trans_id"]),
            sweep_amount=remaining,
        )
        await admin_store.record_audit(
            db,
            action="spot_ban_sweep_submitted",
            target_type="spot",
            target_id=spot_id,
            detail=f"Submitted {remaining} Luna to the fixed cancellation address.",
        )
        await db.commit()
        return {
            "ok": True,
            "spot_id": spot_id,
            "swept": False,
            "submitted": True,
            "amount": remaining,
            "trans_id": int(result["trans_id"]),
            "tx_hash": result.get("tx_hash"),
        }


async def ban_spot(
    *,
    spot_id: int,
    report_id: int | None = None,
    reason: str | None = None,
) -> RowDict:
    """Immediately ban one Spot, then begin its fixed-address balance sweep."""
    spot_id = int(spot_id)
    clean_reason = str(reason or "").strip()[:2000] or "Administrator banned Spot."

    async with get_db() as db:
        await admin_store.ensure_admin_tables(db)
        async with db_access.transaction(db, immediate=True):
            spot = await db_access.get_spot(db, spot_id=spot_id)
            if spot is None:
                raise ValueError(f"spot id={spot_id} does not exist")

            status = int(spot.get(schema.SPOT_STATUS) if spot.get(schema.SPOT_STATUS) is not None else -1)
            if status == const.SPOT_STATUS_CANCELLED:
                raise ValueError("cancelled Spots cannot be converted into moderation bans")
            if (
                status != const.SPOT_STATUS_BANNED
                and spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
            ):
                raise ValueError(
                    "this Spot is already in creator cancellation; reconcile that flow before banning it"
                )

            if status != const.SPOT_STATUS_BANNED:
                await db_access.set_spot_status_to_banned(db, spot_id=spot_id)

            failed_claim_count = await _fail_claims_without_committed_payout(
                db,
                spot_id=spot_id,
            )

            if report_id is not None:
                await admin_store.set_report_status(
                    db,
                    report_id=int(report_id),
                    status=const.REPORT_STATUS_APPROVED,
                    moderator_note="Spot banned by administrator.",
                )

            await admin_store.upsert_ban_record(
                db,
                spot_id=spot_id,
                report_id=report_id,
                reason=clean_reason,
                state="pending_sweep",
            )
            await admin_store.record_audit(
                db,
                action="spot_banned",
                target_type="spot",
                target_id=spot_id,
                detail=(
                    f"{clean_reason} Claims without an already-committed payout "
                    f"were failed: {failed_claim_count}."
                ),
            )

        await _notify_ban_change(db, spot=spot)

    sweep = await attempt_banned_spot_sweep(spot_id=spot_id)
    return {
        "ok": True,
        "spot_id": spot_id,
        "banned": True,
        "failed_claim_count": failed_claim_count,
        "sweep": sweep,
    }


async def reconcile_banned_spot_sweeps(*, limit: int = 50) -> RowDict:
    """Retry/finalise pending moderation sweeps after normal chain reconciliation."""
    async with get_db() as db:
        spot_ids = await admin_store.pending_banned_spot_ids(db, limit=int(limit))

    results = [await attempt_banned_spot_sweep(spot_id=spot_id) for spot_id in spot_ids]
    return {
        "ok": all(bool(item.get("ok")) or bool(item.get("manual_review_required")) for item in results),
        "checked_count": len(results),
        "results": results,
    }


async def _guarded_create_claim_transaction(db, **kwargs):
    """Race-safe final gate: a banned Spot can never gain a new payout intent."""
    original = _ORIGINAL_CREATE_CLAIM_TRANSACTION
    if original is None:
        raise RuntimeError("admin moderation guard is not installed")

    claim_id = int(kwargs.get("claim_id"))
    claim = await db_access.get_claim(db, claim_id=claim_id)
    if claim is None:
        raise RuntimeError(f"Claim not found id={claim_id}")
    spot = await db_access.get_spot(
        db,
        spot_id=int(claim[schema.CLAIM_SPOT_ID]),
    )
    if spot is None:
        raise RuntimeError(f"Spot not found for claim id={claim_id}")
    if int(spot.get(schema.SPOT_STATUS) or -1) == const.SPOT_STATUS_BANNED:
        raise RuntimeError("claim payout blocked because the Spot has been banned")
    return await original(db, **kwargs)


async def _checked_pending_transactions_with_ban_reconciliation(*args, **kwargs):
    original = _ORIGINAL_CHECK_PENDING_TRANSACTIONS
    if original is None:
        raise RuntimeError("admin moderation reconciliation is not installed")
    result = await original(*args, **kwargs)
    try:
        result["admin_ban_sweeps"] = await reconcile_banned_spot_sweeps()
    except Exception as exc:
        result["admin_ban_sweeps"] = {
            "ok": False,
            "error": wallet.redact_secret_values(exc),
        }
    return result


def install() -> None:
    """Install moderation guards after all ordinary funding-flow wrappers."""
    global _INSTALLED, _ORIGINAL_CREATE_CLAIM_TRANSACTION, _ORIGINAL_CHECK_PENDING_TRANSACTIONS
    if _INSTALLED:
        return

    _ORIGINAL_CREATE_CLAIM_TRANSACTION = db_access.create_claim_transaction
    _ORIGINAL_CHECK_PENDING_TRANSACTIONS = trans_updater.check_pending_transactions

    db_access.create_claim_transaction = _guarded_create_claim_transaction
    trans_updater.check_pending_transactions = _checked_pending_transactions_with_ban_reconciliation
    _INSTALLED = True


__all__ = [
    "attempt_banned_spot_sweep",
    "ban_spot",
    "install",
    "reconcile_banned_spot_sweeps",
]
