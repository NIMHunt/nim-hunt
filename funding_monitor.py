"""Transaction polling, local-intent recovery and health diagnostics."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any

import constants as const
import database as schema
import db_access
import settlement_updater
import trans_updater
import wallet
from funding_status import is_real_chain_hash

RowDict = dict[str, Any]
logger = logging.getLogger(__name__)
DEFAULT_PUBLIC_POLL_SECONDS = 15
_WARNING_INTERVAL_SECONDS = 5 * 60
_RECOVERY_INTERVAL_SECONDS = 60
_INSTALLED = False
_LAST_WARNING_AT: dict[int, float] = {}
_LAST_RECOVERY_AT: dict[int, float] = {}

_ORIGINAL_CHECK_PENDING = trans_updater.check_pending_transactions
_ORIGINAL_START_REFRESHER = trans_updater.start_transaction_refresher


def _empty_recovery_result(*, error: str | None = None) -> RowDict:
    errors = [] if error is None else [{"reason": error}]
    return {
        "ok": not errors,
        "recovered_count": 0,
        "ambiguous_count": 0,
        "error_count": len(errors),
        "recovered": [],
        "ambiguous": [],
        "errors": errors,
    }


async def recover_local_creation_fee_intents() -> RowDict:
    """Recover a fee hash when broadcast succeeded but its response was lost.

    A Spot has a unique deposit address. Recovery therefore accepts only one
    unused chain transaction matching that sender, the snapshotted fee recipient
    and the exact fee amount. Missing or ambiguous matches remain pending rather
    than risking a duplicate transfer.
    """
    now = time.monotonic()
    recovered: list[RowDict] = []
    ambiguous: list[RowDict] = []
    errors: list[RowDict] = []

    async with trans_updater.get_db() as db:
        if not hasattr(db, "execute_fetchall"):
            return _empty_recovery_result()
        pending = await db_access.get_transactions_by_status(
            db,
            status=const.TRANS_STATUS_PENDING,
            limit=db_access.MAX_LIMIT,
        )
        intents = [
            row
            for row in pending
            if int(row.get(schema.TRANS_TYPE) or -1)
            == const.TRANS_TYPE_CREATION_FEE
            and not is_real_chain_hash(row.get(schema.TRANS_TX_HASH))
        ]

        for row in intents:
            trans_id = int(row[schema.TRANS_ID])
            last = _LAST_RECOVERY_AT.get(trans_id, 0.0)
            if now - last < _RECOVERY_INTERVAL_SECONDS:
                continue
            _LAST_RECOVERY_AT[trans_id] = now

            address = str(row.get(schema.TRANS_FROM_ADDRESS) or "").strip()
            if not address:
                errors.append({"trans_id": trans_id, "reason": "missing sender"})
                continue

            try:
                history = await trans_updater.get_chain_transactions_by_address(
                    address,
                    max_transactions=int(
                        getattr(const, "NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)
                    ),
                )
            except Exception as exc:
                errors.append(
                    {
                        "trans_id": trans_id,
                        "reason": wallet.redact_secret_values(exc),
                    }
                )
                continue

            matches: list[tuple[str, Any, Any]] = []
            for candidate in trans_updater._iter_candidate_transactions(history):
                tx_hash = str(
                    trans_updater._extract_chain_hash(candidate) or ""
                ).strip()
                if not tx_hash:
                    continue
                existing = await db_access.get_transaction_by_hash(db, tx_hash=tx_hash)
                if existing is not None and int(existing[schema.TRANS_ID]) != trans_id:
                    continue

                chain_status = trans_updater.ChainTransactionStatus(
                    status="confirmed",
                    tx_hash=tx_hash,
                    block_number=trans_updater._extract_block_number(candidate),
                    raw=candidate,
                    reason="recovered from deposit-address history",
                )
                verified = trans_updater._verify_chain_details_for_record(
                    row,
                    chain_status,
                )
                if verified.ok:
                    matches.append((tx_hash, chain_status, verified))

            unique = {item[0].lower(): item for item in matches}
            matches = list(unique.values())
            if len(matches) != 1:
                if len(matches) > 1:
                    ambiguous.append(
                        {"trans_id": trans_id, "candidate_count": len(matches)}
                    )
                continue

            tx_hash, chain_status, verified = matches[0]
            async with db_access.transaction(db):
                await db_access.update_transaction_chain_details(
                    db,
                    trans_id=trans_id,
                    tx_hash=tx_hash,
                    from_address=verified.from_address,
                    to_address=verified.to_address,
                    amount=verified.amount,
                    block_number=chain_status.block_number,
                )

            updated = await db_access.get_transaction(db, trans_id=trans_id)
            if updated is None:
                errors.append(
                    {"trans_id": trans_id, "reason": "intent disappeared after recovery"}
                )
                continue
            await trans_updater.mark_trans_as_confirmed(
                db,
                updated,
                block_number=chain_status.block_number,
                verified_details=verified,
            )
            recovered.append(
                {
                    "trans_id": trans_id,
                    "spot_id": row.get(schema.TRANS_SPOT_ID),
                    "tx_hash": tx_hash,
                }
            )
            logger.warning(
                "Recovered creation-fee chain hash: trans_id=%s spot_id=%s tx_hash=%s",
                trans_id,
                row.get(schema.TRANS_SPOT_ID),
                tx_hash,
            )

    for item in ambiguous:
        logger.error(
            "Creation-fee intent has multiple matching chain transactions: trans_id=%s candidates=%s",
            item.get("trans_id"),
            item.get("candidate_count"),
        )
    for item in errors:
        logger.error(
            "Creation-fee intent recovery failed: trans_id=%s reason=%s",
            item.get("trans_id"),
            item.get("reason"),
        )
    return {
        "ok": not errors and not ambiguous,
        "recovered_count": len(recovered),
        "ambiguous_count": len(ambiguous),
        "error_count": len(errors),
        "recovered": recovered,
        "ambiguous": ambiguous,
        "errors": errors,
    }


async def _log_stuck_local_intents(result: RowDict) -> None:
    now = time.monotonic()
    local_ids = [
        int(item.get("trans_id"))
        for item in list(result.get("checked") or [])
        if item.get("trans_id") is not None
        and "local outbox intent" in str(item.get("reason") or "").lower()
    ]
    if not local_ids:
        return

    async with trans_updater.get_db() as db:
        if not hasattr(db, "execute_fetchall"):
            return
        for trans_id in local_ids:
            if now - _LAST_WARNING_AT.get(trans_id, 0.0) < _WARNING_INTERVAL_SECONDS:
                continue
            row = await db_access.get_transaction(db, trans_id=trans_id)
            if row is None:
                continue
            _LAST_WARNING_AT[trans_id] = now
            logger.warning(
                "Transaction remains a local outbox intent: trans_id=%s spot_id=%s type=%s. "
                "The helper did not return a usable chain hash.",
                trans_id,
                row.get(schema.TRANS_SPOT_ID),
                row.get(schema.TRANS_TYPE),
            )


async def logged_check_pending_transactions(**kwargs) -> RowDict:
    try:
        recovery = await recover_local_creation_fee_intents()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        recovery = _empty_recovery_result(error="transaction schema is not ready")
    result = await _ORIGINAL_CHECK_PENDING(**kwargs)
    result["local_intent_recovery"] = recovery

    creation_fees = result.get("creation_fees") or {}
    finalised = int(result.get("finalised_count") or 0)
    unknown = int(result.get("unknown_count") or 0)
    fee_submitted = int(creation_fees.get("submitted_count") or 0)
    fee_errors = int(creation_fees.get("error_count") or 0)
    if finalised or unknown or fee_submitted or fee_errors or recovery["recovered_count"]:
        logger.info(
            "Transaction reconciliation: checked=%s finalised=%s pending=%s unknown=%s "
            "fee_submitted=%s fee_errors=%s intents_recovered=%s",
            int(result.get("checked_count") or 0),
            finalised,
            int(result.get("still_pending_count") or 0),
            unknown,
            fee_submitted,
            fee_errors,
            recovery["recovered_count"],
        )
    await _log_stuck_local_intents(result)
    return result


def default_refresh_interval() -> int:
    raw = os.getenv("NIMHUNT_TRANSACTION_CHECK_INTERVAL_SECONDS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return DEFAULT_PUBLIC_POLL_SECONDS
    return DEFAULT_PUBLIC_POLL_SECONDS


async def start_transaction_refresher(
    *,
    run_immediately: bool = False,
    interval_seconds: int | None = None,
    fail_on_initial_error: bool = False,
) -> None:
    interval = default_refresh_interval() if interval_seconds is None else int(interval_seconds)
    await _ORIGINAL_START_REFRESHER(
        run_immediately=run_immediately,
        interval_seconds=max(1, interval),
        fail_on_initial_error=fail_on_initial_error,
    )


async def funding_flow_diagnostics() -> RowDict:
    """Return secret-free signing, transaction and settlement diagnostics."""
    status = trans_updater.transaction_refresher_status()
    last_result = status.get("last_result") if isinstance(status, dict) else None
    last_result = last_result if isinstance(last_result, dict) else {}

    rows: list[RowDict] = []
    try:
        async with trans_updater.get_db() as db:
            if hasattr(db, "execute_fetchall"):
                for transaction_status in (
                    const.TRANS_STATUS_PENDING,
                    const.TRANS_STATUS_CONFIRMED,
                    const.TRANS_STATUS_FAILED,
                ):
                    rows.extend(await db_access.get_transactions_by_status(
                        db, status=transaction_status, limit=db_access.MAX_LIMIT
                    ))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise

    type_names = {
        const.TRANS_TYPE_CREATION_FEE: "creation_fee",
        const.TRANS_TYPE_CLAIM: "claim_payout",
        const.TRANS_TYPE_CANCEL_SPOT: "spot_refund",
        const.TRANS_TYPE_PLAT_FEE: "platform_fee",
    }
    status_names = {
        const.TRANS_STATUS_PENDING: "pending",
        const.TRANS_STATUS_CONFIRMED: "confirmed",
        const.TRANS_STATUS_FAILED: "failed",
    }
    by_type: dict[str, dict[str, int]] = {
        name: {"pending": 0, "confirmed": 0, "failed": 0, "local_intents": 0}
        for name in type_names.values()
    }
    for row in rows:
        name = type_names.get(int(row.get(schema.TRANS_TYPE) or -1), "other")
        state_name = status_names.get(int(row.get(schema.TRANS_STATUS) or -1), "unknown")
        bucket = by_type.setdefault(
            name,
            {"pending": 0, "confirmed": 0, "failed": 0, "local_intents": 0},
        )
        bucket[state_name] = int(bucket.get(state_name, 0)) + 1
        if state_name == "pending" and not is_real_chain_hash(row.get(schema.TRANS_TX_HASH)):
            bucket["local_intents"] += 1

    pending = [row for row in rows if int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_PENDING]
    local_intents = [row for row in pending if not is_real_chain_hash(row.get(schema.TRANS_TX_HASH))]
    creation_fees = last_result.get("creation_fees") or {}
    settlement = settlement_updater.settlement_refresher_status()
    settlement_result = settlement.get("last_result") or {}
    standard = settlement_result.get("standard_claim_payouts") or {}
    prizedraw = settlement_result.get("prizedraw_payout_retries") or {}
    cancellations = settlement_result.get("spot_cancellations") or {}

    send_env = getattr(const, "NIMHUNT_NIMIQ_SEND_COMMAND_ENV", "NIMHUNT_NIMIQ_SEND_COMMAND")
    mnemonic_env = getattr(const, "NIMHUNT_NIMIQ_MNEMONIC_ENV", "NIMHUNT_NIMIQ_MNEMONIC")
    external_env = getattr(const, "NIMHUNT_NIMIQ_EXTERNAL_SIGNER_ENV", "NIMHUNT_NIMIQ_EXTERNAL_SIGNER")
    send_command = os.getenv(send_env, "").strip()
    mnemonic_configured = bool(os.getenv(mnemonic_env, "").strip())
    external_signer = os.getenv(external_env, "").strip().lower() in {"1", "true", "yes", "on"}

    return {
        "network": getattr(const, "NIMIQ_NETWORK", ""),
        "signing": {
            "send_command_configured": bool(send_command),
            "bundled_helper_selected": "nimiq_helper.mjs" in send_command.lower(),
            "mnemonic_configured": mnemonic_configured,
            "external_signer_enabled": external_signer,
            "signer_configured": mnemonic_configured or external_signer,
            "shared_creation_cancellation_fee_address_configured": bool(
                str(getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")).strip()
            ),
            "shared_creation_cancellation_fee_address": str(
                getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")
            ).strip() or None,
        },
        "refresher": {
            "running": bool(status.get("running")) if isinstance(status, dict) else False,
            "healthy": not bool(status.get("last_error")) if isinstance(status, dict) else False,
            "last_error": wallet.redact_secret_values(status.get("last_error") or "") or None,
            "poll_seconds": default_refresh_interval(),
            "last_checked_count": int(last_result.get("checked_count") or 0),
            "last_finalised_count": int(last_result.get("finalised_count") or 0),
            "last_unknown_count": int(last_result.get("unknown_count") or 0),
        },
        "pending_count": len(pending),
        "local_intent_count": len(local_intents),
        "last_creation_fee_error_count": int(creation_fees.get("error_count") or 0),
        "by_type": by_type,
        "settlement": {
            "running": bool(settlement.get("running")),
            "healthy": not bool(settlement.get("last_error")),
            "last_error": wallet.redact_secret_values(settlement.get("last_error") or "") or None,
            "interval_seconds": int(settlement.get("interval_seconds") or 0),
            "standard_payout_failed_count": int(standard.get("failed_count") or 0),
            "prizedraw_payout_failed_count": int(prizedraw.get("failed_count") or 0),
            "cancellation_failed_count": int(cancellations.get("failed_count") or 0),
            "cancellation_pending_count": int(cancellations.get("pending_count") or 0),
        },
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    trans_updater.check_pending_transactions = logged_check_pending_transactions
    trans_updater.start_transaction_refresher = start_transaction_refresher
    _INSTALLED = True
