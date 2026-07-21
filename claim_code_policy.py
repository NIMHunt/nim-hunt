"""Consume one-time claim codes only when a claim actually succeeds.

A password-protected duration Spot may intentionally give the same code to more
than one person. Pending claims therefore record a non-exclusive candidate-code
association. The first claim that reaches SUCCESS atomically consumes the code;
later contenders using that code fail. Failed claims never consume it.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Awaitable, Callable

import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]
ClaimCreator = Callable[..., Awaitable[RowDict]]
ClaimPromoter = Callable[..., Awaitable[RowDict | None]]

_ATTEMPT_TABLE = "claim_code_attempt"
_ATTEMPT_CLAIM_ID = "claim_id"
_ATTEMPT_CODE_ID = "claim_code_id"
_ATTEMPT_CREATED_AT = "created_at"
_ATTEMPT_CODE_INDEX = "idx_claim_code_attempt_code"
_ATTEMPT_MATCH_TRIGGER = "trg_claim_code_attempt_match_spot"
_ATTEMPT_CLEANUP_TRIGGER = "trg_claim_code_attempt_cleanup"

_ORIGINAL_CREATE_CLAIM_ATTEMPT: ClaimCreator = db_access.create_claim_attempt
_ORIGINAL_PROMOTE_CLAIM: ClaimPromoter = (
    db_access.promote_pending_claim_to_success_if_capacity_available
)
_INSTALLED = False


async def _ensure_attempt_table(db) -> None:
    """Create the additive policy table and migrate old pending code claims.

    Earlier NimHunt versions wrote CLAIM_CODE.used_by as soon as a duration claim
    began. The INSERT/UPDATE pair below converts any such still-pending claim into
    the new non-exclusive association and releases the code for a fair race.
    Successful historical claims keep their used_by link unchanged.
    """
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ATTEMPT_TABLE} (
            {_ATTEMPT_CLAIM_ID} INTEGER PRIMARY KEY,
            {_ATTEMPT_CODE_ID} INTEGER NOT NULL,
            {_ATTEMPT_CREATED_AT} INTEGER NOT NULL DEFAULT (unixepoch()),

            CHECK ({_ATTEMPT_CREATED_AT} > 0),

            FOREIGN KEY ({_ATTEMPT_CLAIM_ID})
                REFERENCES {schema.CLAIM_TABLE_NAME}({schema.CLAIM_ID})
                ON DELETE CASCADE,

            FOREIGN KEY ({_ATTEMPT_CODE_ID})
                REFERENCES {schema.CLAIM_CODE_TABLE_NAME}({schema.CLAIM_CODE_ID})
                ON DELETE CASCADE
        );
        """
    )
    await db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {_ATTEMPT_CODE_INDEX}
        ON {_ATTEMPT_TABLE}({_ATTEMPT_CODE_ID});
        """
    )
    await db.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {_ATTEMPT_MATCH_TRIGGER}
        BEFORE INSERT ON {_ATTEMPT_TABLE}
        FOR EACH ROW
        WHEN (
            SELECT c.{schema.CLAIM_SPOT_ID}
            FROM {schema.CLAIM_TABLE_NAME} c
            WHERE c.{schema.CLAIM_ID} = NEW.{_ATTEMPT_CLAIM_ID}
        ) IS NOT (
            SELECT cc.{schema.CLAIM_CODE_SPOT_ID}
            FROM {schema.CLAIM_CODE_TABLE_NAME} cc
            WHERE cc.{schema.CLAIM_CODE_ID} = NEW.{_ATTEMPT_CODE_ID}
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'claim_code_attempt must join a claim and code from the same spot'
            );
        END;
        """
    )
    await db.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {_ATTEMPT_CLEANUP_TRIGGER}
        AFTER UPDATE OF {schema.CLAIM_STATUS} ON {schema.CLAIM_TABLE_NAME}
        FOR EACH ROW
        WHEN NEW.{schema.CLAIM_STATUS} IN (
            {const.CLAIM_STATUS_SUCCESS},
            {const.CLAIM_STATUS_FAILED}
        )
        BEGIN
            DELETE FROM {_ATTEMPT_TABLE}
            WHERE {_ATTEMPT_CLAIM_ID} = NEW.{schema.CLAIM_ID};
        END;
        """
    )

    # Convert still-pending claims created by the previous immediate-consumption
    # policy. Multiple future attempts may then reference the same released code.
    await db.execute(
        f"""
        INSERT OR IGNORE INTO {_ATTEMPT_TABLE} (
            {_ATTEMPT_CLAIM_ID},
            {_ATTEMPT_CODE_ID}
        )
        SELECT
            c.{schema.CLAIM_ID},
            cc.{schema.CLAIM_CODE_ID}
        FROM {schema.CLAIM_TABLE_NAME} c
        JOIN {schema.CLAIM_CODE_TABLE_NAME} cc
            ON cc.{schema.CLAIM_CODE_USED_BY} = c.{schema.CLAIM_ID}
        WHERE c.{schema.CLAIM_STATUS} = ?;
        """,
        (const.CLAIM_STATUS_PENDING,),
    )
    await db.execute(
        f"""
        UPDATE {schema.CLAIM_CODE_TABLE_NAME}
        SET {schema.CLAIM_CODE_USED_BY} = NULL
        WHERE {schema.CLAIM_CODE_USED_BY} IN (
            SELECT c.{schema.CLAIM_ID}
            FROM {schema.CLAIM_TABLE_NAME} c
            WHERE c.{schema.CLAIM_STATUS} = ?
        );
        """,
        (const.CLAIM_STATUS_PENDING,),
    )
    await db.execute(
        f"""
        DELETE FROM {_ATTEMPT_TABLE}
        WHERE {_ATTEMPT_CLAIM_ID} IN (
            SELECT c.{schema.CLAIM_ID}
            FROM {schema.CLAIM_TABLE_NAME} c
            WHERE c.{schema.CLAIM_STATUS} != ?
        );
        """,
        (const.CLAIM_STATUS_PENDING,),
    )


async def _create_attempt(db, *, claim_id: int, claim_code_id: int) -> None:
    await db.execute(
        f"""
        INSERT INTO {_ATTEMPT_TABLE} (
            {_ATTEMPT_CLAIM_ID},
            {_ATTEMPT_CODE_ID}
        )
        VALUES (?, ?);
        """,
        (int(claim_id), int(claim_code_id)),
    )


async def _attempt_for_claim(db, *, claim_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT
            a.{_ATTEMPT_CLAIM_ID},
            a.{_ATTEMPT_CODE_ID},
            a.{_ATTEMPT_CREATED_AT},
            cc.{schema.CLAIM_CODE_SPOT_ID},
            cc.{schema.CLAIM_CODE_CODE},
            cc.{schema.CLAIM_CODE_USED_BY}
        FROM {_ATTEMPT_TABLE} a
        JOIN {schema.CLAIM_CODE_TABLE_NAME} cc
            ON cc.{schema.CLAIM_CODE_ID} = a.{_ATTEMPT_CODE_ID}
        WHERE a.{_ATTEMPT_CLAIM_ID} = ?;
        """,
        (int(claim_id),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _release_code_from_claim(db, *, claim_id: int) -> None:
    await db.execute(
        f"""
        UPDATE {schema.CLAIM_CODE_TABLE_NAME}
        SET {schema.CLAIM_CODE_USED_BY} = NULL
        WHERE {schema.CLAIM_CODE_USED_BY} = ?;
        """,
        (int(claim_id),),
    )


def _failed_promotion(
    claim: RowDict | None,
    *,
    claim_id: int,
    spot_id: int,
    reason: str,
) -> RowDict | None:
    if claim is not None:
        claim["capacity_promotion"] = {
            "ok": False,
            "claim_id": int(claim_id),
            "spot_id": int(spot_id),
            "reason": str(reason),
        }
    return claim


async def promote_pending_claim_to_success_if_code_available(
    db,
    *,
    claim_id: int,
) -> RowDict | None:
    """Consume a candidate code and promote the claim in one DB transaction."""
    await _ensure_attempt_table(db)

    claim = await db_access.get_claim(db, claim_id=int(claim_id))
    if claim is None:
        return None
    if int(claim[schema.CLAIM_STATUS]) != const.CLAIM_STATUS_PENDING:
        return claim

    spot_id = int(claim[schema.CLAIM_SPOT_ID])
    spot = await db_access.get_spot(db, spot_id=spot_id)
    if spot is None:
        return claim

    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
    code_consumed = False
    if use_password:
        attempt = await _attempt_for_claim(db, claim_id=int(claim_id))
        if attempt is None:
            await db_access.set_claim_status_to_failed(db, claim_id=int(claim_id))
            failed = await db_access.get_claim(db, claim_id=int(claim_id))
            return _failed_promotion(
                failed,
                claim_id=claim_id,
                spot_id=spot_id,
                reason="claim_code_attempt_missing",
            )

        code_id = int(attempt[_ATTEMPT_CODE_ID])
        cur = await db.execute(
            f"""
            UPDATE {schema.CLAIM_CODE_TABLE_NAME}
            SET {schema.CLAIM_CODE_USED_BY} = ?
            WHERE {schema.CLAIM_CODE_ID} = ?
              AND (
                    {schema.CLAIM_CODE_USED_BY} IS NULL
                    OR {schema.CLAIM_CODE_USED_BY} = ?
              )
            RETURNING {schema.CLAIM_CODE_ID};
            """,
            (int(claim_id), code_id, int(claim_id)),
        )
        code_row = await cur.fetchone()
        if code_row is None:
            await db_access.set_claim_status_to_failed(db, claim_id=int(claim_id))
            failed = await db_access.get_claim(db, claim_id=int(claim_id))
            return _failed_promotion(
                failed,
                claim_id=claim_id,
                spot_id=spot_id,
                reason="claim_code_already_used",
            )
        code_consumed = True

    try:
        result = await _ORIGINAL_PROMOTE_CLAIM(db, claim_id=int(claim_id))
    except Exception:
        if code_consumed:
            await _release_code_from_claim(db, claim_id=int(claim_id))
        raise

    succeeded = bool(
        result is not None
        and int(result.get(schema.CLAIM_STATUS) or -1) == const.CLAIM_STATUS_SUCCESS
    )
    if code_consumed and not succeeded:
        # Capacity may have been won by a different claim after this claim consumed
        # its code but before the original conditional SUCCESS update completed.
        await _release_code_from_claim(db, claim_id=int(claim_id))
    return result


async def create_claim_attempt_success_only(
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
    """Create a claim while keeping its code available until final success."""
    await _ensure_attempt_table(db)

    spot = await db_access.get_spot(db, spot_id=spot_id)
    if spot is None:
        raise ValueError("This spot could not be found.")

    rule = await db_access.get_claim_rule_check(
        db,
        spot_id=spot_id,
        user_id=user_id,
        lat=lat,
        long=long,
        location_accuracy_metres=location_accuracy_metres,
    )
    if not rule["allowed"]:
        raise ValueError(rule.get("message") or "This spot cannot be claimed right now.")

    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
    clean_code = db_access._normalise_claim_code(claim_code, required=use_password)
    claim_code_id: int | None = None
    if use_password:
        existing_code = await db_access.get_claim_code_by_code(
            db,
            spot_id=spot_id,
            claim_code=clean_code,
        )
        if existing_code is None:
            raise ValueError("That claim code is not valid for this spot.")
        if existing_code.get(schema.CLAIM_CODE_USED_BY) is not None:
            raise ValueError("This code has already been used.")
        claim_code_id = int(existing_code[schema.CLAIM_CODE_ID])

    claim_duration = int(spot.get(schema.SPOT_CLAIM_DURATION) or 0)
    accuracy_score = 1.0

    if await db_access.has_spot_cancellation_started(db, spot_id=spot_id):
        raise ValueError("This spot is being cancelled and can no longer be claimed.")

    try:
        claim_id = await db_access.create_claim(
            db,
            spot_id=spot_id,
            user_id=user_id,
            lat=lat,
            long=long,
            accuracy=accuracy_score,
            payout_address=payout_address,
        )
    except sqlite3.IntegrityError as exc:
        if "spot cancellation has started" in str(exc).lower():
            raise ValueError(
                "This spot is being cancelled and can no longer be claimed."
            ) from exc
        raise

    if claim_code_id is not None:
        await _create_attempt(
            db,
            claim_id=int(claim_id),
            claim_code_id=int(claim_code_id),
        )

    if claim_duration <= 0:
        claim_after = await promote_pending_claim_to_success_if_code_available(
            db,
            claim_id=int(claim_id),
        )
        promotion = (
            claim_after.get("capacity_promotion")
            if isinstance(claim_after, dict)
            else None
        )
        if isinstance(promotion, dict) and promotion.get("ok") is False:
            reason = str(promotion.get("reason") or "")
            if reason == "claim_code_already_used":
                raise ValueError("This code has already been used.")
            if reason == "claim_code_attempt_missing":
                raise ValueError("This claim no longer has a valid code attempt.")
            raise ValueError("This spot has run out of rewards.")
        if claim_after is not None:
            return claim_after

    claim = await db_access.get_claim(db, claim_id=int(claim_id))
    if claim is None:
        raise RuntimeError("Claim was created but could not be reloaded")
    return claim


def install() -> None:
    """Install the policy once for routes and background duration settlement."""
    global _INSTALLED
    if _INSTALLED:
        return
    db_access.create_claim_attempt = create_claim_attempt_success_only
    db_access.promote_pending_claim_to_success_if_capacity_available = (
        promote_pending_claim_to_success_if_code_available
    )
    _INSTALLED = True


__all__ = [
    "create_claim_attempt_success_only",
    "install",
    "promote_pending_claim_to_success_if_code_available",
]
