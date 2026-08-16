"""Maintenance for ephemeral claim-security metadata.

Authentication challenges, sessions and rolling rate-limit buckets are useful
only for a bounded time. Keeping expired entries forever would let repeated
requests grow app_metadata indefinitely. This module piggybacks a small cleanup
pass on NimHunt's existing settlement loop; durable claim audit records and
wallet/device bindings are intentionally retained.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import claim_security
import database as schema
import db_access
import settlement_updater
from database import get_db

RowDict = dict[str, Any]
SettlementPass = Callable[[], Awaitable[RowDict]]

logger = logging.getLogger(__name__)

MAX_ROWS_PER_PASS = 500
CLEANUP_CURSOR_KEY = f"{claim_security.METADATA_PREFIX}cleanup_cursor"
_DELEGATE: SettlementPass | None = None
_INSTALLED = False


def _is_ephemeral_key(key: str) -> bool:
    return key.startswith(
        (
            claim_security.CHALLENGE_PREFIX,
            claim_security.SESSION_PREFIX,
            claim_security.RATE_PREFIX,
        )
    )


def _prune_value(*, key: str, raw_value: str, now: int) -> tuple[str, Any | None]:
    """Return ('keep'|'update'|'delete', replacement)."""
    try:
        value = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "delete", None

    if key.startswith((claim_security.CHALLENGE_PREFIX, claim_security.SESSION_PREFIX)):
        if not isinstance(value, dict):
            return "delete", None
        try:
            expires_at = int(value.get("expires_at") or 0)
        except (TypeError, ValueError):
            return "delete", None
        return ("delete", None) if expires_at <= int(now) else ("keep", value)

    if key.startswith(claim_security.RATE_PREFIX):
        if not isinstance(value, list):
            return "delete", None
        cutoff = int(now) - int(claim_security.AUTH_RATE_WINDOW_SECONDS)
        kept: list[int] = []
        for item in value:
            try:
                stamp = int(item)
            except (TypeError, ValueError):
                continue
            if stamp > cutoff:
                kept.append(stamp)
        if not kept:
            return "delete", None
        if kept != value:
            return "update", kept
        return "keep", kept

    return "keep", value


async def _ephemeral_rows_after_cursor(
    db,
    *,
    cursor: str,
    limit: int,
) -> list[Any]:
    """Return the next lexicographic page of ephemeral metadata rows."""
    return await db.execute_fetchall(
        f"""
        SELECT {schema.APP_METADATA_KEY} AS key,
               {schema.APP_METADATA_VALUE} AS value
        FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} > ?
          AND (
                {schema.APP_METADATA_KEY} LIKE ?
             OR {schema.APP_METADATA_KEY} LIKE ?
             OR {schema.APP_METADATA_KEY} LIKE ?
          )
        ORDER BY {schema.APP_METADATA_KEY}
        LIMIT ?;
        """,
        (
            str(cursor),
            f"{claim_security.CHALLENGE_PREFIX}%",
            f"{claim_security.SESSION_PREFIX}%",
            f"{claim_security.RATE_PREFIX}%",
            int(limit),
        ),
    )


async def cleanup_expired_claim_security_metadata(*, limit: int = MAX_ROWS_PER_PASS) -> RowDict:
    """Delete/prune one rotating page of expired ephemeral security metadata.

    The last inspected key is persisted as a cursor. Without that cursor, a
    permanently-live first page (for example frequently refreshed rate-limit
    buckets) would be selected on every pass and expired rows after it would
    never be examined. When a later pass finds no rows after the cursor, that
    same pass wraps to the first ephemeral row. This keeps every row reachable
    while each settlement pass remains bounded.
    """
    limit = max(1, int(limit))
    deleted = 0
    updated = 0
    checked = 0
    wrapped = False

    async with get_db() as db:
        async with db_access.transaction(db, immediate=True):
            now = await db_access.get_unixepoch(db)
            cursor_value = await claim_security._metadata_get(db, CLEANUP_CURSOR_KEY)
            cursor = str(cursor_value) if isinstance(cursor_value, str) else ""

            rows = await _ephemeral_rows_after_cursor(
                db,
                cursor=cursor,
                limit=limit,
            )
            if not rows and cursor:
                wrapped = True
                rows = await _ephemeral_rows_after_cursor(
                    db,
                    cursor="",
                    limit=limit,
                )

            for row in rows:
                key = str(row["key"])
                if not _is_ephemeral_key(key):
                    continue
                checked += 1
                action, replacement = _prune_value(
                    key=key,
                    raw_value=str(row["value"]),
                    now=now,
                )
                if action == "delete":
                    await db.execute(
                        f"DELETE FROM {schema.APP_METADATA_TABLE_NAME} "
                        f"WHERE {schema.APP_METADATA_KEY} = ?;",
                        (key,),
                    )
                    deleted += 1
                elif action == "update":
                    await db.execute(
                        f"UPDATE {schema.APP_METADATA_TABLE_NAME} "
                        f"SET {schema.APP_METADATA_VALUE} = ? "
                        f"WHERE {schema.APP_METADATA_KEY} = ?;",
                        (
                            json.dumps(replacement, separators=(",", ":"), sort_keys=True),
                            key,
                        ),
                    )
                    updated += 1

            if rows:
                # Persist the key we inspected, even if pruning deleted that row.
                # A lexicographic cursor does not require the row itself to remain.
                await claim_security._metadata_set(
                    db,
                    CLEANUP_CURSOR_KEY,
                    str(rows[-1]["key"]),
                )
            else:
                await claim_security._metadata_delete(db, CLEANUP_CURSOR_KEY)

    return {
        "ok": True,
        "checked_count": checked,
        "deleted_count": deleted,
        "updated_count": updated,
        "wrapped": wrapped,
    }


async def run_settlement_pass_with_security_maintenance() -> RowDict:
    delegate = _DELEGATE
    if delegate is None:  # pragma: no cover - install() is required in runtime
        raise RuntimeError("claim security maintenance is not installed")

    result = await delegate()
    try:
        cleanup = await cleanup_expired_claim_security_metadata()
    except Exception as exc:  # Cleanup must never stop financial reconciliation.
        logger.exception("Claim-security metadata cleanup failed")
        cleanup = {"ok": False, "error": repr(exc)}
    return {**result, "claim_security_cleanup": cleanup}


def install() -> None:
    """Attach bounded cleanup to the existing settlement pass."""
    global _DELEGATE, _INSTALLED
    if _INSTALLED:
        return
    _DELEGATE = settlement_updater.run_settlement_pass
    settlement_updater.run_settlement_pass = run_settlement_pass_with_security_maintenance
    _INSTALLED = True


__all__ = [
    "CLEANUP_CURSOR_KEY",
    "MAX_ROWS_PER_PASS",
    "cleanup_expired_claim_security_metadata",
    "install",
    "run_settlement_pass_with_security_maintenance",
]
