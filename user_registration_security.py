"""Single public boundary for creating browser-backed USER rows.

Existing users are always returned before registration quotas are considered.
The quotas contain database/metrics abuse; they are not identity proof and are
deliberately independent of claim eligibility and wallet verification.
"""

from __future__ import annotations

import os
from typing import Any

import database as schema
import db_access

METADATA_KEY = "user_registration_security:recent"
WINDOW_SECONDS = int(os.getenv("NIMHUNT_USER_REGISTRATION_WINDOW_SECONDS", "3600"))
MAX_REGISTRATIONS = int(os.getenv("NIMHUNT_USER_REGISTRATION_MAX_PER_WINDOW", "100"))


class RegistrationRateLimited(ValueError):
    def __init__(self, *, retry_at: int):
        super().__init__("New device registrations are temporarily limited.")
        self.retry_at = int(retry_at)


async def get_or_create_public_user(db, *, device_id_hash: str) -> tuple[int, bool]:
    """Return an existing USER or atomically consume one creation allowance."""
    existing = await db_access.get_user(db, device_id_hash=device_id_hash)
    if existing is not None:
        return int(existing[schema.USER_ID]), False

    now = await db_access.get_unixepoch(db)
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} AS value FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;",
        (METADATA_KEY,),
    )
    row = await cur.fetchone()
    stamps: list[int] = []
    if row is not None:
        import json
        try:
            raw: Any = json.loads(str(row["value"]))
        except (TypeError, ValueError):
            raw = []
        if isinstance(raw, list):
            stamps = [int(value) for value in raw if str(value).lstrip("-").isdigit()]
    cutoff = int(now) - max(1, WINDOW_SECONDS)
    stamps = [stamp for stamp in stamps if stamp > cutoff]
    if len(stamps) >= max(1, MAX_REGISTRATIONS):
        raise RegistrationRateLimited(retry_at=min(stamps) + WINDOW_SECONDS + 1)

    user_id, created = await db_access.get_or_create_user(db, device_id_hash=device_id_hash)
    if created:
        import json
        stamps.append(int(now))
        await db.execute(
            f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} ({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) "
            "VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value;",
            (METADATA_KEY, json.dumps(stamps, separators=(",", ":"))),
        )
    return user_id, created


__all__ = ["RegistrationRateLimited", "get_or_create_public_user"]
