"""Durable, privacy-minimal records of security enforcement decisions."""

from __future__ import annotations

import json
from typing import Any

import constants as const
import database as schema

TABLE_NAME = "SECURITY_EVENT"
DECISION_TEMPORARY_RESTRICTION = "temporary_restriction"
DECISION_AUTOMATIC_BAN = "automatic_ban"
RETENTION_SECONDS = 90 * 24 * 60 * 60


async def ensure_table(db) -> None:
    """Create the additive event table for existing and new SQLite databases."""
    await db.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            decision_type TEXT NOT NULL CHECK (
                decision_type IN ('temporary_restriction', 'automatic_ban')
            ),
            episode_key TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            metadata TEXT,
            FOREIGN KEY (user_id) REFERENCES {schema.USER_TABLE_NAME}({schema.USER_ID})
                ON DELETE RESTRICT
        );
    """)
    await db.execute(f"CREATE INDEX IF NOT EXISTS idx_security_event_created ON {TABLE_NAME}(created_at DESC, id DESC);")
    await db.execute(f"CREATE INDEX IF NOT EXISTS idx_security_event_active ON {TABLE_NAME}(decision_type, expires_at, user_id);")


async def prune_expired(db, *, now: int) -> None:
    """Prune history unless it is the record of a still-enforced automatic ban."""
    await db.execute(
        f"""
        DELETE FROM {TABLE_NAME}
        WHERE created_at < ?
          AND NOT (
              decision_type = ?
              AND EXISTS (
                  SELECT 1 FROM {schema.USER_TABLE_NAME} u
                  WHERE u.{schema.USER_ID} = {TABLE_NAME}.user_id
                    AND u.{schema.USER_STATUS} = ?
              )
          );
        """,
        (int(now) - RETENTION_SECONDS, DECISION_AUTOMATIC_BAN, const.USER_STATUS_BANNED),
    )


async def record_decision(db, *, user_id: int, code: str, decision_type: str,
                          created_at: int, expires_at: int | None = None,
                          metadata: dict[str, Any] | None = None) -> int | None:
    """Record one enforcement episode, deduplicated by action and boundary."""
    if decision_type not in {DECISION_TEMPORARY_RESTRICTION, DECISION_AUTOMATIC_BAN}:
        raise ValueError("unsupported security decision type")
    code = str(code).strip()[:80]
    if not code:
        raise ValueError("security event code is required")
    created_at = int(created_at)
    expires_at = None if expires_at is None else int(expires_at)
    boundary = expires_at if expires_at is not None else created_at
    episode_key = f"{int(user_id)}:{code}:{decision_type}:{boundary}"
    clean_metadata = None
    if metadata:
        # Explicitly exclude coordinates, IP data, and wallet/funding addresses.
        allowed = {"signal_count", "reason_variant"}
        clean = {key: metadata[key] for key in allowed if key in metadata}
        clean_metadata = json.dumps(clean, separators=(",", ":"), sort_keys=True) if clean else None
    await ensure_table(db)
    await prune_expired(db, now=created_at)
    cur = await db.execute(f"""
        INSERT OR IGNORE INTO {TABLE_NAME}
            (user_id, code, decision_type, episode_key, created_at, expires_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?);
    """, (int(user_id), code, decision_type, episode_key, created_at, expires_at, clean_metadata))
    return int(cur.lastrowid) if cur.rowcount else None
