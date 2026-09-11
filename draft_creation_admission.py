"""Durable draft admission and bounded deposit-address derivation.

The rolling records are the resource boundary; the client arithmetic question
is only lightweight UI friction. Reservations and key-index allocation happen
atomically, while the external derivation command runs after the write
transaction has committed.
"""

from __future__ import annotations

import asyncio

import constants as const
import database as schema
import db_access
import wallet

_NEXT_KEY_INDEX = "draft_creation_admission:next_deposit_key_index"
_DERIVATION_SEMAPHORE = asyncio.Semaphore(max(1, const.DRAFT_DERIVATION_CONCURRENCY))


class DraftAdmissionLimited(ValueError):
    def __init__(self, *, code: str, retry_at: int):
        super().__init__("Draft creation is temporarily limited.")
        self.code = code
        self.retry_at = int(retry_at)


async def reserve(db, *, user_id: int) -> tuple[int, int]:
    """Atomically reserve rolling capacity and one never-reused key index.

    The caller must hold an IMMEDIATE transaction.
    """
    now = await db_access.get_unixepoch(db)
    cutoff = now - max(1, int(const.DRAFT_CREATION_WINDOW_SECONDS))
    stale_pending = now - max(1, int(const.DRAFT_RESERVATION_STALE_SECONDS))
    await db.execute(
        f"DELETE FROM {schema.DRAFT_ADMISSION_TABLE_NAME} "
        f"WHERE {schema.DRAFT_ADMISSION_CREATED_AT} <= ? AND "
        f"({schema.DRAFT_ADMISSION_STATE} = 'consumed' OR "
        f"({schema.DRAFT_ADMISSION_STATE} = 'pending' AND {schema.DRAFT_ADMISSION_CREATED_AT} <= ?));",
        (cutoff, stale_pending),
    )
    cur = await db.execute(
        f"SELECT {schema.DRAFT_ADMISSION_USER_ID} AS user_id, "
        f"{schema.DRAFT_ADMISSION_CREATED_AT} AS created_at "
        f"FROM {schema.DRAFT_ADMISSION_TABLE_NAME} ORDER BY {schema.DRAFT_ADMISSION_CREATED_AT};"
    )
    rows = await cur.fetchall()
    user_rows = [row for row in rows if int(row["user_id"]) == int(user_id)]
    window = max(1, int(const.DRAFT_CREATION_WINDOW_SECONDS))
    if len(user_rows) >= max(1, int(const.DRAFT_CREATION_PER_USER_LIMIT)):
        raise DraftAdmissionLimited(code="draft_creation_user_rate_limited", retry_at=int(user_rows[0]["created_at"]) + window + 1)
    if len(rows) >= max(1, int(const.DRAFT_CREATION_GLOBAL_LIMIT)):
        raise DraftAdmissionLimited(code="draft_creation_global_rate_limited", retry_at=int(rows[0]["created_at"]) + window + 1)

    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} AS value FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;", (_NEXT_KEY_INDEX,),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await db.execute(
            f"SELECT COALESCE(MAX({schema.SPOT_DEPOSIT_KEY_INDEX}), -1) + 1 AS value "
            f"FROM {schema.SPOT_TABLE_NAME};"
        )
        key_index = int((await cur.fetchone())["value"])
    else:
        key_index = int(row["value"])
    await db.execute(
        f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} ({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) "
        f"VALUES (?, ?) ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET "
        f"{schema.APP_METADATA_VALUE}=excluded.{schema.APP_METADATA_VALUE};",
        (_NEXT_KEY_INDEX, str(key_index + 1)),
    )
    cur = await db.execute(
        f"INSERT INTO {schema.DRAFT_ADMISSION_TABLE_NAME} "
        f"({schema.DRAFT_ADMISSION_USER_ID}, {schema.DRAFT_ADMISSION_KEY_INDEX}, {schema.DRAFT_ADMISSION_STATE}, {schema.DRAFT_ADMISSION_CREATED_AT}) "
        f"VALUES (?, ?, 'pending', ?);", (int(user_id), key_index, now),
    )
    return int(cur.lastrowid), key_index


async def derive(*, key_index: int) -> wallet.DerivedSpotAddress:
    """Run external derivation under the process-wide concurrency ceiling."""
    async with _DERIVATION_SEMAPHORE:
        return await asyncio.to_thread(wallet.derive_spot_deposit_address, int(key_index))


async def consume(db, *, reservation_id: int, user_id: int) -> None:
    cur = await db.execute(
        f"UPDATE {schema.DRAFT_ADMISSION_TABLE_NAME} SET {schema.DRAFT_ADMISSION_STATE}='consumed' "
        f"WHERE {schema.DRAFT_ADMISSION_ID}=? AND {schema.DRAFT_ADMISSION_USER_ID}=? "
        f"AND {schema.DRAFT_ADMISSION_STATE}='pending';", (int(reservation_id), int(user_id)),
    )
    if cur.rowcount != 1:
        raise RuntimeError("draft creation reservation is missing or already consumed")


async def release(db, *, reservation_id: int, user_id: int) -> None:
    """Release only failed pending work; successful/deleted drafts keep counting."""
    await db.execute(
        f"DELETE FROM {schema.DRAFT_ADMISSION_TABLE_NAME} WHERE {schema.DRAFT_ADMISSION_ID}=? "
        f"AND {schema.DRAFT_ADMISSION_USER_ID}=? AND {schema.DRAFT_ADMISSION_STATE}='pending';",
        (int(reservation_id), int(user_id)),
    )

