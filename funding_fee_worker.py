"""Creation-fee submission, retry and logging helpers."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import constants as const
import database as schema
import db_access
import trans_updater
import wallet
from transaction_descriptions import build_transaction_description

RowDict = dict[str, Any]
logger = logging.getLogger(__name__)
_INSTALLED = False

_ORIGINAL_CREATE_FEE = db_access.create_spot_creation_fee_transaction
_ORIGINAL_SUBMIT_READY = trans_updater.submit_ready_spot_creation_fees


async def get_spot_ids_ready_for_creation_fee(
    db,
    *,
    limit: int = db_access.DEFAULT_LIMIT,
) -> list[int]:
    """Include published/completed Spots so a later missing fee can be retried."""
    rows = await db.execute_fetchall(
        f"""
        SELECT s.{schema.SPOT_ID} AS spot_id
        FROM {schema.SPOT_TABLE_NAME} s
        WHERE s.{schema.SPOT_STATUS} IN (?, ?, ?)
          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
          AND s.{schema.SPOT_CREATION_FEE} > 0
          AND (
                SELECT COALESCE(SUM(t.{schema.TRANS_AMOUNT}), 0)
                FROM {schema.TRANS_TABLE_NAME} t
                WHERE t.{schema.TRANS_SPOT_ID} = s.{schema.SPOT_ID}
                  AND t.{schema.TRANS_TYPE} = ?
                  AND t.{schema.TRANS_STATUS} = ?
          ) >= s.{schema.SPOT_TOTAL_VALUE} + s.{schema.SPOT_CREATION_FEE}
          AND NOT EXISTS (
                SELECT 1
                FROM {schema.TRANS_TABLE_NAME} f
                WHERE f.{schema.TRANS_SPOT_ID} = s.{schema.SPOT_ID}
                  AND f.{schema.TRANS_TYPE} = ?
                  AND f.{schema.TRANS_STATUS} != ?
          )
        ORDER BY s.{schema.SPOT_UPDATED_AT} ASC, s.{schema.SPOT_ID} ASC
        LIMIT ?;
        """,
        (
            const.SPOT_STATUS_DRAFT,
            const.SPOT_STATUS_PUBLISHED,
            const.SPOT_STATUS_COMPLETED,
            const.TRANS_TYPE_FILL_SPOT,
            const.TRANS_STATUS_CONFIRMED,
            const.TRANS_TYPE_CREATION_FEE,
            const.TRANS_STATUS_FAILED,
            max(1, min(int(limit), int(db_access.MAX_LIMIT))),
        ),
    )
    return [int(row["spot_id"]) for row in rows]


async def create_spot_creation_fee_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
) -> int:
    """Use the original draft check, with narrow published/completed retry paths."""
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    status = int(spot[schema.SPOT_STATUS])
    if status == const.SPOT_STATUS_DRAFT:
        return await _ORIGINAL_CREATE_FEE(
            db,
            user_id=int(user_id),
            spot_id=int(spot_id),
            amount=int(amount),
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
        )
    if status not in {const.SPOT_STATUS_PUBLISHED, const.SPOT_STATUS_COMPLETED}:
        raise ValueError("creation fees can only be created for draft, published or completed spots")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("creation fee cannot be created after cancellation has started")

    expected_owner = int(spot[schema.SPOT_CREATED_BY])
    if int(user_id) != expected_owner:
        raise ValueError("creation fee user does not match the Spot owner")
    expected_amount = db_access.spot_creation_fee_amount(spot)
    if expected_amount <= 0 or int(amount) != expected_amount:
        raise ValueError("creation fee amount does not match the Spot snapshot")

    allow_dev = bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False))
    expected_from = wallet.normalise_nimiq_address(
        str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="spot deposit address",
        allow_dev_placeholder=allow_dev,
    )
    submitted_from = wallet.normalise_nimiq_address(
        from_address,
        field_name="creation fee from_address",
        allow_dev_placeholder=allow_dev,
    )
    if submitted_from != expected_from:
        raise ValueError("creation fee sender does not match the Spot deposit address")

    expected_to = wallet.normalise_nimiq_address(
        str(spot.get(schema.SPOT_CREATION_FEE_ADDRESS) or ""),
        field_name="spot creation fee address",
        allow_dev_placeholder=allow_dev,
    )
    submitted_to = wallet.normalise_nimiq_address(
        to_address,
        field_name="creation fee to_address",
        allow_dev_placeholder=allow_dev,
    )
    if submitted_to != expected_to:
        raise ValueError("creation fee recipient does not match the Spot snapshot")

    confirmed = await db_access.get_confirmed_spot_deposit_total(
        db,
        spot_id=int(spot_id),
    )
    if confirmed < db_access.spot_required_deposit_amount(spot):
        raise ValueError("creation fee cannot be created before full funding confirms")
    if await db_access.has_nonfailed_spot_creation_fee_transaction(
        db,
        spot_id=int(spot_id),
    ):
        raise RuntimeError(
            f"Spot id={spot_id} already has a non-failed creation fee transaction"
        )

    try:
        return await db_access._create_transaction(
            db,
            user_id=expected_owner,
            spot_id=int(spot_id),
            claim_id=None,
            trans_type=const.TRANS_TYPE_CREATION_FEE,
            amount=expected_amount,
            from_address=expected_from,
            to_address=expected_to,
            tx_hash=tx_hash,
        )
    except sqlite3.IntegrityError as exc:
        if await db_access.has_nonfailed_spot_creation_fee_transaction(
            db,
            spot_id=int(spot_id),
        ):
            raise RuntimeError(
                f"Spot id={spot_id} already has a non-failed creation fee transaction"
            ) from exc
        raise


async def submit_spot_creation_fee_transaction(db, *, spot_id: int) -> RowDict:
    """Submit a fee while allowing genuine helper failures to reach logs.

    The old broad ``RuntimeError`` handler treated the newly-created local intent
    as if another worker had successfully submitted the fee.  That swallowed the
    helper error and left the Spot stuck forever.  Only a real duplicate-guard
    error is converted into an idempotent no-op here.
    """
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    status_value = spot.get(schema.SPOT_STATUS)
    status = int(status_value if status_value is not None else -1)
    if status not in {
        const.SPOT_STATUS_DRAFT,
        const.SPOT_STATUS_PUBLISHED,
        const.SPOT_STATUS_COMPLETED,
    }:
        raise ValueError("creation fees can only be submitted for draft, published or completed spots")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "skipped": True,
            "reason": "cancellation_started",
            "trans_id": None,
        }

    amount = db_access.spot_creation_fee_amount(spot)
    if amount <= 0:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "skipped": True,
            "reason": "zero_amount",
            "trans_id": None,
        }

    confirmed = await db_access.get_confirmed_spot_deposit_total(
        db,
        spot_id=int(spot_id),
    )
    required = db_access.spot_required_deposit_amount(spot)
    if confirmed < required:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "skipped": True,
            "reason": "not_fully_funded",
            "confirmed_deposit_total": confirmed,
            "required_total": required,
            "trans_id": None,
        }
    if await db_access.has_nonfailed_spot_creation_fee_transaction(
        db,
        spot_id=int(spot_id),
    ):
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "already_exists": True,
            "trans_id": None,
        }

    fee_address = str(spot.get(schema.SPOT_CREATION_FEE_ADDRESS) or "").strip()
    if not fee_address:
        raise ValueError("spot creation fee address is missing")

    try:
        result = await trans_updater._submit_recorded_chain_send(
            db,
            spot=spot,
            to_address=fee_address,
            amount=amount,
            memo=build_transaction_description(
                "Creation Fee",
                spot.get(schema.SPOT_TITLE),
            ),
            intent_kind="creation_fee",
            intent_primary_id=int(spot_id),
            create_transaction=db_access.create_spot_creation_fee_transaction,
            create_transaction_kwargs={
                "user_id": int(spot[schema.SPOT_CREATED_BY]),
                "spot_id": int(spot_id),
            },
            serialize_intent=True,
        )
    except ValueError:
        current = await db_access.get_spot(db, spot_id=int(spot_id))
        if (
            current is not None
            and current.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
        ):
            return {
                "ok": True,
                "spot_id": int(spot_id),
                "skipped": True,
                "reason": "cancellation_started",
                "trans_id": None,
            }
        raise
    except sqlite3.IntegrityError:
        if await db_access.has_nonfailed_spot_creation_fee_transaction(
            db,
            spot_id=int(spot_id),
        ):
            return {
                "ok": True,
                "spot_id": int(spot_id),
                "already_exists": True,
                "trans_id": None,
            }
        raise
    except RuntimeError as exc:
        duplicate = "already has a non-failed creation fee transaction" in str(exc)
        if duplicate and await db_access.has_nonfailed_spot_creation_fee_transaction(
            db,
            spot_id=int(spot_id),
        ):
            return {
                "ok": True,
                "spot_id": int(spot_id),
                "already_exists": True,
                "trans_id": None,
            }
        raise

    return {**result, "spot_id": int(spot_id)}


async def logged_submit_ready_spot_creation_fees(
    db,
    *,
    limit: int = 50,
) -> RowDict:
    result = await _ORIGINAL_SUBMIT_READY(db, limit=int(limit))
    eligible = int(result.get("eligible_count") or 0)
    submitted = int(result.get("submitted_count") or 0)
    errors = list(result.get("errors") or [])
    if eligible or submitted or errors:
        logger.info(
            "Creation-fee reconciliation: eligible=%s submitted=%s skipped=%s errors=%s",
            eligible,
            submitted,
            int(result.get("skipped_count") or 0),
            len(errors),
        )
    for item in errors:
        logger.error(
            "Creation-fee submission failed: spot_id=%s error=%s",
            item.get("spot_id"),
            wallet.redact_secret_values(item.get("error") or "unknown error"),
        )
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    db_access.get_spot_ids_ready_for_creation_fee = get_spot_ids_ready_for_creation_fee
    db_access.create_spot_creation_fee_transaction = create_spot_creation_fee_transaction
    trans_updater.submit_spot_creation_fee_transaction = submit_spot_creation_fee_transaction
    trans_updater.submit_ready_spot_creation_fees = logged_submit_ready_spot_creation_fees
    _INSTALLED = True
