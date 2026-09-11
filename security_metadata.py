"""Small, transaction-neutral helpers for JSON security metadata.

The caller owns transaction scope and the lifecycle policy for every keyspace.
These helpers deliberately do not commit, start writer transactions, invent keys,
or apply generic expiry rules: challenge replay state, evidence caches, and durable
financial reservations have different deletion semantics.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import database as schema

logger = logging.getLogger(__name__)


async def get_json(db, key: str, *, delete_malformed: bool = True) -> Any | None:
    """Decode one JSON value and optionally delete an undecodable row.

    An absent row and an undecodable value both return ``None``. When
    ``delete_malformed`` is true, an undecodable row is also removed within the
    caller's transaction; otherwise it is retained. The caller owns the policy
    consequence of treating malformed state as absent or deleting it.
    """
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} AS value "
        f"FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;",
        (str(key),),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["value"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        if delete_malformed:
            logger.warning("Deleting malformed JSON metadata key=%s", key)
            await delete(db, key)
        else:
            logger.warning("Retaining malformed JSON metadata key=%s", key)
        return None


async def set_json(db, key: str, value: Any) -> None:
    """Upsert compact deterministic JSON without changing transaction scope."""
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    await db.execute(
        f"""
        INSERT INTO {schema.APP_METADATA_TABLE_NAME} (
            {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}
        ) VALUES (?, ?)
        ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET
            {schema.APP_METADATA_VALUE} = excluded.{schema.APP_METADATA_VALUE};
        """,
        (str(key), payload),
    )


async def delete(db, key: str) -> None:
    """Delete one exact metadata key without committing."""
    await db.execute(
        f"DELETE FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;",
        (str(key),),
    )


async def admit_timestamp(
    db,
    *,
    key: str,
    now: int,
    window_seconds: int,
    limit: int,
) -> tuple[bool, int]:
    """Append to a bounded rolling-window timestamp bucket.

    This is a rate-admission primitive, not a concurrency or financial
    reservation. The caller must serialize competing updates when that matters.
    At most ``limit`` timestamps are retained in the one caller-selected row.
    """
    raw = await get_json(db, key)
    timestamps: list[int] = []
    if isinstance(raw, list):
        for value in raw:
            try:
                stamp = int(value)
            except (TypeError, ValueError):
                continue
            if stamp > int(now) - int(window_seconds):
                timestamps.append(stamp)

    if len(timestamps) >= int(limit):
        retry_at = min(timestamps) + int(window_seconds) + 1
        await set_json(db, key, timestamps[-int(limit) :])
        return False, retry_at

    timestamps.append(int(now))
    await set_json(db, key, timestamps[-int(limit) :])
    return True, int(now)
