"""Small deterministic profanity filtering for public user-submitted text.

This module deliberately does not make contextual or AI-based judgements. It
checks a short fixed list of strong English profanity, case-insensitively. A
word may be written normally or with whitespace between its letters. Matching
letters are replaced with ``#`` while any separating whitespace is preserved.

A detected submission also starts a temporary creator/profile cooldown stored
in NimHunt's existing ``app_metadata`` table. The cooldown applies only to
changing a public display name or publishing another Spot; it does not interfere
with claims, refunds, deposits, cancellation, or settlement work.
"""

from __future__ import annotations

import json
import re
from typing import Any, Match

import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]

CONTENT_MODERATION_METADATA_KEY_PREFIX = "content_moderation_cooldown:"
CONTENT_MODERATION_COOLDOWN_SECONDS = int(
    getattr(const, "CONTENT_MODERATION_COOLDOWN_SECONDS", 60 * 60)
)

# Keep this list intentionally small and explicit. Variants are listed rather
# than inferred so that moderation behaviour remains predictable and testable.
BLOCKED_WORDS = (
    "motherfuckers",
    "motherfucker",
    "motherfucking",
    "arseholes",
    "assholes",
    "bastards",
    "bollocks",
    "fuckers",
    "fucking",
    "wankers",
    "arsehole",
    "asshole",
    "bitches",
    "faggots",
    "niggers",
    "bastard",
    "fucked",
    "fucker",
    "shitting",
    "wanking",
    "bitch",
    "faggot",
    "nigger",
    "nigga",
    "shitty",
    "wanker",
    "cunts",
    "fucks",
    "shits",
    "twats",
    "cunt",
    "fuck",
    "shit",
    "twat",
    "wank",
)


def _spaced_word_pattern(word: str) -> str:
    separator = r"\s*"
    letters = separator.join(re.escape(letter) for letter in word)
    # ASCII boundaries are deliberate because the fixed list itself is English
    # ASCII text. They prevent the classic Scunthorpe-style substring problem.
    return rf"(?<![A-Za-z0-9]){letters}(?![A-Za-z0-9])"


_PROFANITY_PATTERN = re.compile(
    "|".join(_spaced_word_pattern(word) for word in BLOCKED_WORDS),
    flags=re.IGNORECASE,
)


def _mask_match(match: Match[str]) -> str:
    return "".join("#" if character.isalpha() else character for character in match.group(0))


def censor_text(value: str | None) -> tuple[str | None, bool]:
    """Return ``(censored_text, changed)`` for one optional text value."""
    if value is None:
        return None, False
    censored, replacements = _PROFANITY_PATTERN.subn(_mask_match, str(value))
    return censored, replacements > 0


def contains_blocked_word(value: str | None) -> bool:
    """Return whether text contains one fixed blocked word."""
    if value is None:
        return False
    return _PROFANITY_PATTERN.search(str(value)) is not None


def _cooldown_key(user_id: int) -> str:
    return f"{CONTENT_MODERATION_METADATA_KEY_PREFIX}{int(user_id)}"


async def _load_cooldown(db, *, user_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT {schema.APP_METADATA_VALUE} AS value
        FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} = ?;
        """,
        (_cooldown_key(user_id),),
    )
    row = await cur.fetchone()
    if row is None:
        return None

    try:
        value = json.loads(str(row["value"]))
        if not isinstance(value, dict):
            raise ValueError("content moderation marker is not an object")
        attempted_at = int(value["attempted_at"])
        retry_at = int(value["retry_at"])
        reason = str(value.get("reason") or "blocked_language")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await clear_content_cooldown(db, user_id=user_id)
        return None

    return {
        "attempted_at": attempted_at,
        "retry_at": retry_at,
        "reason": reason,
    }


async def _save_cooldown(db, *, user_id: int, value: RowDict) -> None:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    await db.execute(
        f"""
        INSERT INTO {schema.APP_METADATA_TABLE_NAME} (
            {schema.APP_METADATA_KEY},
            {schema.APP_METADATA_VALUE}
        )
        VALUES (?, ?)
        ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET
            {schema.APP_METADATA_VALUE} = excluded.{schema.APP_METADATA_VALUE};
        """,
        (_cooldown_key(user_id), payload),
    )


async def clear_content_cooldown(db, *, user_id: int) -> None:
    await db.execute(
        f"""
        DELETE FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} = ?;
        """,
        (_cooldown_key(user_id),),
    )


async def get_content_cooldown(
    db,
    *,
    user_id: int,
    checked_at: int | None = None,
) -> RowDict | None:
    """Return an active marker, clearing it once its retry time has passed."""
    now = await db_access.get_unixepoch(db) if checked_at is None else int(checked_at)
    marker = await _load_cooldown(db, user_id=user_id)
    if marker is None:
        return None
    if int(marker["retry_at"]) <= now:
        await clear_content_cooldown(db, user_id=user_id)
        return None
    return {
        **marker,
        "retry_after_seconds": max(1, int(marker["retry_at"]) - now),
    }


async def start_content_cooldown(
    db,
    *,
    user_id: int,
    reason: str,
    checked_at: int | None = None,
) -> RowDict:
    """Create a one-hour marker without extending an already-active cooldown."""
    now = await db_access.get_unixepoch(db) if checked_at is None else int(checked_at)
    existing = await get_content_cooldown(db, user_id=user_id, checked_at=now)
    if existing is not None:
        return existing

    marker = {
        "attempted_at": now,
        "retry_at": now + max(1, CONTENT_MODERATION_COOLDOWN_SECONDS),
        "reason": str(reason or "blocked_language"),
    }
    await _save_cooldown(db, user_id=user_id, value=marker)
    return {
        **marker,
        "retry_after_seconds": max(1, int(marker["retry_at"]) - now),
    }


def format_wait(seconds: int) -> str:
    seconds = max(1, int(seconds))
    minutes = (seconds + 59) // 60
    if minutes < 60:
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    hours, remaining_minutes = divmod(minutes, 60)
    hour_text = f"{hours} hour" if hours == 1 else f"{hours} hours"
    if remaining_minutes == 0:
        return hour_text
    minute_text = (
        f"{remaining_minutes} minute"
        if remaining_minutes == 1
        else f"{remaining_minutes} minutes"
    )
    return f"{hour_text} {minute_text}"


def cooldown_api_fields(marker: RowDict, *, checked_at: int | None = None) -> RowDict:
    retry_at = int(marker["retry_at"])
    retry_after = int(marker.get("retry_after_seconds") or 0)
    if retry_after <= 0 and checked_at is not None:
        retry_after = max(1, retry_at - int(checked_at))
    retry_after = max(1, retry_after)
    return {
        "moderation_retry_at": retry_at,
        "moderation_retry_after_seconds": retry_after,
    }


def active_cooldown_message(marker: RowDict, *, checked_at: int | None = None) -> str:
    fields = cooldown_api_fields(marker, checked_at=checked_at)
    wait = format_wait(int(fields["moderation_retry_after_seconds"]))
    return (
        "Public profile changes and Spot publishing are temporarily paused "
        f"after blocked language was submitted. Try again in {wait}."
    )


async def censor_draft_spot_for_publish(db, *, spot_id: int) -> RowDict:
    """Censor a draft's title/description immediately before publication."""
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError("spot does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("only draft spots can be moderated for publication")

    title, title_changed = censor_text(spot.get(schema.SPOT_TITLE))
    description, description_changed = censor_text(spot.get(schema.SPOT_DESC))
    changed = bool(title_changed or description_changed)
    if changed:
        await db_access.modify_draft_spot(
            db,
            spot_id=int(spot_id),
            title=title,
            desc=description,
        )

    return {
        "changed": changed,
        "title_changed": bool(title_changed),
        "description_changed": bool(description_changed),
        "title": title,
        "description": description,
    }
