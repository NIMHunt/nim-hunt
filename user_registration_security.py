"""Durable anti-abuse controls for device-account registration.

Nimiq Pay's device hash is a useful bearer identifier, but it is supplied by
the browser and is not proof that a request came from Nimiq Pay.  In
particular, accepting an unlimited number of fresh hashes lets one script fill
the USER table and inflate the public daily-user metric.  Existing device
accounts are never restricted here; only creation of new rows is throttled.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from fastapi import Request

import claim_network_security
import constants as const
import database as schema
import db_access

METADATA_PREFIX = "user_registration_rate:"


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


HOURLY_LIMIT = _env_positive_int("NIMHUNT_USER_REGISTRATION_HOURLY_LIMIT_PER_IP", 5)
DAILY_LIMIT = _env_positive_int("NIMHUNT_USER_REGISTRATION_DAILY_LIMIT_PER_IP", 12)


class RegistrationRateLimited(Exception):
    """Raised when one source network creates too many device accounts."""

    def __init__(self, *, retry_at: int) -> None:
        super().__init__("Too many new device accounts")
        self.retry_at = int(retry_at)


def _source_fingerprint(request: Request) -> str:
    source_ip = claim_network_security.request_ip(request)
    return hashlib.sha256(
        f"nimhunt-user-registration-v1:{source_ip}".encode("utf-8")
    ).hexdigest()


async def _metadata_get(db, key: str) -> Any | None:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} AS value "
        f"FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;",
        (key,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["value"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


async def _metadata_set(db, key: str, value: Any) -> None:
    await db.execute(
        f"""
        INSERT INTO {schema.APP_METADATA_TABLE_NAME} (
            {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}
        ) VALUES (?, ?)
        ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET
            {schema.APP_METADATA_VALUE} = excluded.{schema.APP_METADATA_VALUE};
        """,
        (key, json.dumps(value, separators=(",", ":"))),
    )


async def get_or_create_user(
    db,
    *,
    request: Request,
    device_id_hash: str,
) -> tuple[int, bool]:
    """Return a device user, throttling only fresh public registrations.

    The caller must hold the normal database transaction so checking the
    durable bucket and inserting the USER row form one unit of work.
    """
    existing = await db_access.get_user(db, device_id_hash=device_id_hash)
    if existing is not None:
        return int(existing[schema.USER_ID]), False

    if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        return await db_access.create_user(db, device_id_hash=device_id_hash), True

    now = await db_access.get_unixepoch(db)
    key = f"{METADATA_PREFIX}{_source_fingerprint(request)}"
    raw = await _metadata_get(db, key)
    timestamps: list[int] = []
    if isinstance(raw, list):
        for value in raw:
            try:
                timestamp = int(value)
            except (TypeError, ValueError):
                continue
            if timestamp > int(now) - 24 * 60 * 60:
                timestamps.append(timestamp)

    hourly = [stamp for stamp in timestamps if stamp > int(now) - 60 * 60]
    retry_candidates: list[int] = []
    if len(hourly) >= HOURLY_LIMIT:
        retry_candidates.append(min(hourly) + 60 * 60 + 1)
    if len(timestamps) >= DAILY_LIMIT:
        retry_candidates.append(min(timestamps) + 24 * 60 * 60 + 1)
    if retry_candidates:
        # Persist pruning even when rejecting so malformed/expired state cannot
        # grow indefinitely under repeated traffic.
        await _metadata_set(db, key, timestamps[-DAILY_LIMIT:])
        raise RegistrationRateLimited(retry_at=max(retry_candidates))

    user_id = await db_access.create_user(db, device_id_hash=device_id_hash)
    timestamps.append(int(now))
    await _metadata_set(db, key, timestamps[-DAILY_LIMIT:])
    return int(user_id), True


__all__ = [
    "DAILY_LIMIT",
    "HOURLY_LIMIT",
    "RegistrationRateLimited",
    "get_or_create_user",
]
