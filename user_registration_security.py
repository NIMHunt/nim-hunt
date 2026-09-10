"""Single public boundary for creating browser-backed USER rows.

Existing users are always returned before registration quotas are considered.
The quotas contain database/metrics abuse; they are not identity proof and are
deliberately independent of claim eligibility and wallet verification.
"""

from __future__ import annotations

import json
import os
from typing import Any

import database as schema
import db_access

METADATA_PREFIX = "user_registration_security:recent:"
SOURCE_HOURLY_LIMIT = int(os.getenv("NIMHUNT_USER_REGISTRATION_SOURCE_HOURLY_LIMIT", "5"))
SOURCE_DAILY_LIMIT = int(os.getenv("NIMHUNT_USER_REGISTRATION_SOURCE_DAILY_LIMIT", "12"))
GLOBAL_HOURLY_LIMIT = int(os.getenv("NIMHUNT_USER_REGISTRATION_GLOBAL_HOURLY_LIMIT", "500"))


class RegistrationRateLimited(ValueError):
    def __init__(self, *, retry_at: int):
        super().__init__("New device registrations are temporarily limited.")
        self.retry_at = int(retry_at)


async def _timestamps(db, *, key: str, cutoff: int) -> list[int]:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} AS value FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;", (key,),
    )
    row = await cur.fetchone()
    if row is None:
        return []
    try:
        raw: Any = json.loads(str(row["value"]))
    except (TypeError, ValueError):
        return []
    return [int(value) for value in raw if str(value).lstrip("-").isdigit() and int(value) > cutoff]


async def _store_timestamps(db, *, key: str, stamps: list[int]) -> None:
    await db.execute(
        f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} ({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) "
        f"VALUES (?, ?) ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET "
        f"{schema.APP_METADATA_VALUE} = excluded.{schema.APP_METADATA_VALUE};",
        (key, json.dumps(stamps, separators=(",", ":"))),
    )


async def get_or_create_public_user(
    db, *, device_id_hash: str, source_network_hash: str | None = None,
) -> tuple[int, bool]:
    """Return an existing USER or atomically consume one creation allowance."""
    existing = await db_access.get_user(db, device_id_hash=device_id_hash)
    if existing is not None:
        return int(existing[schema.USER_ID]), False

    now = await db_access.get_unixepoch(db)
    source = str(source_network_hash or "unknown").strip().lower()
    source_key = f"{METADATA_PREFIX}source:{source}"
    global_key = f"{METADATA_PREFIX}global"
    source_stamps = await _timestamps(db, key=source_key, cutoff=int(now) - 24 * 60 * 60)
    global_stamps = await _timestamps(db, key=global_key, cutoff=int(now) - 60 * 60)

    hourly = [stamp for stamp in source_stamps if stamp > int(now) - 60 * 60]
    if len(hourly) >= max(1, SOURCE_HOURLY_LIMIT):
        raise RegistrationRateLimited(retry_at=min(hourly) + 60 * 60 + 1)
    if len(source_stamps) >= max(1, SOURCE_DAILY_LIMIT):
        raise RegistrationRateLimited(retry_at=min(source_stamps) + 24 * 60 * 60 + 1)
    if len(global_stamps) >= max(1, GLOBAL_HOURLY_LIMIT):
        raise RegistrationRateLimited(retry_at=min(global_stamps) + 60 * 60 + 1)

    user_id, created = await db_access.get_or_create_user(db, device_id_hash=device_id_hash)
    if created:
        source_stamps.append(int(now))
        global_stamps.append(int(now))
        await _store_timestamps(db, key=source_key, stamps=source_stamps)
        await _store_timestamps(db, key=global_key, stamps=global_stamps)
    return user_id, created


__all__ = ["RegistrationRateLimited", "get_or_create_public_user"]
