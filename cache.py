"""
cache.py

Small in-memory cache helpers for NimHunt.

This cache is deliberately simple, but now has two layers:

1. SPOT cache
   - Stores published, non-expired current/upcoming SPOTs.
   - Stores related server-side detail for those SPOTs:
     creator, prizedraw data, claims, claim codes, and transactions.
   - Public serializers strip raw claim codes by default.

2. RECENT USER cache
   - Stores the X most recently seen USERs.
   - Stores lightweight Home / My History / My Spots data for those users.
   - This is a convenience cache, not the source of truth. Older users should
     still be fetched from the database directly if they are not in memory.

3. PENDING TRANSACTION cache
   - Stores every TRANSACTION that is still pending.
   - Used by trans_updater.py to cheaply poll only unresolved blockchain
     transactions.

The database remains the source of truth. The cache is here to make common page
loads cheap and to keep the frontend talking to one clean interface.
"""

from __future__ import annotations

import asyncio
import copy
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Iterable

import constants as const
import database as schema


# ---------------------------------------------------------------------------
# Types and cache state
# ---------------------------------------------------------------------------

RowDict = dict[str, Any]


@dataclass(slots=True)
class SpotCacheRecord:
    """One server-side cached SPOT record.

    Important: `claim_codes` may contain raw codes/passwords. Do not return a
    SpotCacheRecord directly from a public route.
    """

    spot_id: int
    spot: RowDict
    creator: RowDict
    prizedraw: RowDict | None
    owner_summary: RowDict | None
    claims: list[RowDict]
    claim_codes: list[RowDict]
    transactions: list[RowDict]


@dataclass(slots=True)
class SpotCacheSnapshot:
    """Immutable-by-convention snapshot of the public SPOT cache."""

    loaded_at: int
    now_at_load: int
    next_transition_at: int | None
    spots_by_id: dict[int, SpotCacheRecord]
    spot_ids_by_start: list[int]
    current_spot_ids_by_start: list[int]
    upcoming_spot_ids_by_start: list[int]


@dataclass(slots=True)
class UserCacheRecord:
    """One cached recent USER record.

    This is for authenticated/self/admin flows. Do not expose the raw `user`
    row directly because it includes the device hash.
    """

    user_id: int
    user: RowDict
    dashboard_counts: RowDict
    claims: list[RowDict]
    owner_spots: list[RowDict]
    transactions: list[RowDict]


@dataclass(slots=True)
class UserCacheSnapshot:
    """Immutable-by-convention snapshot of recently active USERs."""

    loaded_at: int
    user_limit: int
    detail_limit_per_user: int
    users_by_id: dict[int, UserCacheRecord]
    user_ids_by_last_seen: list[int]


@dataclass(slots=True)
class PendingTransactionCacheSnapshot:
    """Immutable-by-convention snapshot of unresolved TRANSACTION rows."""

    loaded_at: int
    transactions_by_id: dict[int, RowDict]
    transaction_ids_by_created: list[int]
    transaction_id_by_hash: dict[str, int]


_CACHE_LOCK = asyncio.Lock()
_SPOT_CACHE: SpotCacheSnapshot | None = None
_USER_CACHE: UserCacheSnapshot | None = None
_PENDING_TRANS_CACHE: PendingTransactionCacheSnapshot | None = None
_SPOT_CACHE_DIRTY = True
_USER_CACHE_DIRTY = True
_PENDING_TRANS_CACHE_DIRTY = True


# ---------------------------------------------------------------------------
# Background refresher state
# ---------------------------------------------------------------------------

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
SQLITE_IN_CHUNK_SIZE = 400

DEFAULT_FULL_REFRESH_SECONDS = 3 * 60 * 60
DEFAULT_RECENT_USER_CACHE_SIZE = 500
DEFAULT_USER_DETAIL_LIMIT_PER_USER = 100

_REFRESH_TASK: asyncio.Task | None = None
_REFRESH_STOP_EVENT: asyncio.Event | None = None
_REFRESH_INTERVAL_SECONDS = DEFAULT_FULL_REFRESH_SECONDS
_REFRESH_USER_LIMIT = DEFAULT_RECENT_USER_CACHE_SIZE
_REFRESH_USER_DETAIL_LIMIT = DEFAULT_USER_DETAIL_LIMIT_PER_USER
_REFRESH_LAST_ATTEMPT_AT: int | None = None
_REFRESH_LAST_SUCCESS_AT: int | None = None
_REFRESH_LAST_ERROR: str | None = None


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

async def _get_unixepoch(db) -> int:
    cur = await db.execute("SELECT unixepoch() AS now;")
    row = await cur.fetchone()
    return int(row["now"])


def _rows_to_dicts(rows: Iterable[Any]) -> list[RowDict]:
    return [dict(row) for row in rows]


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _normalise_offset(offset: int) -> int:
    return max(0, int(offset))


def _slice(items: list[int], *, limit: int, offset: int) -> list[int]:
    limit = _clamp_limit(limit)
    offset = _normalise_offset(offset)
    return items[offset : offset + limit]


def _sql_placeholders(count: int) -> str:
    if count <= 0:
        raise ValueError("count must be positive")
    return ", ".join("?" for _ in range(count))


def _chunks(values: list[int], chunk_size: int = SQLITE_IN_CHUNK_SIZE) -> Iterable[list[int]]:
    for i in range(0, len(values), chunk_size):
        yield values[i : i + chunk_size]


def _copy_row(row: RowDict | None) -> RowDict | None:
    return copy.deepcopy(row) if row is not None else None


def _copy_rows(rows: list[RowDict]) -> list[RowDict]:
    return copy.deepcopy(rows)


def _spot_start_sort_value(spot: RowDict) -> int:
    """Sort spots by start time, with always-open/null-start spots first."""
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    return 0 if starts_at is None else int(starts_at)


def _spot_absolute_ends_at(spot: RowDict) -> int | None:
    """Return absolute end time from relative SPOT.ends_at seconds."""
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    ends_after = spot.get(schema.SPOT_ENDS_AT)
    if starts_at is None or ends_after is None:
        return None
    return int(starts_at) + int(ends_after)


def _is_current_spot(spot: RowDict, *, now: int) -> bool:
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    return starts_at is None or int(starts_at) <= int(now)


def _is_upcoming_spot(spot: RowDict, *, now: int) -> bool:
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    return starts_at is not None and int(starts_at) > int(now)


def _is_in_bounds(spot: RowDict, *, min_lat: float, min_long: float, max_lat: float, max_long: float) -> bool:
    lat = float(spot[schema.SPOT_LAT])
    long = float(spot[schema.SPOT_LONG])
    return min_lat <= lat <= max_lat and min_long <= long <= max_long


def _make_prizedraw_from_spot_row(spot: RowDict) -> RowDict | None:
    """Build a small prizedraw object from the public spot-list view row."""
    prize_count = spot.get(schema.PRIZEDRAW_PRIZE_COUNT)

    if prize_count is None:
        return None

    return {
        schema.PRIZEDRAW_SPOT_ID: int(spot[schema.SPOT_ID]),
        schema.PRIZEDRAW_PRIZE_COUNT: int(prize_count or 1),
    }


def _make_creator_from_spot_row(spot: RowDict) -> RowDict:
    return {
        schema.USER_ID: int(spot[schema.SPOT_CREATED_BY]),
        schema.USER_DISPLAY_NAME: spot.get("creator_display_name"),
        schema.USER_STATUS: spot.get("creator_status"),
    }


def _rebuild_spot_indexes(
    *,
    loaded_at: int,
    now: int,
    spots_by_id: dict[int, SpotCacheRecord],
) -> SpotCacheSnapshot:
    spot_ids = list(spots_by_id.keys())
    spot_ids_by_start = sorted(
        spot_ids,
        key=lambda sid: (_spot_start_sort_value(spots_by_id[sid].spot), sid),
    )

    current_spot_ids_by_start = [
        sid for sid in spot_ids_by_start if _is_current_spot(spots_by_id[sid].spot, now=now)
    ]
    upcoming_spot_ids_by_start = [
        sid for sid in spot_ids_by_start if _is_upcoming_spot(spots_by_id[sid].spot, now=now)
    ]

    future_transitions: list[int] = []
    for sid in spot_ids_by_start:
        spot = spots_by_id[sid].spot
        starts_at = spot.get(schema.SPOT_STARTS_AT)
        ends_at = _spot_absolute_ends_at(spot)
        if starts_at is not None and int(starts_at) > now:
            future_transitions.append(int(starts_at))
        if ends_at is not None and int(ends_at) > now:
            future_transitions.append(int(ends_at))

    return SpotCacheSnapshot(
        loaded_at=loaded_at,
        now_at_load=now,
        next_transition_at=min(future_transitions) if future_transitions else None,
        spots_by_id=spots_by_id,
        spot_ids_by_start=spot_ids_by_start,
        current_spot_ids_by_start=current_spot_ids_by_start,
        upcoming_spot_ids_by_start=upcoming_spot_ids_by_start,
    )


def _strip_sensitive_user_fields(user: RowDict) -> RowDict:
    out = dict(user)
    out.pop(schema.USER_DEVICE_ID_HASH, None)
    return out


def _strip_claim_code_secret(row: RowDict) -> RowDict:
    out = dict(row)
    out.pop(schema.CLAIM_CODE_CODE, None)
    out.pop("claim_code", None)
    return out


# ---------------------------------------------------------------------------
# SPOT database extraction
# ---------------------------------------------------------------------------

async def _fetch_public_spot_rows(db) -> list[RowDict]:
    """Fetch all published, non-expired spots from the public list view."""
    rows = await db.execute_fetchall(
        f"""
        SELECT
            s.*,
            u.{schema.USER_DISPLAY_NAME} AS creator_display_name,
            u.{schema.USER_STATUS} AS creator_status
        FROM {schema.SPOT_VIEW_PUBLIC_LIST} s
        JOIN {schema.USER_TABLE_NAME} u
            ON u.{schema.USER_ID} = s.{schema.SPOT_CREATED_BY}
        ORDER BY
            CASE WHEN s.{schema.SPOT_STARTS_AT} IS NULL THEN 0 ELSE 1 END ASC,
            s.{schema.SPOT_STARTS_AT} ASC,
            s.{schema.SPOT_ID} ASC;
        """
    )
    return _rows_to_dicts(rows)


async def _fetch_one_public_spot_row(db, *, spot_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT
            s.*,
            u.{schema.USER_DISPLAY_NAME} AS creator_display_name,
            u.{schema.USER_STATUS} AS creator_status
        FROM {schema.SPOT_VIEW_PUBLIC_LIST} s
        JOIN {schema.USER_TABLE_NAME} u
            ON u.{schema.USER_ID} = s.{schema.SPOT_CREATED_BY}
        WHERE s.{schema.SPOT_ID} = ?;
        """,
        (int(spot_id),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _fetch_owner_summaries_by_spot_id(db, spot_ids: list[int]) -> dict[int, RowDict]:
    out: dict[int, RowDict] = {}
    if not spot_ids:
        return out

    for batch in _chunks(spot_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            SELECT *
            FROM {schema.SPOT_VIEW_OWNER_SUMMARY}
            WHERE {schema.SPOT_ID} IN ({placeholders});
            """,
            tuple(batch),
        )
        for row in rows:
            rec = dict(row)
            out[int(rec[schema.SPOT_ID])] = rec

    return out


async def _fetch_claims_by_spot_id(db, spot_ids: list[int]) -> dict[int, list[RowDict]]:
    out: dict[int, list[RowDict]] = {spot_id: [] for spot_id in spot_ids}
    if not spot_ids:
        return out

    for batch in _chunks(spot_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            SELECT *
            FROM {schema.CLAIM_VIEW_DETAIL}
            WHERE {schema.CLAIM_SPOT_ID} IN ({placeholders})
            ORDER BY {schema.CLAIM_SPOT_ID} ASC,
                     {schema.CLAIM_CLAIMED_AT} DESC,
                     {schema.CLAIM_ID} DESC;
            """,
            tuple(batch),
        )
        for row in rows:
            rec = dict(row)
            out.setdefault(int(rec[schema.CLAIM_SPOT_ID]), []).append(rec)

    return out


async def _fetch_claim_codes_by_spot_id(db, spot_ids: list[int]) -> dict[int, list[RowDict]]:
    out: dict[int, list[RowDict]] = {spot_id: [] for spot_id in spot_ids}
    if not spot_ids:
        return out

    for batch in _chunks(spot_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            SELECT *
            FROM {schema.CLAIM_CODE_VIEW_DETAIL}
            WHERE {schema.CLAIM_CODE_SPOT_ID} IN ({placeholders})
            ORDER BY {schema.CLAIM_CODE_SPOT_ID} ASC,
                     CASE WHEN {schema.CLAIM_CODE_USED_BY} IS NULL THEN 0 ELSE 1 END ASC,
                     {schema.CLAIM_CODE_ID} ASC;
            """,
            tuple(batch),
        )
        for row in rows:
            rec = dict(row)
            out.setdefault(int(rec[schema.CLAIM_CODE_SPOT_ID]), []).append(rec)

    return out


async def _fetch_transactions_by_spot_id(db, spot_ids: list[int]) -> dict[int, list[RowDict]]:
    out: dict[int, list[RowDict]] = {spot_id: [] for spot_id in spot_ids}
    if not spot_ids:
        return out

    for batch in _chunks(spot_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            SELECT *
            FROM {schema.TRANS_VIEW_DETAIL}
            WHERE {schema.TRANS_SPOT_ID} IN ({placeholders})
            ORDER BY {schema.TRANS_SPOT_ID} ASC,
                     {schema.TRANS_CREATED_AT} DESC,
                     {schema.TRANS_ID} DESC;
            """,
            tuple(batch),
        )
        for row in rows:
            rec = dict(row)
            out.setdefault(int(rec[schema.TRANS_SPOT_ID]), []).append(rec)

    return out


async def _build_spot_record_from_public_row(db, spot: RowDict) -> SpotCacheRecord:
    spot_id = int(spot[schema.SPOT_ID])
    owner_summaries = await _fetch_owner_summaries_by_spot_id(db, [spot_id])
    claims = await _fetch_claims_by_spot_id(db, [spot_id])
    claim_codes = await _fetch_claim_codes_by_spot_id(db, [spot_id])
    transactions = await _fetch_transactions_by_spot_id(db, [spot_id])

    return SpotCacheRecord(
        spot_id=spot_id,
        spot=spot,
        creator=_make_creator_from_spot_row(spot),
        prizedraw=_make_prizedraw_from_spot_row(spot),
        owner_summary=owner_summaries.get(spot_id),
        claims=claims.get(spot_id, []),
        claim_codes=claim_codes.get(spot_id, []),
        transactions=transactions.get(spot_id, []),
    )


async def build_spot_cache_snapshot(db) -> SpotCacheSnapshot:
    """Extract all current/upcoming published SPOT data from the database."""
    now = await _get_unixepoch(db)
    loaded_at = now

    spot_rows = await _fetch_public_spot_rows(db)
    spot_ids = [int(row[schema.SPOT_ID]) for row in spot_rows]

    owner_summaries = await _fetch_owner_summaries_by_spot_id(db, spot_ids)
    claims = await _fetch_claims_by_spot_id(db, spot_ids)
    claim_codes = await _fetch_claim_codes_by_spot_id(db, spot_ids)
    transactions = await _fetch_transactions_by_spot_id(db, spot_ids)

    spots_by_id: dict[int, SpotCacheRecord] = {}
    for spot in spot_rows:
        spot_id = int(spot[schema.SPOT_ID])
        spots_by_id[spot_id] = SpotCacheRecord(
            spot_id=spot_id,
            spot=spot,
            creator=_make_creator_from_spot_row(spot),
            prizedraw=_make_prizedraw_from_spot_row(spot),
            owner_summary=owner_summaries.get(spot_id),
            claims=claims.get(spot_id, []),
            claim_codes=claim_codes.get(spot_id, []),
            transactions=transactions.get(spot_id, []),
        )

    return _rebuild_spot_indexes(
        loaded_at=loaded_at,
        now=now,
        spots_by_id=spots_by_id,
    )


# ---------------------------------------------------------------------------
# USER database extraction
# ---------------------------------------------------------------------------

async def _fetch_recent_user_rows(db, *, limit: int) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.USER_TABLE_NAME}
        ORDER BY {schema.USER_LAST_SEEN_AT} DESC, {schema.USER_ID} DESC
        LIMIT ?;
        """,
        (max(1, int(limit)),),
    )
    return _rows_to_dicts(rows)


async def _fetch_users_by_id(db, user_ids: list[int]) -> dict[int, RowDict]:
    out: dict[int, RowDict] = {}
    if not user_ids:
        return out

    for batch in _chunks(user_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            SELECT *
            FROM {schema.USER_TABLE_NAME}
            WHERE {schema.USER_ID} IN ({placeholders});
            """,
            tuple(batch),
        )
        for row in rows:
            rec = dict(row)
            out[int(rec[schema.USER_ID])] = rec

    return out


async def _fetch_dashboard_counts_by_user_id(db, user_ids: list[int]) -> dict[int, RowDict]:
    out: dict[int, RowDict] = {
        uid: {
            "spot_count": 0,
            "published_spot_count": 0,
            "claim_count": 0,
            "successful_claim_count": 0,
        }
        for uid in user_ids
    }
    if not user_ids:
        return out

    for batch in _chunks(user_ids):
        placeholders = _sql_placeholders(len(batch))

        spot_rows = await db.execute_fetchall(
            f"""
            SELECT
                {schema.SPOT_CREATED_BY} AS user_id,
                COUNT(*) AS spot_count,
                SUM(CASE WHEN {schema.SPOT_STATUS} = {const.SPOT_STATUS_PUBLISHED} THEN 1 ELSE 0 END)
                    AS published_spot_count
            FROM {schema.SPOT_TABLE_NAME}
            WHERE {schema.SPOT_CREATED_BY} IN ({placeholders})
            GROUP BY {schema.SPOT_CREATED_BY};
            """,
            tuple(batch),
        )
        for row in spot_rows:
            uid = int(row["user_id"])
            out[uid]["spot_count"] = int(row["spot_count"] or 0)
            out[uid]["published_spot_count"] = int(row["published_spot_count"] or 0)

        claim_rows = await db.execute_fetchall(
            f"""
            SELECT
                {schema.CLAIM_RECIPIENT} AS user_id,
                COUNT(*) AS claim_count,
                SUM(CASE WHEN {schema.CLAIM_STATUS} = {const.CLAIM_STATUS_SUCCESS} THEN 1 ELSE 0 END)
                    AS successful_claim_count
            FROM {schema.CLAIM_TABLE_NAME}
            WHERE {schema.CLAIM_RECIPIENT} IN ({placeholders})
            GROUP BY {schema.CLAIM_RECIPIENT};
            """,
            tuple(batch),
        )
        for row in claim_rows:
            uid = int(row["user_id"])
            out[uid]["claim_count"] = int(row["claim_count"] or 0)
            out[uid]["successful_claim_count"] = int(row["successful_claim_count"] or 0)

    return out


async def _fetch_claims_by_user_id(
    db,
    user_ids: list[int],
    *,
    detail_limit_per_user: int,
) -> dict[int, list[RowDict]]:
    out: dict[int, list[RowDict]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return out

    for batch in _chunks(user_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            WITH ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY {schema.CLAIM_RECIPIENT}
                        ORDER BY {schema.CLAIM_CLAIMED_AT} DESC, {schema.CLAIM_ID} DESC
                    ) AS rn
                FROM {schema.CLAIM_VIEW_DETAIL}
                WHERE {schema.CLAIM_RECIPIENT} IN ({placeholders})
            )
            SELECT *
            FROM ranked
            WHERE rn <= ?
            ORDER BY {schema.CLAIM_RECIPIENT} ASC,
                     {schema.CLAIM_CLAIMED_AT} DESC,
                     {schema.CLAIM_ID} DESC;
            """,
            (*batch, int(detail_limit_per_user)),
        )
        for row in rows:
            rec = dict(row)
            rec.pop("rn", None)
            out.setdefault(int(rec[schema.CLAIM_RECIPIENT]), []).append(rec)

    return out


async def _fetch_owner_spots_by_user_id(
    db,
    user_ids: list[int],
    *,
    detail_limit_per_user: int,
) -> dict[int, list[RowDict]]:
    out: dict[int, list[RowDict]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return out

    for batch in _chunks(user_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            WITH ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY {schema.SPOT_CREATED_BY}
                        ORDER BY {schema.SPOT_CREATED_AT} DESC, {schema.SPOT_ID} DESC
                    ) AS rn
                FROM {schema.SPOT_VIEW_OWNER_SUMMARY}
                WHERE {schema.SPOT_CREATED_BY} IN ({placeholders})
            )
            SELECT *
            FROM ranked
            WHERE rn <= ?
            ORDER BY {schema.SPOT_CREATED_BY} ASC,
                     {schema.SPOT_CREATED_AT} DESC,
                     {schema.SPOT_ID} DESC;
            """,
            (*batch, int(detail_limit_per_user)),
        )
        for row in rows:
            rec = dict(row)
            rec.pop("rn", None)
            out.setdefault(int(rec[schema.SPOT_CREATED_BY]), []).append(rec)

    return out


async def _fetch_transactions_by_user_id(
    db,
    user_ids: list[int],
    *,
    detail_limit_per_user: int,
) -> dict[int, list[RowDict]]:
    out: dict[int, list[RowDict]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return out

    for batch in _chunks(user_ids):
        placeholders = _sql_placeholders(len(batch))
        rows = await db.execute_fetchall(
            f"""
            WITH ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY {schema.TRANS_USER_ID}
                        ORDER BY {schema.TRANS_CREATED_AT} DESC, {schema.TRANS_ID} DESC
                    ) AS rn
                FROM {schema.TRANS_VIEW_DETAIL}
                WHERE {schema.TRANS_USER_ID} IN ({placeholders})
            )
            SELECT *
            FROM ranked
            WHERE rn <= ?
            ORDER BY {schema.TRANS_USER_ID} ASC,
                     {schema.TRANS_CREATED_AT} DESC,
                     {schema.TRANS_ID} DESC;
            """,
            (*batch, int(detail_limit_per_user)),
        )
        for row in rows:
            rec = dict(row)
            rec.pop("rn", None)
            out.setdefault(int(rec[schema.TRANS_USER_ID]), []).append(rec)

    return out


async def _build_user_record_from_user_row(
    db,
    user: RowDict,
    *,
    detail_limit_per_user: int,
) -> UserCacheRecord:
    user_id = int(user[schema.USER_ID])
    dashboard = await _fetch_dashboard_counts_by_user_id(db, [user_id])
    claims = await _fetch_claims_by_user_id(db, [user_id], detail_limit_per_user=detail_limit_per_user)
    owner_spots = await _fetch_owner_spots_by_user_id(db, [user_id], detail_limit_per_user=detail_limit_per_user)
    transactions = await _fetch_transactions_by_user_id(db, [user_id], detail_limit_per_user=detail_limit_per_user)

    return UserCacheRecord(
        user_id=user_id,
        user=user,
        dashboard_counts=dashboard.get(user_id, {}),
        claims=claims.get(user_id, []),
        owner_spots=owner_spots.get(user_id, []),
        transactions=transactions.get(user_id, []),
    )


async def build_user_cache_snapshot(
    db,
    *,
    user_limit: int = DEFAULT_RECENT_USER_CACHE_SIZE,
    detail_limit_per_user: int = DEFAULT_USER_DETAIL_LIMIT_PER_USER,
) -> UserCacheSnapshot:
    """Extract the most recently seen users and their common page data."""
    loaded_at = await _get_unixepoch(db)
    user_rows = await _fetch_recent_user_rows(db, limit=int(user_limit))
    user_ids = [int(row[schema.USER_ID]) for row in user_rows]

    dashboard = await _fetch_dashboard_counts_by_user_id(db, user_ids)
    claims = await _fetch_claims_by_user_id(
        db,
        user_ids,
        detail_limit_per_user=int(detail_limit_per_user),
    )
    owner_spots = await _fetch_owner_spots_by_user_id(
        db,
        user_ids,
        detail_limit_per_user=int(detail_limit_per_user),
    )
    transactions = await _fetch_transactions_by_user_id(
        db,
        user_ids,
        detail_limit_per_user=int(detail_limit_per_user),
    )

    users_by_id: dict[int, UserCacheRecord] = {}
    for user in user_rows:
        user_id = int(user[schema.USER_ID])
        users_by_id[user_id] = UserCacheRecord(
            user_id=user_id,
            user=user,
            dashboard_counts=dashboard.get(user_id, {}),
            claims=claims.get(user_id, []),
            owner_spots=owner_spots.get(user_id, []),
            transactions=transactions.get(user_id, []),
        )

    user_ids_by_last_seen = sorted(
        user_ids,
        key=lambda uid: (
            -int(users_by_id[uid].user[schema.USER_LAST_SEEN_AT]),
            -int(uid),
        ),
    )

    return UserCacheSnapshot(
        loaded_at=loaded_at,
        user_limit=int(user_limit),
        detail_limit_per_user=int(detail_limit_per_user),
        users_by_id=users_by_id,
        user_ids_by_last_seen=user_ids_by_last_seen,
    )


# ---------------------------------------------------------------------------
# PENDING TRANSACTION database extraction
# ---------------------------------------------------------------------------

async def _fetch_pending_transaction_rows(db) -> list[RowDict]:
    """Fetch every TRANSACTION that is still waiting for a final outcome."""
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_STATUS} = ?
        ORDER BY {schema.TRANS_CREATED_AT} ASC, {schema.TRANS_ID} ASC;
        """,
        (const.TRANS_STATUS_PENDING,),
    )
    return _rows_to_dicts(rows)


async def _fetch_one_transaction_row(db, *, trans_id: int) -> RowDict | None:
    """Fetch one TRANSACTION detail row by id, regardless of current status."""
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_ID} = ?;
        """,
        (int(trans_id),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


def _build_pending_transaction_snapshot(
    *,
    loaded_at: int,
    transactions_by_id: dict[int, RowDict],
) -> PendingTransactionCacheSnapshot:
    """Build lookup indexes for pending TRANSACTION rows."""
    transaction_ids_by_created = sorted(
        transactions_by_id,
        key=lambda tid: (
            int(transactions_by_id[tid][schema.TRANS_CREATED_AT]),
            int(tid),
        ),
    )
    transaction_id_by_hash = {
        str(row[schema.TRANS_TX_HASH]): int(tid)
        for tid, row in transactions_by_id.items()
        if row.get(schema.TRANS_TX_HASH)
    }

    return PendingTransactionCacheSnapshot(
        loaded_at=int(loaded_at),
        transactions_by_id=transactions_by_id,
        transaction_ids_by_created=transaction_ids_by_created,
        transaction_id_by_hash=transaction_id_by_hash,
    )


async def build_pending_transaction_cache_snapshot(db) -> PendingTransactionCacheSnapshot:
    """Extract all currently pending TRANSACTION rows from the database."""
    loaded_at = await _get_unixepoch(db)
    rows = await _fetch_pending_transaction_rows(db)
    return _build_pending_transaction_snapshot(
        loaded_at=loaded_at,
        transactions_by_id={int(row[schema.TRANS_ID]): row for row in rows},
    )


# ---------------------------------------------------------------------------
# Cache lifecycle
# ---------------------------------------------------------------------------

async def refresh_spot_cache(db) -> SpotCacheSnapshot:
    """Reload the full public SPOT cache from the database."""
    global _SPOT_CACHE, _SPOT_CACHE_DIRTY

    snapshot = await build_spot_cache_snapshot(db)

    async with _CACHE_LOCK:
        _SPOT_CACHE = snapshot
        _SPOT_CACHE_DIRTY = False

    return snapshot


async def refresh_user_cache(
    db,
    *,
    user_limit: int = DEFAULT_RECENT_USER_CACHE_SIZE,
    detail_limit_per_user: int = DEFAULT_USER_DETAIL_LIMIT_PER_USER,
) -> UserCacheSnapshot:
    """Reload the recent USER cache from the database."""
    global _USER_CACHE, _USER_CACHE_DIRTY

    snapshot = await build_user_cache_snapshot(
        db,
        user_limit=int(user_limit),
        detail_limit_per_user=int(detail_limit_per_user),
    )

    async with _CACHE_LOCK:
        _USER_CACHE = snapshot
        _USER_CACHE_DIRTY = False

    return snapshot


async def refresh_pending_transaction_cache(db) -> PendingTransactionCacheSnapshot:
    """Reload the pending TRANSACTION cache from the database."""
    global _PENDING_TRANS_CACHE, _PENDING_TRANS_CACHE_DIRTY

    snapshot = await build_pending_transaction_cache_snapshot(db)

    async with _CACHE_LOCK:
        _PENDING_TRANS_CACHE = snapshot
        _PENDING_TRANS_CACHE_DIRTY = False

    return snapshot


async def refresh_all_caches(
    db,
    *,
    user_limit: int = DEFAULT_RECENT_USER_CACHE_SIZE,
    detail_limit_per_user: int = DEFAULT_USER_DETAIL_LIMIT_PER_USER,
) -> tuple[SpotCacheSnapshot, UserCacheSnapshot, PendingTransactionCacheSnapshot]:
    """Reload SPOT, recent USER, and pending TRANSACTION caches."""
    spot_snapshot = await refresh_spot_cache(db)
    user_snapshot = await refresh_user_cache(
        db,
        user_limit=user_limit,
        detail_limit_per_user=detail_limit_per_user,
    )
    pending_transaction_snapshot = await refresh_pending_transaction_cache(db)
    return spot_snapshot, user_snapshot, pending_transaction_snapshot


def mark_spot_cache_dirty() -> None:
    """Mark the SPOT cache as stale after spot-related writes."""
    global _SPOT_CACHE_DIRTY
    _SPOT_CACHE_DIRTY = True


def mark_user_cache_dirty() -> None:
    """Mark the recent USER cache as stale after user/user-page writes."""
    global _USER_CACHE_DIRTY
    _USER_CACHE_DIRTY = True


def mark_pending_transaction_cache_dirty() -> None:
    """Mark the pending TRANSACTION cache as stale after transaction writes."""
    global _PENDING_TRANS_CACHE_DIRTY
    _PENDING_TRANS_CACHE_DIRTY = True


def mark_all_caches_dirty() -> None:
    mark_spot_cache_dirty()
    mark_user_cache_dirty()
    mark_pending_transaction_cache_dirty()


def is_spot_cache_dirty() -> bool:
    return bool(_SPOT_CACHE_DIRTY)


def is_user_cache_dirty() -> bool:
    return bool(_USER_CACHE_DIRTY)


def is_pending_transaction_cache_dirty() -> bool:
    return bool(_PENDING_TRANS_CACHE_DIRTY)


def is_spot_cache_loaded() -> bool:
    return _SPOT_CACHE is not None


def is_user_cache_loaded() -> bool:
    return _USER_CACHE is not None


def is_pending_transaction_cache_loaded() -> bool:
    return _PENDING_TRANS_CACHE is not None


async def ensure_spot_cache(db) -> SpotCacheSnapshot:
    """Return a fresh-enough spot cache snapshot."""
    async with _CACHE_LOCK:
        snapshot = _SPOT_CACHE
        dirty = _SPOT_CACHE_DIRTY

    now = int(time.time())
    time_transition_due = (
        snapshot is not None
        and snapshot.next_transition_at is not None
        and now >= int(snapshot.next_transition_at)
    )

    if snapshot is None or dirty or time_transition_due:
        return await refresh_spot_cache(db)

    return snapshot


async def ensure_user_cache(
    db,
    *,
    user_limit: int = DEFAULT_RECENT_USER_CACHE_SIZE,
    detail_limit_per_user: int = DEFAULT_USER_DETAIL_LIMIT_PER_USER,
) -> UserCacheSnapshot:
    """Return a fresh-enough recent-user cache snapshot."""
    async with _CACHE_LOCK:
        snapshot = _USER_CACHE
        dirty = _USER_CACHE_DIRTY

    needs_refresh = (
        snapshot is None
        or dirty
        or int(snapshot.user_limit) != int(user_limit)
        or int(snapshot.detail_limit_per_user) != int(detail_limit_per_user)
    )
    if needs_refresh:
        return await refresh_user_cache(
            db,
            user_limit=user_limit,
            detail_limit_per_user=detail_limit_per_user,
        )

    return snapshot


async def ensure_pending_transaction_cache(db) -> PendingTransactionCacheSnapshot:
    """Return a fresh-enough pending TRANSACTION cache snapshot."""
    async with _CACHE_LOCK:
        snapshot = _PENDING_TRANS_CACHE
        dirty = _PENDING_TRANS_CACHE_DIRTY

    if snapshot is None or dirty:
        return await refresh_pending_transaction_cache(db)

    return snapshot


async def force_spot_cache_clear() -> None:
    global _SPOT_CACHE, _SPOT_CACHE_DIRTY

    async with _CACHE_LOCK:
        _SPOT_CACHE = None
        _SPOT_CACHE_DIRTY = True


async def force_user_cache_clear() -> None:
    global _USER_CACHE, _USER_CACHE_DIRTY

    async with _CACHE_LOCK:
        _USER_CACHE = None
        _USER_CACHE_DIRTY = True


async def force_pending_transaction_cache_clear() -> None:
    global _PENDING_TRANS_CACHE, _PENDING_TRANS_CACHE_DIRTY

    async with _CACHE_LOCK:
        _PENDING_TRANS_CACHE = None
        _PENDING_TRANS_CACHE_DIRTY = True


async def force_all_cache_clear() -> None:
    await force_spot_cache_clear()
    await force_user_cache_clear()
    await force_pending_transaction_cache_clear()


# ---------------------------------------------------------------------------
# Targeted refresh / write notifications
# ---------------------------------------------------------------------------

async def refresh_cached_spot(db, *, spot_id: int) -> RowDict:
    """Refresh one spot in memory.

    If the spot is no longer public/non-expired, it is removed from the public
    SPOT cache. If the cache has not loaded yet, this performs a full refresh.
    """
    global _SPOT_CACHE, _SPOT_CACHE_DIRTY

    async with _CACHE_LOCK:
        snapshot = _SPOT_CACHE

    if snapshot is None:
        await refresh_spot_cache(db)
        return {"mode": "full_refresh", "spot_id": int(spot_id)}

    now = await _get_unixepoch(db)
    spot_row = await _fetch_one_public_spot_row(db, spot_id=int(spot_id))
    new_spots = dict(snapshot.spots_by_id)

    if spot_row is None:
        existed = int(spot_id) in new_spots
        new_spots.pop(int(spot_id), None)
        action = "removed" if existed else "not_cached"
    else:
        new_spots[int(spot_id)] = await _build_spot_record_from_public_row(db, spot_row)
        action = "updated"

    new_snapshot = _rebuild_spot_indexes(
        loaded_at=snapshot.loaded_at,
        now=now,
        spots_by_id=new_spots,
    )

    async with _CACHE_LOCK:
        _SPOT_CACHE = new_snapshot
        _SPOT_CACHE_DIRTY = False

    return {"mode": "targeted_refresh", "spot_id": int(spot_id), "action": action}


async def refresh_cached_user(
    db,
    *,
    user_id: int,
    user_limit: int = DEFAULT_RECENT_USER_CACHE_SIZE,
    detail_limit_per_user: int = DEFAULT_USER_DETAIL_LIMIT_PER_USER,
) -> RowDict:
    """Refresh one recent-user record in memory.

    If the user is now outside the recent-user set, the safest small-project
    behaviour is to refresh the whole user cache. This keeps the top-X ordering
    correct after last_seen updates.
    """
    global _USER_CACHE, _USER_CACHE_DIRTY

    async with _CACHE_LOCK:
        snapshot = _USER_CACHE

    if snapshot is None:
        await refresh_user_cache(
            db,
            user_limit=user_limit,
            detail_limit_per_user=detail_limit_per_user,
        )
        return {"mode": "full_refresh", "user_id": int(user_id)}

    # Because last_seen changes can alter membership/order in the top-X set,
    # full user refresh is cleaner and still cheap at this scale.
    await refresh_user_cache(
        db,
        user_limit=snapshot.user_limit,
        detail_limit_per_user=snapshot.detail_limit_per_user,
    )
    return {"mode": "full_user_refresh", "user_id": int(user_id)}


async def notify_spot_changed(db, *, spot_id: int) -> RowDict:
    """Call after writes that affect one SPOT's public/owner state."""
    return await refresh_cached_spot(db, spot_id=int(spot_id))


async def notify_spots_changed(db, *, spot_ids: Iterable[int]) -> list[RowDict]:
    """Call after writes that affect several SPOTs."""
    results: list[RowDict] = []
    for spot_id in spot_ids:
        results.append(await refresh_cached_spot(db, spot_id=int(spot_id)))
    return results


async def notify_user_changed(db, *, user_id: int) -> RowDict:
    """Call after USER display-name/status/last-seen writes."""
    # User name/status can appear on spot creator data as well.
    mark_spot_cache_dirty()
    return await refresh_cached_user(db, user_id=int(user_id))


async def notify_claim_changed(
    db,
    *,
    spot_id: int,
    user_id: int | None = None,
) -> RowDict:
    """Call after CLAIM writes/status changes."""
    spot_result = await refresh_cached_spot(db, spot_id=int(spot_id))
    if user_id is not None:
        await refresh_cached_user(db, user_id=int(user_id))
    else:
        mark_user_cache_dirty()
    return spot_result


async def notify_claim_code_changed(db, *, spot_id: int) -> RowDict:
    """Call after CLAIM_CODE writes/used_by changes."""
    return await refresh_cached_spot(db, spot_id=int(spot_id))


async def refresh_cached_pending_transaction(db, *, trans_id: int) -> RowDict:
    """Refresh one TRANSACTION's membership in the pending cache.

    Pending rows are inserted/updated in memory. Non-pending or missing rows are
    removed from the pending cache.
    """
    global _PENDING_TRANS_CACHE, _PENDING_TRANS_CACHE_DIRTY

    async with _CACHE_LOCK:
        snapshot = _PENDING_TRANS_CACHE

    if snapshot is None:
        await refresh_pending_transaction_cache(db)
        return {"mode": "full_refresh", "trans_id": int(trans_id)}

    row = await _fetch_one_transaction_row(db, trans_id=int(trans_id))
    new_transactions = dict(snapshot.transactions_by_id)

    if row is not None and int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_PENDING:
        new_transactions[int(trans_id)] = row
        action = "updated"
    else:
        existed = int(trans_id) in new_transactions
        new_transactions.pop(int(trans_id), None)
        action = "removed" if existed else "not_cached"

    new_snapshot = _build_pending_transaction_snapshot(
        loaded_at=snapshot.loaded_at,
        transactions_by_id=new_transactions,
    )

    async with _CACHE_LOCK:
        _PENDING_TRANS_CACHE = new_snapshot
        _PENDING_TRANS_CACHE_DIRTY = False

    return {"mode": "targeted_refresh", "trans_id": int(trans_id), "action": action}


async def add_pending_transaction_to_cache(db, *, trans_id: int) -> RowDict:
    """Call immediately after creating a pending TRANSACTION row."""
    return await refresh_cached_pending_transaction(db, trans_id=int(trans_id))


async def remove_pending_transaction_from_cache(
    *,
    trans_id: int | None = None,
    tx_hash: str | None = None,
) -> RowDict:
    """Remove a TRANSACTION from the pending cache without touching the DB."""
    global _PENDING_TRANS_CACHE

    if trans_id is None and tx_hash is None:
        raise ValueError("trans_id or tx_hash is required")

    async with _CACHE_LOCK:
        snapshot = _PENDING_TRANS_CACHE
        if snapshot is None:
            return {"mode": "not_loaded", "removed": False}

        resolved_id = int(trans_id) if trans_id is not None else snapshot.transaction_id_by_hash.get(str(tx_hash))
        if resolved_id is None:
            return {"mode": "targeted_remove", "removed": False, "trans_id": None}

        new_transactions = dict(snapshot.transactions_by_id)
        removed = new_transactions.pop(int(resolved_id), None) is not None
        _PENDING_TRANS_CACHE = _build_pending_transaction_snapshot(
            loaded_at=snapshot.loaded_at,
            transactions_by_id=new_transactions,
        )

    return {"mode": "targeted_remove", "removed": removed, "trans_id": int(resolved_id)}


async def notify_transaction_changed(
    db,
    *,
    trans_id: int | None = None,
    spot_id: int | None = None,
    user_id: int | None = None,
) -> RowDict:
    """Call after TRANSACTION writes/status changes."""
    result: RowDict = {
        "spot_refreshed": False,
        "user_refreshed": False,
        "pending_transaction_refreshed": False,
    }
    if spot_id is not None:
        result["spot"] = await refresh_cached_spot(db, spot_id=int(spot_id))
        result["spot_refreshed"] = True
    else:
        mark_spot_cache_dirty()

    if user_id is not None:
        result["user"] = await refresh_cached_user(db, user_id=int(user_id))
        result["user_refreshed"] = True
    else:
        mark_user_cache_dirty()

    if trans_id is not None:
        result["pending_transaction"] = await refresh_cached_pending_transaction(db, trans_id=int(trans_id))
        result["pending_transaction_refreshed"] = True
    else:
        mark_pending_transaction_cache_dirty()

    return result


async def notify_transaction_created(
    db,
    *,
    trans_id: int,
    spot_id: int | None = None,
    user_id: int | None = None,
) -> RowDict:
    """Call after inserting a new pending TRANSACTION row."""
    return await notify_transaction_changed(
        db,
        trans_id=int(trans_id),
        spot_id=spot_id,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Background refresher
# ---------------------------------------------------------------------------

async def _cache_refresher_loop() -> None:
    global _REFRESH_LAST_ATTEMPT_AT, _REFRESH_LAST_SUCCESS_AT, _REFRESH_LAST_ERROR

    if _REFRESH_STOP_EVENT is None:
        return

    while not _REFRESH_STOP_EVENT.is_set():
        try:
            _REFRESH_LAST_ATTEMPT_AT = int(time.time())
            async with schema.get_db() as db:
                await refresh_all_caches(
                    db,
                    user_limit=_REFRESH_USER_LIMIT,
                    detail_limit_per_user=_REFRESH_USER_DETAIL_LIMIT,
                )
            _REFRESH_LAST_SUCCESS_AT = int(time.time())
            _REFRESH_LAST_ERROR = None
        except Exception as e:
            _REFRESH_LAST_ERROR = repr(e)

        try:
            await asyncio.wait_for(
                _REFRESH_STOP_EVENT.wait(),
                timeout=max(60, int(_REFRESH_INTERVAL_SECONDS)),
            )
        except asyncio.TimeoutError:
            pass


async def start_cache_refresher(
    *,
    full_refresh_seconds: int = DEFAULT_FULL_REFRESH_SECONDS,
    user_limit: int = DEFAULT_RECENT_USER_CACHE_SIZE,
    detail_limit_per_user: int = DEFAULT_USER_DETAIL_LIMIT_PER_USER,
    run_immediately: bool = True,
) -> None:
    """Start the background full-cache refresher.

    Use this in FastAPI startup. The maximum useful frequency here is not high;
    targeted refresh helpers should handle normal writes.
    """
    global _REFRESH_TASK, _REFRESH_STOP_EVENT
    global _REFRESH_INTERVAL_SECONDS, _REFRESH_USER_LIMIT, _REFRESH_USER_DETAIL_LIMIT
    global _REFRESH_LAST_ATTEMPT_AT, _REFRESH_LAST_SUCCESS_AT, _REFRESH_LAST_ERROR

    if _REFRESH_TASK is not None and not _REFRESH_TASK.done():
        return

    _REFRESH_INTERVAL_SECONDS = max(60, int(full_refresh_seconds))
    _REFRESH_USER_LIMIT = max(1, int(user_limit))
    _REFRESH_USER_DETAIL_LIMIT = max(1, int(detail_limit_per_user))
    _REFRESH_STOP_EVENT = asyncio.Event()

    if run_immediately:
        _REFRESH_LAST_ATTEMPT_AT = int(time.time())
        try:
            async with schema.get_db() as db:
                await refresh_all_caches(
                    db,
                    user_limit=_REFRESH_USER_LIMIT,
                    detail_limit_per_user=_REFRESH_USER_DETAIL_LIMIT,
                )
            _REFRESH_LAST_SUCCESS_AT = int(time.time())
            _REFRESH_LAST_ERROR = None
        except Exception as e:
            _REFRESH_LAST_ERROR = repr(e)
            raise

    _REFRESH_TASK = asyncio.create_task(_cache_refresher_loop())


async def stop_cache_refresher() -> None:
    """Stop the background full-cache refresher."""
    global _REFRESH_TASK, _REFRESH_STOP_EVENT

    if _REFRESH_STOP_EVENT is not None:
        _REFRESH_STOP_EVENT.set()

    if _REFRESH_TASK is not None:
        _REFRESH_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await _REFRESH_TASK

    _REFRESH_TASK = None
    _REFRESH_STOP_EVENT = None


# Backwards-compatible names from the earlier spot-only cache pass.
async def start_spot_cache_refresher(
    *,
    full_refresh_seconds: int = DEFAULT_FULL_REFRESH_SECONDS,
    run_immediately: bool = True,
) -> None:
    await start_cache_refresher(
        full_refresh_seconds=full_refresh_seconds,
        run_immediately=run_immediately,
    )


async def stop_spot_cache_refresher() -> None:
    await stop_cache_refresher()


# ---------------------------------------------------------------------------
# Status / diagnostics
# ---------------------------------------------------------------------------

async def get_spot_cache_status() -> RowDict:
    async with _CACHE_LOCK:
        snapshot = _SPOT_CACHE
        dirty = _SPOT_CACHE_DIRTY

    if snapshot is None:
        return {
            "loaded": False,
            "dirty": dirty,
            "loaded_at": None,
            "now_at_load": None,
            "next_transition_at": None,
            "spot_count": 0,
            "current_spot_count": 0,
            "upcoming_spot_count": 0,
        }

    return {
        "loaded": True,
        "dirty": dirty,
        "loaded_at": snapshot.loaded_at,
        "now_at_load": snapshot.now_at_load,
        "next_transition_at": snapshot.next_transition_at,
        "spot_count": len(snapshot.spots_by_id),
        "current_spot_count": len(snapshot.current_spot_ids_by_start),
        "upcoming_spot_count": len(snapshot.upcoming_spot_ids_by_start),
    }


async def get_user_cache_status() -> RowDict:
    async with _CACHE_LOCK:
        snapshot = _USER_CACHE
        dirty = _USER_CACHE_DIRTY

    if snapshot is None:
        return {
            "loaded": False,
            "dirty": dirty,
            "loaded_at": None,
            "user_limit": None,
            "detail_limit_per_user": None,
            "user_count": 0,
        }

    return {
        "loaded": True,
        "dirty": dirty,
        "loaded_at": snapshot.loaded_at,
        "user_limit": snapshot.user_limit,
        "detail_limit_per_user": snapshot.detail_limit_per_user,
        "user_count": len(snapshot.users_by_id),
    }


async def get_pending_transaction_cache_status() -> RowDict:
    async with _CACHE_LOCK:
        snapshot = _PENDING_TRANS_CACHE
        dirty = _PENDING_TRANS_CACHE_DIRTY

    if snapshot is None:
        return {
            "loaded": False,
            "dirty": dirty,
            "loaded_at": None,
            "pending_transaction_count": 0,
        }

    return {
        "loaded": True,
        "dirty": dirty,
        "loaded_at": snapshot.loaded_at,
        "pending_transaction_count": len(snapshot.transactions_by_id),
    }


async def get_cache_status() -> RowDict:
    return {
        "spot_cache": await get_spot_cache_status(),
        "user_cache": await get_user_cache_status(),
        "pending_transaction_cache": await get_pending_transaction_cache_status(),
        "refresher": {
            "running": _REFRESH_TASK is not None and not _REFRESH_TASK.done(),
            "interval_seconds": _REFRESH_INTERVAL_SECONDS,
            "last_attempt_at": _REFRESH_LAST_ATTEMPT_AT,
            "last_success_at": _REFRESH_LAST_SUCCESS_AT,
            "last_error": _REFRESH_LAST_ERROR,
        },
    }


# ---------------------------------------------------------------------------
# SPOT output shaping
# ---------------------------------------------------------------------------

def spot_record_to_public_dict(record: SpotCacheRecord, *, include_counts: bool = True) -> RowDict:
    """Return a public-safe spot dictionary.

    Suitable for Spot Search and public Spot pages. It includes creator display
    name and aggregate counts, but not raw claim rows or raw claim codes.
    """
    out: RowDict = {
        "spot": _copy_row(record.spot),
        "creator": _copy_row(record.creator),
        "prizedraw": _copy_row(record.prizedraw),
    }

    if include_counts:
        out["counts"] = {
            "claim_count": int(record.spot.get("claim_count", 0) or 0),
            "pending_claim_count": int(record.spot.get("pending_claim_count", 0) or 0),
            "success_claim_count": int(record.spot.get("success_claim_count", 0) or 0),
            "failed_claim_count": int(record.spot.get("failed_claim_count", 0) or 0),
            "claim_code_count": int(record.spot.get("claim_code_count", 0) or 0),
            "unused_code_count": int(record.spot.get("unused_code_count", 0) or 0),
            "used_code_count": int(record.spot.get("used_code_count", 0) or 0),
        }

    return out


def spot_record_to_owner_dict(record: SpotCacheRecord, *, owner_user_id: int) -> RowDict | None:
    """Return full published-spot detail, but only for the spot owner."""
    if int(record.spot[schema.SPOT_CREATED_BY]) != int(owner_user_id):
        return None

    return {
        "spot": _copy_row(record.spot),
        "creator": _copy_row(record.creator),
        "prizedraw": _copy_row(record.prizedraw),
        "owner_summary": _copy_row(record.owner_summary),
        "claims": _copy_rows(record.claims),
        "claim_codes": _copy_rows(record.claim_codes),
        "transactions": _copy_rows(record.transactions),
    }


def spot_record_to_internal_dict(record: SpotCacheRecord) -> RowDict:
    """Return the complete server-side cache record."""
    return {
        "spot": _copy_row(record.spot),
        "creator": _copy_row(record.creator),
        "prizedraw": _copy_row(record.prizedraw),
        "owner_summary": _copy_row(record.owner_summary),
        "claims": _copy_rows(record.claims),
        "claim_codes": _copy_rows(record.claim_codes),
        "transactions": _copy_rows(record.transactions),
    }


# ---------------------------------------------------------------------------
# USER output shaping
# ---------------------------------------------------------------------------

def user_record_to_self_dict(record: UserCacheRecord) -> RowDict:
    """Return safe authenticated-user data for Home/My History/My Spots."""
    return {
        "user": _strip_sensitive_user_fields(record.user),
        "dashboard_counts": _copy_row(record.dashboard_counts),
        "claims": _copy_rows(record.claims),
        "owner_spots": _copy_rows(record.owner_spots),
        "transactions": _copy_rows(record.transactions),
    }


def user_record_to_public_dict(record: UserCacheRecord) -> RowDict:
    """Return minimal public-ish user data."""
    user = record.user
    return {
        schema.USER_ID: int(user[schema.USER_ID]),
        schema.USER_DISPLAY_NAME: user.get(schema.USER_DISPLAY_NAME),
        schema.USER_STATUS: user.get(schema.USER_STATUS),
    }


# ---------------------------------------------------------------------------
# Pending TRANSACTION cache getters
# ---------------------------------------------------------------------------

async def get_pending_transactions(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return cached pending TRANSACTION rows, oldest first."""
    snapshot = await ensure_pending_transaction_cache(db)
    ids = _slice(snapshot.transaction_ids_by_created, limit=limit, offset=offset)
    return [_copy_row(snapshot.transactions_by_id[tid]) for tid in ids if tid in snapshot.transactions_by_id]  # type: ignore[list-item]


async def get_pending_transaction(db, *, trans_id: int) -> RowDict | None:
    """Return one cached pending TRANSACTION by id, or None."""
    snapshot = await ensure_pending_transaction_cache(db)
    return _copy_row(snapshot.transactions_by_id.get(int(trans_id)))


async def get_pending_transaction_by_hash(db, *, tx_hash: str) -> RowDict | None:
    """Return one cached pending TRANSACTION by blockchain hash, or None."""
    snapshot = await ensure_pending_transaction_cache(db)
    trans_id = snapshot.transaction_id_by_hash.get(str(tx_hash))
    if trans_id is None:
        return None
    return _copy_row(snapshot.transactions_by_id.get(int(trans_id)))


async def count_pending_transactions(db) -> int:
    snapshot = await ensure_pending_transaction_cache(db)
    return len(snapshot.transactions_by_id)


# ---------------------------------------------------------------------------
# Public SPOT cache getters
# ---------------------------------------------------------------------------

async def get_all_public_spots(db, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[RowDict]:
    snapshot = await ensure_spot_cache(db)
    ids = _slice(snapshot.spot_ids_by_start, limit=limit, offset=offset)
    return [spot_record_to_public_dict(snapshot.spots_by_id[sid]) for sid in ids]


async def get_current_spots(db, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[RowDict]:
    snapshot = await ensure_spot_cache(db)
    ids = _slice(snapshot.current_spot_ids_by_start, limit=limit, offset=offset)
    return [spot_record_to_public_dict(snapshot.spots_by_id[sid]) for sid in ids]


async def get_upcoming_spots(db, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[RowDict]:
    snapshot = await ensure_spot_cache(db)
    ids = _slice(snapshot.upcoming_spot_ids_by_start, limit=limit, offset=offset)
    return [spot_record_to_public_dict(snapshot.spots_by_id[sid]) for sid in ids]


async def get_public_spot(db, *, spot_id: int) -> RowDict | None:
    snapshot = await ensure_spot_cache(db)
    record = snapshot.spots_by_id.get(int(spot_id))
    if record is None:
        return None
    return spot_record_to_public_dict(record)


async def get_owner_spot(db, *, owner_user_id: int, spot_id: int) -> RowDict | None:
    snapshot = await ensure_spot_cache(db)
    record = snapshot.spots_by_id.get(int(spot_id))
    if record is None:
        return None
    return spot_record_to_owner_dict(record, owner_user_id=int(owner_user_id))


async def get_internal_spot_record(db, *, spot_id: int) -> RowDict | None:
    snapshot = await ensure_spot_cache(db)
    record = snapshot.spots_by_id.get(int(spot_id))
    if record is None:
        return None
    return spot_record_to_internal_dict(record)


async def get_spots_by_geohash_prefix(
    db,
    *,
    geohash_prefix: str,
    current_only: bool = False,
    upcoming_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return cached spots whose geohash begins with a prefix."""
    snapshot = await ensure_spot_cache(db)
    prefix = str(geohash_prefix).strip().lower()

    if current_only and upcoming_only:
        raise ValueError("current_only and upcoming_only cannot both be true")

    source_ids = snapshot.spot_ids_by_start
    if current_only:
        source_ids = snapshot.current_spot_ids_by_start
    elif upcoming_only:
        source_ids = snapshot.upcoming_spot_ids_by_start

    matching: list[int] = []
    for sid in source_ids:
        spot = snapshot.spots_by_id[sid].spot
        geohash = spot.get(schema.SPOT_GEOHASH)
        if geohash is not None and str(geohash).lower().startswith(prefix):
            matching.append(sid)

    ids = _slice(matching, limit=limit, offset=offset)
    return [spot_record_to_public_dict(snapshot.spots_by_id[sid]) for sid in ids]


async def get_spots_in_bounds(
    db,
    *,
    min_lat: float,
    min_long: float,
    max_lat: float,
    max_long: float,
    current_only: bool = False,
    upcoming_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return cached spots inside a map bounding box."""
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if min_long > max_long:
        min_long, max_long = max_long, min_long

    if current_only and upcoming_only:
        raise ValueError("current_only and upcoming_only cannot both be true")

    snapshot = await ensure_spot_cache(db)
    source_ids = snapshot.spot_ids_by_start
    if current_only:
        source_ids = snapshot.current_spot_ids_by_start
    elif upcoming_only:
        source_ids = snapshot.upcoming_spot_ids_by_start

    matching = [
        sid
        for sid in source_ids
        if _is_in_bounds(
            snapshot.spots_by_id[sid].spot,
            min_lat=float(min_lat),
            min_long=float(min_long),
            max_lat=float(max_lat),
            max_long=float(max_long),
        )
    ]

    ids = _slice(matching, limit=limit, offset=offset)
    return [spot_record_to_public_dict(snapshot.spots_by_id[sid]) for sid in ids]


async def get_my_current_public_spots(
    db,
    *,
    owner_user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return cached published/non-expired spots owned by a USER."""
    snapshot = await ensure_spot_cache(db)
    matching = [
        sid
        for sid in snapshot.spot_ids_by_start
        if int(snapshot.spots_by_id[sid].spot[schema.SPOT_CREATED_BY]) == int(owner_user_id)
    ]
    ids = _slice(matching, limit=limit, offset=offset)

    out: list[RowDict] = []
    for sid in ids:
        owner_row = spot_record_to_owner_dict(snapshot.spots_by_id[sid], owner_user_id=int(owner_user_id))
        if owner_row is not None:
            out.append(owner_row)
    return out


# ---------------------------------------------------------------------------
# Recent USER cache getters
# ---------------------------------------------------------------------------

async def get_cached_user(db, *, user_id: int) -> RowDict | None:
    snapshot = await ensure_user_cache(db)
    record = snapshot.users_by_id.get(int(user_id))
    if record is None:
        return None
    return user_record_to_self_dict(record)


async def get_cached_user_home(db, *, user_id: int) -> RowDict | None:
    """Return the small data bundle needed by Home."""
    snapshot = await ensure_user_cache(db)
    record = snapshot.users_by_id.get(int(user_id))
    if record is None:
        return None
    return {
        "user": _strip_sensitive_user_fields(record.user),
        "dashboard_counts": _copy_row(record.dashboard_counts),
    }


async def get_cached_user_claims(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict] | None:
    """Return My History rows for a recent user, or None if not cached."""
    snapshot = await ensure_user_cache(db)
    record = snapshot.users_by_id.get(int(user_id))
    if record is None:
        return None
    rows = _copy_rows(record.claims)
    return rows[_normalise_offset(offset) : _normalise_offset(offset) + _clamp_limit(limit)]


async def get_cached_user_spots(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict] | None:
    """Return My Spots rows for a recent user, including draft/completed/etc."""
    snapshot = await ensure_user_cache(db)
    record = snapshot.users_by_id.get(int(user_id))
    if record is None:
        return None
    rows = _copy_rows(record.owner_spots)
    return rows[_normalise_offset(offset) : _normalise_offset(offset) + _clamp_limit(limit)]


async def get_cached_user_transactions(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict] | None:
    snapshot = await ensure_user_cache(db)
    record = snapshot.users_by_id.get(int(user_id))
    if record is None:
        return None
    rows = _copy_rows(record.transactions)
    return rows[_normalise_offset(offset) : _normalise_offset(offset) + _clamp_limit(limit)]


async def get_recent_cached_users(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return minimal info for recently seen cached users."""
    snapshot = await ensure_user_cache(db)
    ids = _slice(snapshot.user_ids_by_last_seen, limit=limit, offset=offset)
    return [user_record_to_public_dict(snapshot.users_by_id[uid]) for uid in ids]


# ---------------------------------------------------------------------------
# Lightweight metrics from cache
# ---------------------------------------------------------------------------

async def get_cached_spot_counts(db) -> RowDict:
    snapshot = await ensure_spot_cache(db)
    return {
        "spot_count": len(snapshot.spots_by_id),
        "current_spot_count": len(snapshot.current_spot_ids_by_start),
        "upcoming_spot_count": len(snapshot.upcoming_spot_ids_by_start),
    }


async def get_cached_spot_counts_by_country(db) -> dict[str, int]:
    snapshot = await ensure_spot_cache(db)
    counts: dict[str, int] = {}
    for sid in snapshot.spot_ids_by_start:
        country = snapshot.spots_by_id[sid].spot.get(schema.SPOT_COUNTRY) or "Unknown"
        counts[str(country)] = counts.get(str(country), 0) + 1
    return counts


async def get_cached_spot_counts_by_city(db) -> dict[str, int]:
    snapshot = await ensure_spot_cache(db)
    counts: dict[str, int] = {}
    for sid in snapshot.spot_ids_by_start:
        city = snapshot.spots_by_id[sid].spot.get(schema.SPOT_CITY) or "Unknown"
        counts[str(city)] = counts.get(str(city), 0) + 1
    return counts


async def get_cached_owner_spot_count(db, *, owner_user_id: int) -> int:
    snapshot = await ensure_spot_cache(db)
    return sum(
        1
        for sid in snapshot.spot_ids_by_start
        if int(snapshot.spots_by_id[sid].spot[schema.SPOT_CREATED_BY]) == int(owner_user_id)
    )


async def get_cached_user_counts(db) -> RowDict:
    snapshot = await ensure_user_cache(db)
    active = limited = banned = 0
    for uid in snapshot.user_ids_by_last_seen:
        status = int(snapshot.users_by_id[uid].user[schema.USER_STATUS])
        if status == const.USER_STATUS_ACTIVE:
            active += 1
        elif status == const.USER_STATUS_LIMITED:
            limited += 1
        elif status == const.USER_STATUS_BANNED:
            banned += 1
    return {
        "cached_user_count": len(snapshot.users_by_id),
        "active_user_count": active,
        "limited_user_count": limited,
        "banned_user_count": banned,
    }


async def get_cached_daily_user_count(db, *, window_seconds: int = 24 * 60 * 60) -> int:
    """Return cached users seen within the last `window_seconds`.

    The cache deliberately contains recent users, so this is cheap and good
    enough for small public homepage metrics. SQLite remains the source of
    truth if a route wants to fall back to a direct count.
    """
    snapshot = await ensure_user_cache(db)
    now = await _get_unixepoch(db)
    threshold = int(now) - max(1, int(window_seconds))

    return sum(
        1
        for uid in snapshot.user_ids_by_last_seen
        if int(snapshot.users_by_id[uid].user.get(schema.USER_LAST_SEEN_AT) or 0) >= threshold
    )


async def get_cached_home_metrics(db) -> RowDict:
    """Return public Home-page counters from the cache layer."""
    spot_counts = await get_cached_spot_counts(db)
    daily_users = await get_cached_daily_user_count(db)
    return {
        "active_spot_count": int(spot_counts.get("current_spot_count", 0) or 0),
        "daily_user_count": int(daily_users or 0),
    }


# ---------------------------------------------------------------------------
# Owner-only claim-code helpers from cache
# ---------------------------------------------------------------------------

async def get_cached_available_claim_codes_for_owner(
    db,
    *,
    owner_user_id: int,
    spot_id: int,
) -> list[RowDict] | None:
    """Return unused claim codes for the spot owner, or None if not owner/found."""
    snapshot = await ensure_spot_cache(db)
    record = snapshot.spots_by_id.get(int(spot_id))
    if record is None:
        return None
    if int(record.spot[schema.SPOT_CREATED_BY]) != int(owner_user_id):
        return None

    return [
        copy.deepcopy(row)
        for row in record.claim_codes
        if row.get(schema.CLAIM_CODE_USED_BY) is None
    ]


async def get_cached_claims_for_owner(
    db,
    *,
    owner_user_id: int,
    spot_id: int,
) -> list[RowDict] | None:
    """Return cached claims for the spot owner, or None if not owner/found."""
    snapshot = await ensure_spot_cache(db)
    record = snapshot.spots_by_id.get(int(spot_id))
    if record is None:
        return None
    if int(record.spot[schema.SPOT_CREATED_BY]) != int(owner_user_id):
        return None

    return _copy_rows(record.claims)
