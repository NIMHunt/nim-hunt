"""
db_access.py

Unified async database access helpers for NimHunt.

This file combines the old roles of db_get, db_write, db_tx, and db_check:

- db_tx:    transaction(db)
- db_get:   narrow row/list/count getters
- db_write: narrow insert/update helpers
- db_check: rule/outcome helpers built from cheap database lookups

Important style note
--------------------
Every function accepts an existing async SQLite connection as its first argument.
That keeps these helpers easy to compose inside one transaction:

    async with get_db() as db:
        async with transaction(db):
            user_id = await create_user(db, device_id_hash=device_hash)
            spot_id = await create_spot(db, created_by=user_id, ...)

The functions do not open their own database connection.
For multi-step writes, wrap the call site in transaction(db).
"""

from __future__ import annotations

import math
import secrets
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import constants as const
import database as schema
import wallet

# ---------------------------------------------------------------------------
# Transaction helper
# ---------------------------------------------------------------------------

@asynccontextmanager
async def transaction(db, *, immediate: bool = False) -> AsyncIterator[None]:
    """Small transaction wrapper for an existing aiosqlite connection.

    ``immediate=True`` acquires SQLite's write reservation before any financial
    eligibility checks. Use it when a competing workflow must not slip between
    a balance/state check and creation of a durable transaction intent.

    SQLite does not support arbitrary nested BEGIN blocks. If a caller needs
    nested transaction-like behaviour later, use SQLite SAVEPOINTs deliberately;
    do not silently nest this wrapper.
    """
    try:
        await db.execute("BEGIN IMMEDIATE;" if immediate else "BEGIN;")
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            "Nested transaction detected. Open one transaction at the top level."
        ) from e

    try:
        yield
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        finally:
            raise


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
GEOHASH_DEFAULT_PRECISION = 8
_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


RowDict = dict[str, Any]
PlaceResolver = Callable[[float, float], Awaitable[tuple[str | None, str | None]]]


def _clamp_limit(limit: int, *, max_limit: int = MAX_LIMIT) -> int:
    return max(1, min(int(limit), int(max_limit)))


def _normalise_offset(offset: int) -> int:
    return max(0, int(offset))


def _row_to_dict(row: Any | None) -> RowDict | None:
    return dict(row) if row is not None else None


def _rows_to_dicts(rows: list[Any]) -> list[RowDict]:
    return [dict(row) for row in rows]


async def get_unixepoch(db) -> int:
    """Return SQLite's current unix timestamp.

    Using SQLite's unixepoch() keeps timestamps consistent with database
    defaults and triggers.
    """
    cur = await db.execute("SELECT unixepoch() AS now;")
    row = await cur.fetchone()
    return int(row["now"])


def _require_one(rowcount: int, message: str) -> None:
    if int(rowcount) != 1:
        raise RuntimeError(message)


def _sql_placeholders(count: int) -> str:
    if count <= 0:
        raise ValueError("count must be positive")
    return ", ".join("?" for _ in range(count))


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


_CLAIM_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_CLAIM_CODE_LENGTH = 10


def _normalise_claim_code(value: str | None, *, required: bool = False) -> str | None:
    """Return an uppercase ASCII claim code or None.

    New claim codes deliberately avoid punctuation so they are easy to type on
    a phone. Lowercase user input is accepted and normalised for convenience.
    """
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        if required:
            raise ValueError("A claim code is required for this spot.")
        return None

    cleaned = cleaned.upper()
    if not cleaned.isascii() or not cleaned.isalnum():
        raise ValueError("Claim codes contain uppercase letters and numbers only.")
    return cleaned


def _clean_optional_nimiq_address(value: str | None, *, required: bool = False) -> str | None:
    """Return a checksum-valid, normalised Nimiq address or None.

    wallet.py owns the actual address rules. Keeping this wrapper here means
    every CLAIM payout-address path gets the same validation without duplicating
    Nimiq checksum logic across database helpers.
    """
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        if required:
            raise ValueError("payout_address is required")
        return None

    max_chars = int(getattr(const, "CLAIM_PAYOUT_ADDRESS_MAX_CHARS", 160))
    if len(cleaned) > max_chars:
        raise ValueError(f"payout_address must be no more than {max_chars} characters")
    return wallet.normalise_nimiq_address(cleaned, field_name="payout_address")


_UNSET = object()


def _validate_spot_title(title: str) -> str:
    """Return a safe SPOT title or raise ValueError."""
    title = str(title or "").strip()
    min_chars = int(getattr(const, "SPOT_TITLE_MIN_CHARS", 3))
    max_chars = int(getattr(const, "SPOT_TITLE_MAX_CHARS", 80))
    if not (min_chars <= len(title) <= max_chars):
        raise ValueError(f"title must be between {min_chars} and {max_chars} characters")
    return title


def _validate_optional_coordinates(lat: float | None, long: float | None) -> tuple[float | None, float | None]:
    """Return valid optional coordinates. Both values must be supplied together."""
    if lat is None and long is None:
        return None, None
    if lat is None or long is None:
        raise ValueError("lat and long must be supplied together")

    lat_f = float(lat)
    long_f = float(long)
    if not (-90 <= lat_f <= 90):
        raise ValueError("lat must be between -90 and 90")
    if not (-180 <= long_f <= 180):
        raise ValueError("long must be between -180 and 180")
    return lat_f, long_f


def _validate_int_range(value: int, *, field_name: str, min_value: int, max_value: int | None = None) -> int:
    value = int(value)
    if value < int(min_value):
        raise ValueError(f"{field_name} must be at least {int(min_value)}")
    if max_value is not None and value > int(max_value):
        raise ValueError(f"{field_name} must be no more than {int(max_value)}")
    return value


def _validate_non_negative_int(value: int, *, field_name: str) -> int:
    return _validate_int_range(value, field_name=field_name, min_value=0)


def _validate_spot_claim_duration(claim_duration: int) -> int:
    return _validate_int_range(
        claim_duration,
        field_name="claim_duration",
        min_value=const.MIN_SPOT_CLAIM_DURATION_SECONDS,
        max_value=const.MAX_SPOT_CLAIM_DURATION_SECONDS,
    )


def _validate_spot_max_claims_per_user(max_claims_per_user: int) -> int:
    return _validate_int_range(
        max_claims_per_user,
        field_name="max_claims_per_user",
        min_value=const.MIN_SPOT_MAX_CLAIMS_PER_USER,
        max_value=const.MAX_SPOT_MAX_CLAIMS_PER_USER,
    )


def _validate_positive_optional_timestamp(value: int | None, *, field_name: str) -> int | None:
    if value is None:
        return None
    value = int(value)
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive Unix timestamp")
    return value


def _validate_spot_ends_after_seconds(value: int | None) -> int:
    """Return a valid SPOT end duration in seconds.

    SPOT.ends_at deliberately stores seconds after SPOT.starts_at, not an
    absolute unix timestamp.
    """
    if value is None:
        value = const.DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS
    return _validate_int_range(
        value,
        field_name="ends_at",
        min_value=const.MIN_SPOT_ENDS_AFTER_SECONDS,
        max_value=const.MAX_SPOT_ENDS_AFTER_SECONDS,
    )


def _validate_spot_time_window(starts_at: int | None, ends_at: int | None) -> tuple[int | None, int]:
    starts_at = _validate_positive_optional_timestamp(starts_at, field_name="starts_at")
    ends_at = _validate_spot_ends_after_seconds(ends_at)
    return starts_at, ends_at


def _validate_spot_radius(radius: int) -> int:
    """Return a safe SPOT radius in metres or raise ValueError.

    Keeping this as a helper gives every SPOT creation path the same bounds.
    It also keeps the error message close to the rule, which makes frontend/API
    validation easier to match later.
    """
    radius = int(radius)
    if not (const.MIN_SPOT_RADIUS_METRES <= radius <= const.MAX_SPOT_RADIUS_METRES):
        raise ValueError(
            "radius must be between "
            f"{const.MIN_SPOT_RADIUS_METRES} and "
            f"{const.MAX_SPOT_RADIUS_METRES} metres"
        )
    return radius


def _validate_spot_max_total_claims(max_total_claims: int, *, is_prizedraw: bool = False) -> int:
    """Return a safe total participant cap or raise ValueError.

    0 means unlimited total participants, but that only makes sense for
    Prizedraw spots. Standard spots must keep a finite total claim cap.
    """
    max_total_claims = _validate_int_range(
        max_total_claims,
        field_name="max_total_claims",
        min_value=const.MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS,
        max_value=const.MAX_SPOT_MAX_TOTAL_CLAIMS,
    )
    if max_total_claims == 0 and not is_prizedraw:
        raise ValueError("max_total_claims can only be 0/unlimited for Prizedraw spots")
    if max_total_claims < const.MIN_SPOT_MAX_TOTAL_CLAIMS and not is_prizedraw:
        raise ValueError(f"max_total_claims must be at least {const.MIN_SPOT_MAX_TOTAL_CLAIMS}")
    if (
        is_prizedraw
        and max_total_claims > 0
        and max_total_claims < const.MIN_FINITE_PRIZEDRAW_TOTAL_PARTICIPANTS
    ):
        raise ValueError(
            "finite Prizedraw max_total_claims must be at least "
            f"{const.MIN_FINITE_PRIZEDRAW_TOTAL_PARTICIPANTS}"
        )
    return max_total_claims


def _validate_prizedraw_prize_count(prize_count: int) -> int:
    prize_count = _validate_int_range(
        prize_count,
        field_name="prize_count",
        min_value=const.MIN_PRIZEDRAW_PRIZE_COUNT,
        max_value=const.MAX_PRIZEDRAW_PRIZE_COUNT,
    )
    options = tuple(getattr(const, "PRIZEDRAW_PRIZE_COUNT_OPTIONS", ()))
    if options and prize_count not in options:
        raise ValueError("prize_count must be one of the allowed Prizedraw prize-count values")
    return prize_count


def _validate_prizedraw_participant_limits(
    *,
    max_claims_per_user: int,
    max_total_claims: int,
    prize_count: int,
) -> None:
    """Validate relationships between finite Prizedraw participation limits."""
    if max_total_claims == 0:
        return
    if max_total_claims < const.MIN_FINITE_PRIZEDRAW_TOTAL_PARTICIPANTS:
        raise ValueError(
            "finite Prizedraw max_total_claims must be at least "
            f"{const.MIN_FINITE_PRIZEDRAW_TOTAL_PARTICIPANTS}"
        )
    if max_claims_per_user > 0 and max_claims_per_user >= max_total_claims:
        raise ValueError(
            "max_claims_per_user must be less than max_total_claims for a finite Prizedraw"
        )
    if prize_count >= max_total_claims:
        raise ValueError(
            "prize_count must be less than max_total_claims for a finite Prizedraw"
        )


def _format_nim_plain(luna: int) -> str:
    """Return a compact NIM amount for validation messages."""
    nim = int(luna) / int(const.LUNA_PER_NIM)
    if nim.is_integer():
        return f"{int(nim)} NIM"
    return f"{nim:g} NIM"


def _minimum_payout_luna(*, is_prizedraw: bool) -> int:
    if is_prizedraw:
        return int(getattr(const, "MIN_PRIZEDRAW_PRIZE_PAYOUT", 1000 * const.LUNA_PER_NIM))
    return int(getattr(const, "MIN_STANDARD_CLAIM_PAYOUT", 100 * const.LUNA_PER_NIM))


def _minimum_payout_nim(*, is_prizedraw: bool) -> int:
    if is_prizedraw:
        return int(getattr(const, "MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM", 1000))
    return int(getattr(const, "MIN_STANDARD_CLAIM_PAYOUT_NIM", 100))


def configured_spot_creation_fee(*, is_prizedraw: bool) -> int:
    """Return the creation fee configured for a newly-created Spot."""
    if is_prizedraw:
        return max(0, int(getattr(const, "PRIZEDRAW_SPOT_CREATION_FEE", 0)))
    return max(0, int(getattr(const, "STANDARD_SPOT_CREATION_FEE", 0)))


def spot_creation_fee_amount(spot: RowDict) -> int:
    """Return the immutable creation fee snapshotted onto one Spot."""
    return max(0, int(spot.get(schema.SPOT_CREATION_FEE) or 0))


def spot_required_deposit_amount(spot: RowDict) -> int:
    """Return reward-pool funding plus the one-time creation fee."""
    return max(0, int(spot.get(schema.SPOT_TOTAL_VALUE) or 0)) + spot_creation_fee_amount(spot)


async def spot_minimum_payout_summary(db, *, spot_id: int) -> RowDict:
    """Return the effective payout floor for a SPOT.

    Standard spots must pay at least MIN_STANDARD_CLAIM_PAYOUT per possible
    claim. Prizedraw spots must pay at least MIN_PRIZEDRAW_PRIZE_PAYOUT per
    prize. This is intentionally checked at draft-save time and publish time,
    not at title-only draft creation, because a new Prizedraw draft may begin
    with placeholder values before the full form is completed.
    """
    spot = await get_spot(db, spot_id=spot_id)
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    spot_is_prizedraw = await is_prizedraw(db, spot_id=spot_id)
    total_value = int(spot.get(schema.SPOT_TOTAL_VALUE) or 0)

    if spot_is_prizedraw:
        prizedraw = await get_prizedraw(db, spot_id=spot_id)
        divisor = int(prizedraw.get(schema.PRIZEDRAW_PRIZE_COUNT) if prizedraw else 0)
        kind = "prize"
        code = "minimum_prize_payout_too_low"
    else:
        divisor = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
        kind = "claim"
        code = "minimum_claim_payout_too_low"

    minimum = _minimum_payout_luna(is_prizedraw=spot_is_prizedraw)
    required_total = minimum * max(1, divisor)
    payout = total_value / max(1, divisor) if divisor > 0 else 0
    ok = divisor > 0 and total_value >= required_total

    return {
        "ok": ok,
        "code": None if ok else code,
        "is_prizedraw": spot_is_prizedraw,
        "kind": kind,
        "divisor": divisor,
        "total_value": total_value,
        "payout": payout,
        "minimum": minimum,
        "minimum_nim": _minimum_payout_nim(is_prizedraw=spot_is_prizedraw),
        "required_total": required_total,
    }


async def spot_meets_minimum_payout(db, *, spot_id: int) -> bool:
    return bool((await spot_minimum_payout_summary(db, spot_id=spot_id))["ok"])


async def require_spot_minimum_payout(db, *, spot_id: int) -> None:
    summary = await spot_minimum_payout_summary(db, spot_id=spot_id)
    if summary["ok"]:
        return

    kind = summary["kind"]
    minimum = _format_nim_plain(int(summary["minimum"]))
    raise ValueError(f"Minimum payout is {minimum} per {kind}.")


def _validate_bool_flag(value: bool | int, *, field_name: str) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be 0 or 1") from exc
    if int_value not in (0, 1):
        raise ValueError(f"{field_name} must be 0 or 1")
    return int_value


def encode_geohash(lat: float, lon: float, precision: int = GEOHASH_DEFAULT_PRECISION) -> str:
    """Encode latitude/longitude as a geohash without an external dependency.

    Geohashes are useful for map searches because nearby points often share a
    prefix. Shorter prefixes cover wider areas; longer prefixes cover tighter
    areas.
    """
    lat = float(lat)
    lon = float(lon)
    precision = int(precision)

    if not (-90 <= lat <= 90):
        raise ValueError("lat must be between -90 and 90")
    if not (-180 <= lon <= 180):
        raise ValueError("long must be between -180 and 180")
    if precision <= 0:
        raise ValueError("precision must be positive")

    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    geohash: list[str] = []
    bit = 0
    ch = 0
    even_bit = True
    bits = [16, 8, 4, 2, 1]

    while len(geohash) < precision:
        if even_bit:
            mid = (lon_interval[0] + lon_interval[1]) / 2
            if lon >= mid:
                ch |= bits[bit]
                lon_interval[0] = mid
            else:
                lon_interval[1] = mid
        else:
            mid = (lat_interval[0] + lat_interval[1]) / 2
            if lat >= mid:
                ch |= bits[bit]
                lat_interval[0] = mid
            else:
                lat_interval[1] = mid

        even_bit = not even_bit
        if bit < 4:
            bit += 1
        else:
            geohash.append(_GEOHASH_ALPHABET[ch])
            bit = 0
            ch = 0

    return "".join(geohash)


async def _derive_place_labels(
    lat: float,
    long: float,
    *,
    city: str | None = None,
    country: str | None = None,
    place_resolver: PlaceResolver | None = None,
) -> tuple[str | None, str | None]:
    """Return city/country labels for a spot.

    The database itself cannot reverse-geocode coordinates. This helper accepts
    already-known labels or an app-provided async resolver. If neither is
    supplied, the labels are stored as NULL and geohash remains the efficient
    search key.
    """
    city = _clean_optional_text(city)
    country = _clean_optional_text(country)
    if (city is not None and country is not None) or place_resolver is None:
        return city, country

    resolved_city, resolved_country = await place_resolver(float(lat), float(long))
    return city or _clean_optional_text(resolved_city), country or _clean_optional_text(resolved_country)


async def _generate_unique_spot_link(db, *, byte_count: int = 8) -> str:
    """Generate a URL-safe unique link/slug for a SPOT."""
    for _ in range(20):
        link = secrets.token_urlsafe(byte_count).rstrip("=")
        existing = await get_spot_by_link(db, link=link)
        if existing is None:
            return link
    raise RuntimeError("Failed to generate a unique spot link")


async def _next_spot_deposit_key_index(db) -> int:
    """Return the next unused SPOT deposit-key index.

    Call this inside the same transaction that inserts the SPOT. SQLite has a
    single writer, so MAX()+1 is sufficient for this app once wrapped in the
    existing transaction() helper.
    """
    cur = await db.execute(
        f"""
        SELECT COALESCE(MAX({schema.SPOT_DEPOSIT_KEY_INDEX}), -1) + 1 AS next_index
        FROM {schema.SPOT_TABLE_NAME};
        """
    )
    row = await cur.fetchone()
    return int(row["next_index"] or 0)


async def _generate_unique_spot_deposit_record(db):
    """Derive a unique SPOT deposit address and its immutable key metadata."""
    key_index = await _next_spot_deposit_key_index(db)

    for offset in range(30):
        record = wallet.derive_spot_deposit_address(key_index + offset)
        cur = await db.execute(
            f"""
            SELECT 1
            FROM {schema.SPOT_TABLE_NAME}
            WHERE {schema.SPOT_DEPOSIT_ADDRESS} = ?
               OR {schema.SPOT_DEPOSIT_KEY_INDEX} = ?;
            """,
            (record.address, record.key_index),
        )
        if await cur.fetchone() is None:
            return record

    raise RuntimeError("Failed to generate a unique spot deposit address")


def distance_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return Haversine distance between two coordinates in metres."""
    radius_m = 6_371_000.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lam = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _location_accuracy_margin_metres(reading_accuracy_metres: float | None) -> float:
    """Return the GPS mercy margin, capped by constants.py."""
    if reading_accuracy_metres is None:
        return 0.0
    try:
        reading = max(0.0, float(reading_accuracy_metres))
    except (TypeError, ValueError):
        return 0.0
    cap = max(0.0, float(getattr(const, "CLAIM_LOCATION_MAX_ACCURACY_MARGIN_METRES", 50)))
    return min(reading, cap)


def _effective_distance_metres(*, distance_metres_value: float, reading_accuracy_metres: float | None) -> float:
    """Distance after giving the GPS reading a capped benefit of the doubt."""
    return max(0.0, float(distance_metres_value) - _location_accuracy_margin_metres(reading_accuracy_metres))


def _duration_claim_penalty(*, effective_distance: float, radius_metres: int) -> float:
    """Return how much CLAIM.accuracy should be reduced for one heartbeat."""
    outside_by = max(0.0, float(effective_distance) - float(radius_metres))
    if outside_by <= 0:
        return 0.0

    soft_margin = max(0.0, float(getattr(const, "CLAIM_LOCATION_SOFT_OUTSIDE_MARGIN_METRES", 25)))
    if outside_by <= soft_margin:
        return max(0.0, float(getattr(const, "CLAIM_LOCATION_SOFT_PENALTY", 0.10)))

    return max(0.0, float(getattr(const, "CLAIM_LOCATION_HARD_PENALTY", 0.35)))


# ---------------------------------------------------------------------------
# USER: writes
# ---------------------------------------------------------------------------

async def create_user(db, *, device_id_hash: str) -> int:
    """Create a USER from a hashed device id and return the new user id.

    display_name is generated as user + the first 8 hash characters, e.g.
    user2F8BAA12. created_at and last_seen_at are filled by SQLite defaults.
    """
    device_id_hash = str(device_id_hash).strip()
    if not device_id_hash:
        raise ValueError("device_id_hash must be non-empty")

    display_name = f"user{device_id_hash[:8].upper()}"
    cur = await db.execute(
        f"""
        INSERT INTO {schema.USER_TABLE_NAME} (
            {schema.USER_DEVICE_ID_HASH},
            {schema.USER_DISPLAY_NAME},
            {schema.USER_STATUS}
        )
        VALUES (?, ?, ?);
        """,
        (device_id_hash, display_name, const.USER_STATUS_ACTIVE),
    )
    return int(cur.lastrowid)


async def get_or_create_user(db, *, device_id_hash: str) -> tuple[int, bool]:
    """Return (user_id, created).

    Useful on app startup when the same device may already have a row.
    """
    existing = await get_user(db, device_id_hash=device_id_hash)
    if existing is not None:
        return int(existing[schema.USER_ID]), False
    return await create_user(db, device_id_hash=device_id_hash), True


async def modify_user_display_name(db, *, user_id: int, display_name: str) -> None:
    display_name = str(display_name).strip()
    if not display_name:
        raise ValueError("display_name must be non-empty")

    cur = await db.execute(
        f"""
        UPDATE {schema.USER_TABLE_NAME}
        SET {schema.USER_DISPLAY_NAME} = ?
        WHERE {schema.USER_ID} = ?;
        """,
        (display_name, int(user_id)),
    )
    _require_one(cur.rowcount, f"Failed to update user display name id={user_id}")


async def modify_user_status(db, *, user_id: int, status: int) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.USER_TABLE_NAME}
        SET {schema.USER_STATUS} = ?
        WHERE {schema.USER_ID} = ?;
        """,
        (int(status), int(user_id)),
    )
    _require_one(cur.rowcount, f"Failed to update user status id={user_id}")


async def set_user_status_to_active(db, *, user_id: int) -> None:
    await modify_user_status(db, user_id=user_id, status=const.USER_STATUS_ACTIVE)


async def set_user_status_to_limited(db, *, user_id: int) -> None:
    await modify_user_status(db, user_id=user_id, status=const.USER_STATUS_LIMITED)


async def set_user_status_to_banned(db, *, user_id: int) -> None:
    await modify_user_status(db, user_id=user_id, status=const.USER_STATUS_BANNED)


async def touch_user_last_seen(db, *, user_id: int) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.USER_TABLE_NAME}
        SET {schema.USER_LAST_SEEN_AT} = unixepoch()
        WHERE {schema.USER_ID} = ?;
        """,
        (int(user_id),),
    )
    _require_one(cur.rowcount, f"Failed to update user last_seen id={user_id}")


# ---------------------------------------------------------------------------
# USER: getters / checks / metrics
# ---------------------------------------------------------------------------

async def get_user(db, *, device_id_hash: str) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.USER_TABLE_NAME}
        WHERE {schema.USER_DEVICE_ID_HASH} = ?;
        """,
        (str(device_id_hash).strip(),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_user_by_id(db, *, user_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.USER_TABLE_NAME}
        WHERE {schema.USER_ID} = ?;
        """,
        (int(user_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_users_by_status(
    db,
    *,
    status: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.USER_TABLE_NAME}
        WHERE {schema.USER_STATUS} = ?
        ORDER BY {schema.USER_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (int(status), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_inactive_users(
    db,
    *,
    last_seen_before: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.USER_TABLE_NAME}
        WHERE {schema.USER_LAST_SEEN_AT} < ?
        ORDER BY {schema.USER_LAST_SEEN_AT} ASC, {schema.USER_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (int(last_seen_before), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def count_users(db) -> int:
    cur = await db.execute(f"SELECT COUNT(*) AS n FROM {schema.USER_TABLE_NAME};")
    row = await cur.fetchone()
    return int(row["n"])


async def count_users_by_status(db, *, status: int) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.USER_TABLE_NAME}
        WHERE {schema.USER_STATUS} = ?;
        """,
        (int(status),),
    )
    row = await cur.fetchone()
    return int(row["n"])


async def can_user_create_spot(db, *, user_id: int) -> bool:
    user = await get_user_by_id(db, user_id=user_id)
    return bool(user and int(user[schema.USER_STATUS]) == const.USER_STATUS_ACTIVE)


async def can_user_claim(db, *, user_id: int) -> bool:
    user = await get_user_by_id(db, user_id=user_id)
    if not user:
        return False
    return int(user[schema.USER_STATUS]) in (
        const.USER_STATUS_ACTIVE,
        const.USER_STATUS_LIMITED,
    )


# ---------------------------------------------------------------------------
# SPOT: writes
# ---------------------------------------------------------------------------

async def _require_draft_spot(db, *, spot_id: int) -> RowDict:
    """Return a SPOT row or raise unless it is still editable as DRAFT."""
    spot = await get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("spot fields can only be edited while the spot is a draft")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("spot fields cannot be edited after cancellation has started")
    return spot


async def create_spot(
    db,
    *,
    created_by: int,
    title: str,
    desc: str | None = None,
    lat: float | None = None,
    long: float | None = None,
    radius: int | None = None,
    claim_duration: int | None = None,
    max_claims_per_user: int | None = None,
    max_total_claims: int | None = None,
    total_value: int | None = None,
    starts_at: int | None = None,
    ends_at: int | None = None,
    use_password: bool | int | None = None,
    is_prizedraw: bool = False,
    link: str | None = None,
    city: str | None = None,
    country: str | None = None,
    geohash_precision: int = GEOHASH_DEFAULT_PRECISION,
    place_resolver: PlaceResolver | None = None,
    auto_reverse_geocode: bool = True,
) -> int:
    """Create a DRAFT SPOT and return its id.

    The Create Spot flow now begins with only the creator and title. The link is
    generated immediately, while location/value/schedule fields can be filled
    later by modify_draft_spot(). Existing tests and seed data may still pass
    the full field set here; all supplied fields are validated in one place.
    """
    title = _validate_spot_title(title)
    radius = _validate_spot_radius(
        const.DEFAULT_DRAFT_SPOT_RADIUS_METRES if radius is None else radius
    )
    claim_duration = _validate_spot_claim_duration(
        const.DEFAULT_DRAFT_SPOT_CLAIM_DURATION_SECONDS if claim_duration is None else claim_duration
    )
    max_claims_per_user = _validate_spot_max_claims_per_user(
        const.DEFAULT_DRAFT_SPOT_MAX_CLAIMS_PER_USER if max_claims_per_user is None else max_claims_per_user
    )
    max_total_claims = _validate_spot_max_total_claims(
        const.DEFAULT_DRAFT_SPOT_MAX_TOTAL_CLAIMS if max_total_claims is None else max_total_claims,
        is_prizedraw=bool(is_prizedraw),
    )
    use_password = _validate_bool_flag(
        const.DEFAULT_DRAFT_SPOT_USE_PASSWORD if use_password is None else use_password,
        field_name="use_password",
    )
    if bool(is_prizedraw) and use_password:
        raise ValueError("Prizedraw spots do not use passwords")
    if use_password and max_total_claims <= 0:
        raise ValueError("use_password requires a finite total participant count")
    total_value = _validate_int_range(
        const.DEFAULT_DRAFT_SPOT_TOTAL_VALUE if total_value is None else total_value,
        field_name="total_value",
        min_value=const.MIN_SPOT_TOTAL_VALUE,
    )
    starts_at, ends_at = _validate_spot_time_window(starts_at, ends_at)

    lat, long = _validate_optional_coordinates(lat, long)
    geohash = encode_geohash(lat, long, geohash_precision) if lat is not None and long is not None else None
    if lat is not None and long is not None:
        city, country = await _derive_place_labels(
            lat,
            long,
            city=city,
            country=country,
            place_resolver=place_resolver if auto_reverse_geocode else None,
        )
    else:
        city = _clean_optional_text(city)
        country = _clean_optional_text(country)

    link = _clean_optional_text(link) or await _generate_unique_spot_link(db)
    deposit_record = await _generate_unique_spot_deposit_record(db)
    creation_fee = configured_spot_creation_fee(is_prizedraw=bool(is_prizedraw))
    creation_fee_address = str(
        getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "") or ""
    ).strip()
    if not creation_fee_address:
        raise ValueError("platform fee address must be configured before creating spots")
    creation_fee_address = wallet.normalise_nimiq_address(
        creation_fee_address,
        field_name="platform fee address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )

    cur = await db.execute(
        f"""
        INSERT INTO {schema.SPOT_TABLE_NAME} (
            {schema.SPOT_CREATED_BY},
            {schema.SPOT_LINK},
            {schema.SPOT_DEPOSIT_ADDRESS},
            {schema.SPOT_DEPOSIT_KEY_INDEX},
            {schema.SPOT_DEPOSIT_KEY_PATH},
            {schema.SPOT_DEPOSIT_KEY_VERSION},
            {schema.SPOT_TITLE},
            {schema.SPOT_DESC},
            {schema.SPOT_LAT},
            {schema.SPOT_LONG},
            {schema.SPOT_GEOHASH},
            {schema.SPOT_CITY},
            {schema.SPOT_COUNTRY},
            {schema.SPOT_RADIUS},
            {schema.SPOT_CLAIM_DURATION},
            {schema.SPOT_MAX_CLAIMS_PER_USER},
            {schema.SPOT_MAX_TOTAL_CLAIMS},
            {schema.SPOT_USE_PASSWORD},
            {schema.SPOT_TOTAL_VALUE},
            {schema.SPOT_CREATION_FEE},
            {schema.SPOT_CREATION_FEE_ADDRESS},
            {schema.SPOT_STARTS_AT},
            {schema.SPOT_ENDS_AT},
            {schema.SPOT_STATUS}
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            int(created_by),
            link,
            deposit_record.address,
            deposit_record.key_index,
            deposit_record.key_path,
            deposit_record.key_version,
            title,
            _clean_optional_text(desc),
            lat,
            long,
            geohash,
            city,
            country,
            radius,
            claim_duration,
            max_claims_per_user,
            max_total_claims,
            use_password,
            total_value,
            creation_fee,
            creation_fee_address,
            starts_at,
            ends_at,
            const.SPOT_STATUS_DRAFT,
        ),
    )
    return int(cur.lastrowid)


async def modify_draft_spot(
    db,
    *,
    spot_id: int,
    title: Any = _UNSET,
    desc: Any = _UNSET,
    lat: Any = _UNSET,
    long: Any = _UNSET,
    radius: Any = _UNSET,
    claim_duration: Any = _UNSET,
    max_claims_per_user: Any = _UNSET,
    max_total_claims: Any = _UNSET,
    prize_count: Any = _UNSET,
    use_password: Any = _UNSET,
    total_value: Any = _UNSET,
    starts_at: Any = _UNSET,
    ends_at: Any = _UNSET,
    city: Any = _UNSET,
    country: Any = _UNSET,
    geohash_precision: int = GEOHASH_DEFAULT_PRECISION,
    place_resolver: PlaceResolver | None = None,
    auto_reverse_geocode: bool = True,
) -> None:
    """Update editable SPOT fields, but only while the SPOT is still DRAFT.

    SPOT_ID, SPOT_CREATED_BY, SPOT_LINK, SPOT_DEPOSIT_ADDRESS, deposit-key metadata, and SPOT_CREATED_AT are intentionally
    never accepted here. SPOT_STATUS is handled by modify_spot_status() and the
    more restrictive publish_spot() helper.
    """
    existing = await _require_draft_spot(db, spot_id=spot_id)

    updates: dict[str, Any] = {}

    if title is not _UNSET:
        updates[schema.SPOT_TITLE] = _validate_spot_title(title)
    if desc is not _UNSET:
        updates[schema.SPOT_DESC] = _clean_optional_text(desc)
    if radius is not _UNSET:
        updates[schema.SPOT_RADIUS] = _validate_spot_radius(radius)
    if claim_duration is not _UNSET:
        updates[schema.SPOT_CLAIM_DURATION] = _validate_spot_claim_duration(claim_duration)
    if max_claims_per_user is not _UNSET:
        updates[schema.SPOT_MAX_CLAIMS_PER_USER] = _validate_spot_max_claims_per_user(max_claims_per_user)
    prizedraw = await get_prizedraw(db, spot_id=spot_id)
    spot_is_prizedraw = prizedraw is not None
    if max_total_claims is not _UNSET:
        updates[schema.SPOT_MAX_TOTAL_CLAIMS] = _validate_spot_max_total_claims(
            max_total_claims,
            is_prizedraw=spot_is_prizedraw,
        )
    if use_password is not _UNSET:
        updates[schema.SPOT_USE_PASSWORD] = _validate_bool_flag(use_password, field_name="use_password")

    next_max_claims_per_user = int(
        updates.get(
            schema.SPOT_MAX_CLAIMS_PER_USER,
            existing.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 0,
        )
    )
    next_max_total_claims = int(
        updates.get(
            schema.SPOT_MAX_TOTAL_CLAIMS,
            existing.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0,
        )
    )
    next_use_password = int(updates.get(schema.SPOT_USE_PASSWORD, existing.get(schema.SPOT_USE_PASSWORD) or 0))
    next_prize_count: int | None = None
    if spot_is_prizedraw:
        current_prize_count = int(prizedraw[schema.PRIZEDRAW_PRIZE_COUNT])
        next_prize_count = (
            current_prize_count
            if prize_count is _UNSET
            else _validate_prizedraw_prize_count(prize_count)
        )
        _validate_prizedraw_participant_limits(
            max_claims_per_user=next_max_claims_per_user,
            max_total_claims=next_max_total_claims,
            prize_count=next_prize_count,
        )
        if next_use_password:
            raise ValueError("Prizedraw spots do not use passwords")
    elif prize_count is not _UNSET:
        raise ValueError("spot is not a Prizedraw")

    if next_use_password and next_max_total_claims <= 0:
        raise ValueError("use_password requires a finite total participant count")

    if total_value is not _UNSET:
        next_total_value = _validate_int_range(
            total_value,
            field_name="total_value",
            min_value=const.MIN_SPOT_TOTAL_VALUE,
        )
        existing_total_value = int(existing.get(schema.SPOT_TOTAL_VALUE) or 0)
        if next_total_value != existing_total_value and await has_spot_nonfailed_deposit_transactions(db, spot_id=spot_id):
            raise ValueError("total_value cannot be changed after a pending or confirmed deposit has been submitted")
        updates[schema.SPOT_TOTAL_VALUE] = next_total_value

    next_starts_at = existing.get(schema.SPOT_STARTS_AT) if starts_at is _UNSET else starts_at
    next_ends_at = existing.get(schema.SPOT_ENDS_AT) if ends_at is _UNSET else ends_at
    if starts_at is not _UNSET or ends_at is not _UNSET:
        next_starts_at, next_ends_at = _validate_spot_time_window(next_starts_at, next_ends_at)
        updates[schema.SPOT_STARTS_AT] = next_starts_at
        updates[schema.SPOT_ENDS_AT] = next_ends_at

    coordinates_changed = lat is not _UNSET or long is not _UNSET
    next_lat = existing.get(schema.SPOT_LAT) if lat is _UNSET else lat
    next_long = existing.get(schema.SPOT_LONG) if long is _UNSET else long

    if coordinates_changed:
        next_lat, next_long = _validate_optional_coordinates(next_lat, next_long)
        updates[schema.SPOT_LAT] = next_lat
        updates[schema.SPOT_LONG] = next_long
        updates[schema.SPOT_GEOHASH] = (
            encode_geohash(next_lat, next_long, geohash_precision)
            if next_lat is not None and next_long is not None
            else None
        )

    place_labels_changed = city is not _UNSET or country is not _UNSET or coordinates_changed
    if place_labels_changed:
        next_city = existing.get(schema.SPOT_CITY) if city is _UNSET else city
        next_country = existing.get(schema.SPOT_COUNTRY) if country is _UNSET else country
        if next_lat is not None and next_long is not None:
            next_city, next_country = await _derive_place_labels(
                next_lat,
                next_long,
                city=next_city,
                country=next_country,
                place_resolver=place_resolver if auto_reverse_geocode else None,
            )
        else:
            next_city = _clean_optional_text(next_city)
            next_country = _clean_optional_text(next_country)
        updates[schema.SPOT_CITY] = next_city
        updates[schema.SPOT_COUNTRY] = next_country

    prize_count_changed = (
        spot_is_prizedraw
        and prize_count is not _UNSET
        and next_prize_count != int(prizedraw[schema.PRIZEDRAW_PRIZE_COUNT])
    )
    if not updates and not prize_count_changed:
        return

    if updates:
        assignments = ", ".join(f"{field} = ?" for field in updates)
        params = [*updates.values(), int(spot_id), const.SPOT_STATUS_DRAFT]
        cur = await db.execute(
            f"""
            UPDATE {schema.SPOT_TABLE_NAME}
            SET {assignments}
            WHERE {schema.SPOT_ID} = ?
              AND {schema.SPOT_STATUS} = ?;
            """,
            tuple(params),
        )
        _require_one(cur.rowcount, f"Failed to update draft spot id={spot_id}")

    if prize_count_changed:
        cur = await db.execute(
            f"""
            UPDATE {schema.PRIZEDRAW_TABLE_NAME}
            SET {schema.PRIZEDRAW_PRIZE_COUNT} = ?
            WHERE {schema.PRIZEDRAW_SPOT_ID} = ?;
            """,
            (int(next_prize_count), int(spot_id)),
        )
        _require_one(cur.rowcount, f"Failed to update Prizedraw spot_id={spot_id}")

    # Draft editing stores only the intent to require claim codes.
    # Codes themselves are generated at publish time, after the draft is funded
    # and no longer editable. Clean up any unused legacy draft codes left by the
    # old draft-time generation behaviour.
    await delete_unused_claim_codes_for_spot(db, spot_id=spot_id)


async def modify_spot_desc(db, *, spot_id: int, desc: str | None) -> None:
    await modify_draft_spot(db, spot_id=spot_id, desc=desc)


async def modify_spot_status(db, *, spot_id: int, status: int) -> None:
    """Low-level status change helper.

    Status is the one SPOT field that may change after draft. Use publish_spot()
    for ordinary creator publishing, because it enforces completeness and
    funding. This low-level helper remains useful for cancellation, completion,
    moderation, and deterministic test data.
    """
    cur = await db.execute(
        f"""
        UPDATE {schema.SPOT_TABLE_NAME}
        SET {schema.SPOT_STATUS} = ?
        WHERE {schema.SPOT_ID} = ?;
        """,
        (int(status), int(spot_id)),
    )
    _require_one(cur.rowcount, f"Failed to update spot status id={spot_id}")


async def publish_spot(db, *, spot_id: int) -> None:
    if not await can_publish_spot(db, spot_id=spot_id):
        raise ValueError("spot is not complete and fully funded enough to publish")

    # Password/claim-code rows are deliberately created only at publish time.
    # A funded draft can still be edited, so creating codes earlier would waste
    # writes and could leave stale code counts when participant limits change.
    await ensure_claim_codes_for_publish(db, spot_id=spot_id)

    # A NULL starts_at means "start when published". Because SPOT.ends_at is
    # stored relative to starts_at, publishing stamps starts_at to make the
    # active window unambiguous.
    cur = await db.execute(
        f"""
        UPDATE {schema.SPOT_TABLE_NAME}
        SET {schema.SPOT_STARTS_AT} = COALESCE({schema.SPOT_STARTS_AT}, unixepoch()),
            {schema.SPOT_STATUS} = ?
        WHERE {schema.SPOT_ID} = ?
          AND {schema.SPOT_STATUS} = ?
          AND {schema.SPOT_CANCELLATION_STARTED_AT} IS NULL;
        """,
        (const.SPOT_STATUS_PUBLISHED, int(spot_id), const.SPOT_STATUS_DRAFT),
    )
    _require_one(cur.rowcount, f"Failed to publish spot id={spot_id}")


async def set_spot_status_to_published(db, *, spot_id: int) -> None:
    await publish_spot(db, spot_id=spot_id)


async def set_spot_status_to_completed(db, *, spot_id: int) -> None:
    await modify_spot_status(db, spot_id=spot_id, status=const.SPOT_STATUS_COMPLETED)


async def set_spot_status_to_cancelled(db, *, spot_id: int) -> None:
    await modify_spot_status(db, spot_id=spot_id, status=const.SPOT_STATUS_CANCELLED)


async def mark_spot_cancellation_started(db, *, spot_id: int) -> bool:
    """Durably mark a funded draft or published Spot cancellation as started.

    Returns True when this call established the marker and False when a previous
    cancellation attempt had already marked the Spot.
    """
    cur = await db.execute(
        f"""
        UPDATE {schema.SPOT_TABLE_NAME}
        SET {schema.SPOT_CANCELLATION_STARTED_AT} = unixepoch()
        WHERE {schema.SPOT_ID} = ?
          AND {schema.SPOT_STATUS} IN (?, ?)
          AND {schema.SPOT_CANCELLATION_STARTED_AT} IS NULL;
        """,
        (
            int(spot_id),
            const.SPOT_STATUS_DRAFT,
            const.SPOT_STATUS_PUBLISHED,
        ),
    )
    return int(cur.rowcount or 0) == 1


async def get_pending_cancellation_spot_ids(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[int]:
    """Return draft/published Spots whose durable cancellation is unfinished."""
    rows = await db.execute_fetchall(
        f"""
        SELECT {schema.SPOT_ID} AS spot_id
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_STATUS} IN (?, ?)
          AND {schema.SPOT_CANCELLATION_STARTED_AT} IS NOT NULL
        ORDER BY {schema.SPOT_CANCELLATION_STARTED_AT} ASC, {schema.SPOT_ID} ASC
        LIMIT ?;
        """,
        (
            const.SPOT_STATUS_DRAFT,
            const.SPOT_STATUS_PUBLISHED,
            max(1, min(int(limit), int(MAX_LIMIT))),
        ),
    )
    return [int(row["spot_id"]) for row in rows]


async def clear_spot_cancellation_started(db, *, spot_id: int) -> None:
    """Clear a cancellation marker. Intended only for explicit administrative recovery."""
    await db.execute(
        f"""
        UPDATE {schema.SPOT_TABLE_NAME}
        SET {schema.SPOT_CANCELLATION_STARTED_AT} = NULL
        WHERE {schema.SPOT_ID} = ?;
        """,
        (int(spot_id),),
    )


async def set_spot_status_to_banned(db, *, spot_id: int) -> None:
    await modify_spot_status(db, spot_id=spot_id, status=const.SPOT_STATUS_BANNED)


# ---------------------------------------------------------------------------
# SPOT: getters / checks / metrics
# ---------------------------------------------------------------------------

async def get_spot(db, *, spot_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_ID} = ?;
        """,
        (int(spot_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_spot_by_link(db, *, link: str) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_LINK} = ?;
        """,
        (str(link),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_public_spot(db, *, spot_id: int) -> RowDict | None:
    """Return a published, non-expired SPOT using the public list view."""
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        WHERE {schema.SPOT_ID} = ?;
        """,
        (int(spot_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_spot_owner_summary(db, *, spot_id: int) -> RowDict | None:
    """Return owner/admin summary for one SPOT using view_spot_owner_summary."""
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_OWNER_SUMMARY}
        WHERE {schema.SPOT_ID} = ?;
        """,
        (int(spot_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_spots_by_user(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return owner summaries for all SPOTs created by a USER."""
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_OWNER_SUMMARY}
        WHERE {schema.SPOT_CREATED_BY} = ?
        ORDER BY {schema.SPOT_CREATED_AT} DESC, {schema.SPOT_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        (int(user_id), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def count_draft_spots_by_user(db, *, user_id: int) -> int:
    """Return how many editable draft SPOTs a USER currently owns."""
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_CREATED_BY} = ?
          AND {schema.SPOT_STATUS} = ?;
        """,
        (int(user_id), const.SPOT_STATUS_DRAFT),
    )
    row = await cur.fetchone()
    return int(row["n"] or 0)


async def get_published_spots_that_have_started(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        WHERE availability_rank = 0
        ORDER BY soon_sort ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (_clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_published_spots_that_have_not_started(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        WHERE availability_rank = 1
        ORDER BY soon_sort ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (_clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_public_spots_by_geohash(
    db,
    *,
    geohash_prefix: str,
    current_only: bool = False,
    upcoming_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return public SPOTs whose geohash begins with geohash_prefix.

    This is the main map/list query. availability_rank is 0 for current and 1
    for upcoming, so the ordering puts claimable spots first.
    """
    params: list[Any] = [f"{str(geohash_prefix).strip()}%"]
    extra = ""
    if current_only and upcoming_only:
        raise ValueError("current_only and upcoming_only cannot both be True")
    if current_only:
        extra = "AND availability_rank = 0"
    elif upcoming_only:
        extra = "AND availability_rank = 1"

    params.extend([_clamp_limit(limit), _normalise_offset(offset)])
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        WHERE {schema.SPOT_GEOHASH} LIKE ?
        {extra}
        ORDER BY availability_rank ASC, soon_sort ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        tuple(params),
    )
    return _rows_to_dicts(rows)


async def get_public_spots_by_geohash_prefixes(
    db,
    *,
    geohash_prefixes: list[str],
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return public SPOTs matching any geohash prefix.

    Useful when the map viewport is represented by several neighbouring cells.
    """
    prefixes = [p.strip() for p in geohash_prefixes if p and p.strip()]
    if not prefixes:
        return []

    where = " OR ".join(f"{schema.SPOT_GEOHASH} LIKE ?" for _ in prefixes)
    params: list[Any] = [f"{p}%" for p in prefixes]
    params.extend([_clamp_limit(limit), _normalise_offset(offset)])

    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        WHERE ({where})
        ORDER BY availability_rank ASC, soon_sort ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        tuple(params),
    )
    return _rows_to_dicts(rows)


async def get_public_spots_by_city_country(
    db,
    *,
    city: str | None = None,
    country: str | None = None,
    current_only: bool = False,
    upcoming_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    if current_only and upcoming_only:
        raise ValueError("current_only and upcoming_only cannot both be True")

    where_parts: list[str] = []
    params: list[Any] = []

    if city is not None:
        where_parts.append(f"{schema.SPOT_CITY} = ?")
        params.append(city)
    if country is not None:
        where_parts.append(f"{schema.SPOT_COUNTRY} = ?")
        params.append(country)
    if current_only:
        where_parts.append("availability_rank = 0")
    elif upcoming_only:
        where_parts.append("availability_rank = 1")

    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    params.extend([_clamp_limit(limit), _normalise_offset(offset)])

    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        {where_sql}
        ORDER BY availability_rank ASC, soon_sort ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        tuple(params),
    )
    return _rows_to_dicts(rows)


async def get_public_spots_in_bounds(
    db,
    *,
    min_lat: float,
    max_lat: float,
    min_long: float,
    max_long: float,
    current_only: bool = False,
    upcoming_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return public SPOTs inside a lat/long bounding box.

    This is convenient for map viewports. It is less index-friendly than a
    geohash-prefix lookup unless you later add a lat/long index.
    """
    if current_only and upcoming_only:
        raise ValueError("current_only and upcoming_only cannot both be True")

    extra = ""
    if current_only:
        extra = "AND availability_rank = 0"
    elif upcoming_only:
        extra = "AND availability_rank = 1"

    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        WHERE {schema.SPOT_LAT} BETWEEN ? AND ?
          AND {schema.SPOT_LONG} BETWEEN ? AND ?
          {extra}
        ORDER BY availability_rank ASC, soon_sort ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (
            float(min_lat),
            float(max_lat),
            float(min_long),
            float(max_long),
            _clamp_limit(limit),
            _normalise_offset(offset),
        ),
    )
    return _rows_to_dicts(rows)


async def count_spots(db) -> int:
    cur = await db.execute(f"SELECT COUNT(*) AS n FROM {schema.SPOT_TABLE_NAME};")
    row = await cur.fetchone()
    return int(row["n"])


async def count_spots_by_status(db, *, status: int) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_STATUS} = ?;
        """,
        (int(status),),
    )
    row = await cur.fetchone()
    return int(row["n"])


async def count_public_spots_by_city_country(
    db,
    *,
    city: str | None = None,
    country: str | None = None,
) -> int:
    where_parts: list[str] = []
    params: list[Any] = []
    if city is not None:
        where_parts.append(f"{schema.SPOT_CITY} = ?")
        params.append(city)
    if country is not None:
        where_parts.append(f"{schema.SPOT_COUNTRY} = ?")
        params.append(country)
    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.SPOT_VIEW_PUBLIC_LIST}
        {where_sql};
        """,
        tuple(params),
    )
    row = await cur.fetchone()
    return int(row["n"])


async def get_spot_claim_counts(db, *, spot_id: int) -> RowDict:
    summary = await get_spot_owner_summary(db, spot_id=spot_id)
    if summary is None:
        return {
            "claim_count": 0,
            "pending_claim_count": 0,
            "success_claim_count": 0,
            "failed_claim_count": 0,
        }
    return {
        "claim_count": int(summary.get("claim_count", 0)),
        "pending_claim_count": int(summary.get("pending_claim_count", 0)),
        "success_claim_count": int(summary.get("success_claim_count", 0)),
        "failed_claim_count": int(summary.get("failed_claim_count", 0)),
    }


async def is_spot_currently_public(db, *, spot_id: int) -> bool:
    return await get_public_spot(db, spot_id=spot_id) is not None


async def is_spot_owned_by_user(db, *, spot_id: int, user_id: int) -> bool:
    spot = await get_spot(db, spot_id=spot_id)
    return bool(spot and int(spot[schema.SPOT_CREATED_BY]) == int(user_id))


async def delete_draft_spot(db, *, spot_id: int) -> None:
    """Delete one SPOT, but only while it is still a DRAFT.

    The database cascades PRIZEDRAW, CLAIM, CLAIM_CODE, and REPORT rows.
    TRANSACTION rows keep their audit record and have spot_id set to NULL by
    the schema. Published or historical spots should be cancelled/completed
    rather than physically deleted.
    """
    spot = await get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("only draft spots can be deleted")
    if await has_spot_deposit_transactions(db, spot_id=int(spot_id)):
        raise ValueError(
            "drafts with deposit history cannot be deleted; cancel the draft from My Spots so transaction records and any recoverable NIM are preserved"
        )

    cur = await db.execute(
        f"""
        DELETE FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_ID} = ?
          AND {schema.SPOT_STATUS} = ?;
        """,
        (int(spot_id), const.SPOT_STATUS_DRAFT),
    )
    _require_one(cur.rowcount, f"Failed to delete draft spot id={spot_id}")


async def get_confirmed_spot_deposit_total(db, *, spot_id: int) -> int:
    """Return confirmed FILL_SPOT amount for one SPOT in Luna."""
    cur = await db.execute(
        f"""
        SELECT COALESCE(SUM({schema.TRANS_AMOUNT}), 0) AS amount
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?;
        """,
        (int(spot_id), const.TRANS_TYPE_FILL_SPOT, const.TRANS_STATUS_CONFIRMED),
    )
    row = await cur.fetchone()
    return int(row["amount"] or 0)


async def get_confirmed_spot_funding_address(db, *, spot_id: int) -> str | None:
    """Return the first confirmed on-chain sender for a Spot's deposits.

    This address becomes the Spot's funding wallet. Later top-ups must come from
    the same wallet so cancellation can safely refund one known contributor.
    """
    cur = await db.execute(
        f"""
        SELECT {schema.TRANS_FROM_ADDRESS} AS from_address
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?
          AND TRIM({schema.TRANS_FROM_ADDRESS}) != ''
        ORDER BY {schema.TRANS_CREATED_AT} ASC, {schema.TRANS_ID} ASC
        LIMIT 1;
        """,
        (int(spot_id), const.TRANS_TYPE_FILL_SPOT, const.TRANS_STATUS_CONFIRMED),
    )
    row = await cur.fetchone()
    if row is None or not row["from_address"]:
        return None
    return str(row["from_address"]).strip()


async def count_spot_deposit_transactions(db, *, spot_id: int) -> int:
    """Return how many FILL_SPOT transactions have ever been submitted."""
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?;
        """,
        (int(spot_id), const.TRANS_TYPE_FILL_SPOT),
    )
    row = await cur.fetchone()
    return int(row["n"] or 0)


async def has_spot_deposit_transactions(db, *, spot_id: int) -> bool:
    """Return True once any deposit transaction exists for the SPOT."""
    return await count_spot_deposit_transactions(db, spot_id=spot_id) > 0


async def count_spot_nonfailed_deposit_transactions(db, *, spot_id: int) -> int:
    """Return pending/confirmed FILL_SPOT transaction count for one SPOT."""
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} IN (?, ?);
        """,
        (
            int(spot_id),
            const.TRANS_TYPE_FILL_SPOT,
            const.TRANS_STATUS_PENDING,
            const.TRANS_STATUS_CONFIRMED,
        ),
    )
    row = await cur.fetchone()
    return int(row["n"] or 0)


async def has_spot_nonfailed_deposit_transactions(db, *, spot_id: int) -> bool:
    """Return True once a pending or confirmed deposit exists for the SPOT."""
    return await count_spot_nonfailed_deposit_transactions(db, spot_id=spot_id) > 0


async def get_spot_deposit_totals(db, *, spot_id: int) -> dict[str, int]:
    """Return FILL_SPOT totals by transaction status for one SPOT."""
    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.TRANS_STATUS} AS status,
            COALESCE(SUM({schema.TRANS_AMOUNT}), 0) AS amount,
            COUNT(*) AS n
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
        GROUP BY {schema.TRANS_STATUS};
        """,
        (int(spot_id), const.TRANS_TYPE_FILL_SPOT),
    )

    out = {
        "confirmed_amount": 0,
        "pending_amount": 0,
        "failed_amount": 0,
        "submitted_count": 0,
    }
    for row in rows:
        status = int(row["status"] if row["status"] is not None else -1)
        amount = int(row["amount"] or 0)
        out["submitted_count"] += int(row["n"] or 0)
        if status == const.TRANS_STATUS_CONFIRMED:
            out["confirmed_amount"] = amount
        elif status == const.TRANS_STATUS_PENDING:
            out["pending_amount"] = amount
        elif status == const.TRANS_STATUS_FAILED:
            out["failed_amount"] = amount
    return out


async def get_spot_creation_fee_totals(db, *, spot_id: int) -> dict[str, int]:
    """Return creation-fee transaction totals by status for one Spot."""
    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.TRANS_STATUS} AS status,
            COALESCE(SUM({schema.TRANS_AMOUNT}), 0) AS amount,
            COUNT(*) AS n
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
        GROUP BY {schema.TRANS_STATUS};
        """,
        (int(spot_id), const.TRANS_TYPE_CREATION_FEE),
    )

    out = {
        "confirmed_amount": 0,
        "pending_amount": 0,
        "failed_amount": 0,
        "submitted_count": 0,
    }
    for row in rows:
        status = int(row["status"] if row["status"] is not None else -1)
        amount = int(row["amount"] or 0)
        out["submitted_count"] += int(row["n"] or 0)
        if status == const.TRANS_STATUS_CONFIRMED:
            out["confirmed_amount"] = amount
        elif status == const.TRANS_STATUS_PENDING:
            out["pending_amount"] = amount
        elif status == const.TRANS_STATUS_FAILED:
            out["failed_amount"] = amount
    return out


async def has_nonfailed_spot_creation_fee_transaction(db, *, spot_id: int) -> bool:
    """Return True once a pending or confirmed creation-fee leg exists."""
    cur = await db.execute(
        f"""
        SELECT 1
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} != ?
        LIMIT 1;
        """,
        (
            int(spot_id),
            const.TRANS_TYPE_CREATION_FEE,
            const.TRANS_STATUS_FAILED,
        ),
    )
    return await cur.fetchone() is not None


async def has_confirmed_spot_creation_fee_transaction(db, *, spot_id: int) -> bool:
    """Return True only when the snapshotted fee reached its snapshotted address."""
    spot = await get_spot(db, spot_id=int(spot_id))
    if spot is None:
        return False
    fee_amount = spot_creation_fee_amount(spot)
    if fee_amount <= 0:
        return True

    cur = await db.execute(
        f"""
        SELECT 1
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?
          AND {schema.TRANS_USER_ID} = ?
          AND {schema.TRANS_AMOUNT} = ?
          AND UPPER(REPLACE({schema.TRANS_FROM_ADDRESS}, ' ', '')) =
              UPPER(REPLACE(?, ' ', ''))
          AND UPPER(REPLACE({schema.TRANS_TO_ADDRESS}, ' ', '')) =
              UPPER(REPLACE(?, ' ', ''))
        LIMIT 1;
        """,
        (
            int(spot_id),
            const.TRANS_TYPE_CREATION_FEE,
            const.TRANS_STATUS_CONFIRMED,
            int(spot[schema.SPOT_CREATED_BY]),
            fee_amount,
            str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
            str(spot.get(schema.SPOT_CREATION_FEE_ADDRESS) or ""),
        ),
    )
    return await cur.fetchone() is not None


async def get_spot_ids_ready_for_creation_fee(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[int]:
    """Return fully funded drafts that still need their one-time creation fee.

    A Spot remains excluded once a non-failed creation-fee intent exists. This
    is the database-backed idempotency guard that prevents duplicate sends
    across restarts or concurrent reconciliation passes.
    """
    rows = await db.execute_fetchall(
        f"""
        SELECT s.{schema.SPOT_ID} AS spot_id
        FROM {schema.SPOT_TABLE_NAME} s
        WHERE s.{schema.SPOT_STATUS} IN (?, ?, ?)
          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
          AND s.{schema.SPOT_CREATION_FEE} > 0
          AND (
                SELECT COALESCE(SUM(t.{schema.TRANS_AMOUNT}), 0)
                FROM {schema.TRANS_TABLE_NAME} t
                WHERE t.{schema.TRANS_SPOT_ID} = s.{schema.SPOT_ID}
                  AND t.{schema.TRANS_TYPE} = ?
                  AND t.{schema.TRANS_STATUS} = ?
          ) >= s.{schema.SPOT_TOTAL_VALUE} + s.{schema.SPOT_CREATION_FEE}
          AND NOT EXISTS (
                SELECT 1
                FROM {schema.TRANS_TABLE_NAME} f
                WHERE f.{schema.TRANS_SPOT_ID} = s.{schema.SPOT_ID}
                  AND f.{schema.TRANS_TYPE} = ?
                  AND f.{schema.TRANS_STATUS} != ?
          )
        ORDER BY s.{schema.SPOT_UPDATED_AT} ASC, s.{schema.SPOT_ID} ASC
        LIMIT ?;
        """,
        (
            const.SPOT_STATUS_DRAFT,
            const.SPOT_STATUS_PUBLISHED,
            const.SPOT_STATUS_COMPLETED,
            const.TRANS_TYPE_FILL_SPOT,
            const.TRANS_STATUS_CONFIRMED,
            const.TRANS_TYPE_CREATION_FEE,
            const.TRANS_STATUS_FAILED,
            _clamp_limit(limit),
        ),
    )
    return [int(row["spot_id"]) for row in rows]


async def can_publish_spot(db, *, spot_id: int) -> bool:
    """Return True when a draft SPOT is complete, creator-active, and funded."""
    spot = await get_spot(db, spot_id=spot_id)
    if not spot:
        return False

    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        return False
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        return False

    if not await can_user_create_spot(db, user_id=int(spot[schema.SPOT_CREATED_BY])):
        return False

    required_values = (
        spot.get(schema.SPOT_TITLE),
        spot.get(schema.SPOT_DEPOSIT_ADDRESS),
        spot.get(schema.SPOT_LAT),
        spot.get(schema.SPOT_LONG),
        spot.get(schema.SPOT_RADIUS),
        spot.get(schema.SPOT_MAX_TOTAL_CLAIMS),
        spot.get(schema.SPOT_TOTAL_VALUE),
    )
    if any(value is None for value in required_values):
        return False

    total_value = int(spot.get(schema.SPOT_TOTAL_VALUE) or 0)
    if total_value <= 0:
        return False

    max_total_claims = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    prizedraw = await get_prizedraw(db, spot_id=spot_id)
    spot_is_prizedraw = prizedraw is not None
    if max_total_claims < const.MIN_SPOT_MAX_TOTAL_CLAIMS and not spot_is_prizedraw:
        return False
    if spot_is_prizedraw:
        try:
            _validate_prizedraw_participant_limits(
                max_claims_per_user=int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 0),
                max_total_claims=max_total_claims,
                prize_count=int(prizedraw[schema.PRIZEDRAW_PRIZE_COUNT]),
            )
        except ValueError:
            return False

    if not await spot_meets_minimum_payout(db, spot_id=spot_id):
        return False

    starts_at = spot.get(schema.SPOT_STARTS_AT)
    ends_after = int(spot.get(schema.SPOT_ENDS_AT) or 0)
    if ends_after < const.MIN_SPOT_ENDS_AFTER_SECONDS:
        return False
    if starts_at is not None:
        now = await get_unixepoch(db)
        if int(starts_at) + ends_after <= now:
            return False

    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
    if use_password:
        if max_total_claims <= 0:
            return False
        if spot_is_prizedraw:
            return False

    confirmed_amount = await get_confirmed_spot_deposit_total(db, spot_id=spot_id)
    if confirmed_amount < spot_required_deposit_amount(spot):
        return False

    # The creator deposits the Spot value and the snapshotted creation fee in one
    # combined payment. Once that complete deposit confirms, the draft is funded.
    # The internal fee transfer is reconciled independently and must not make the
    # creator wait to publish a fully funded Spot.
    return True


async def is_spot_claim_capacity_available(db, *, spot_id: int) -> bool:
    """Return whether the SPOT has capacity for another claim/entry.

    Standard spots count successful claims against max_total_claims. Pending
    duration claims may exist while rewards are still available, but once the
    standard Spot reaches capacity the remaining pending duration claims are
    failed by fail_pending_standard_duration_claims_if_capacity_full().

    Prizedraw entries are different: an entry is already the final
    participation action, so pending entries count as participants until future
    draw settlement resolves them.
    """
    summary = await get_spot_owner_summary(db, spot_id=spot_id)
    if not summary:
        return False

    max_total = int(summary[schema.SPOT_MAX_TOTAL_CLAIMS])
    spot_is_prizedraw = await is_prizedraw(db, spot_id=spot_id)
    counted_claims = int(summary.get("success_claim_count", 0))
    if spot_is_prizedraw:
        counted_claims += int(summary.get("pending_claim_count", 0))

    if max_total > 0 and counted_claims >= max_total:
        return False

    claim_code_count = int(summary.get("claim_code_count", 0))
    unused_code_count = int(summary.get("unused_code_count", 0))
    if claim_code_count > 0 and unused_code_count <= 0:
        return False

    return True


async def is_spot_currently_claimable(db, *, spot_id: int) -> bool:
    if not await is_spot_currently_public(db, spot_id=spot_id):
        return False
    public_row = await get_public_spot(db, spot_id=spot_id)
    if not public_row or int(public_row["availability_rank"]) != 0:
        return False
    return await is_spot_claim_capacity_available(db, spot_id=spot_id)


# ---------------------------------------------------------------------------
# PRIZEDRAW: writes / getters
# ---------------------------------------------------------------------------

async def create_prizedraw(
    db,
    *,
    created_by: int,
    title: str,
    desc: str | None = None,
    lat: float | None = None,
    long: float | None = None,
    radius: int | None = None,
    claim_duration: int | None = None,
    max_claims_per_user: int | None = None,
    max_total_claims: int | None = None,
    total_value: int | None = None,
    prize_count: int | None = None,
    starts_at: int | None = None,
    ends_at: int | None = None,
    use_password: bool | int | None = None,
    link: str | None = None,
    city: str | None = None,
    country: str | None = None,
    geohash_precision: int = GEOHASH_DEFAULT_PRECISION,
    place_resolver: PlaceResolver | None = None,
    auto_reverse_geocode: bool = True,
) -> int:
    """Create a DRAFT PRIZEDRAW SPOT and return the SPOT id.

    Like create_spot(), this supports the new two-step Create Spot flow. The
    first step may pass only created_by + title; prize_count and other fields
    can be adjusted later while the paired SPOT remains DRAFT.
    """
    effective_max_total_claims = _validate_spot_max_total_claims(
        const.DEFAULT_DRAFT_PRIZEDRAW_MAX_TOTAL_CLAIMS if max_total_claims is None else max_total_claims,
        is_prizedraw=True,
    )
    effective_prize_count = _validate_prizedraw_prize_count(
        const.DEFAULT_DRAFT_PRIZEDRAW_PRIZE_COUNT if prize_count is None else prize_count
    )
    effective_max_claims_per_user = _validate_spot_max_claims_per_user(
        const.DEFAULT_DRAFT_SPOT_MAX_CLAIMS_PER_USER
        if max_claims_per_user is None
        else max_claims_per_user
    )
    _validate_prizedraw_participant_limits(
        max_claims_per_user=effective_max_claims_per_user,
        max_total_claims=effective_max_total_claims,
        prize_count=effective_prize_count,
    )
    if use_password not in (None, False, 0):
        raise ValueError("Prizedraw spots do not use passwords")

    spot_id = await create_spot(
        db,
        created_by=created_by,
        title=title,
        desc=desc,
        lat=lat,
        long=long,
        radius=radius,
        claim_duration=claim_duration,
        max_claims_per_user=effective_max_claims_per_user,
        max_total_claims=effective_max_total_claims,
        total_value=total_value,
        starts_at=starts_at,
        ends_at=ends_at,
        use_password=0,
        is_prizedraw=True,
        link=link,
        city=city,
        country=country,
        geohash_precision=geohash_precision,
        place_resolver=place_resolver,
        auto_reverse_geocode=auto_reverse_geocode,
    )
    await db.execute(
        f"""
        INSERT INTO {schema.PRIZEDRAW_TABLE_NAME} (
            {schema.PRIZEDRAW_SPOT_ID},
            {schema.PRIZEDRAW_PRIZE_COUNT}
        )
        VALUES (?, ?);
        """,
        (int(spot_id), int(effective_prize_count)),
    )
    return int(spot_id)


async def modify_draft_prizedraw(
    db,
    *,
    spot_id: int,
    prize_count: int,
) -> None:
    """Update PRIZEDRAW fields while the paired SPOT is still a draft."""
    spot = await _require_draft_spot(db, spot_id=spot_id)
    prizedraw = await get_prizedraw(db, spot_id=spot_id)
    if prizedraw is None:
        raise ValueError("spot is not a Prizedraw")

    prize_count = _validate_prizedraw_prize_count(prize_count)
    max_total_claims = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    max_claims_per_user = int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 0)
    _validate_prizedraw_participant_limits(
        max_claims_per_user=max_claims_per_user,
        max_total_claims=max_total_claims,
        prize_count=prize_count,
    )

    cur = await db.execute(
        f"""
        UPDATE {schema.PRIZEDRAW_TABLE_NAME}
        SET {schema.PRIZEDRAW_PRIZE_COUNT} = ?
        WHERE {schema.PRIZEDRAW_SPOT_ID} = ?;
        """,
        (int(prize_count), int(spot_id)),
    )
    _require_one(cur.rowcount, f"Failed to update Prizedraw spot_id={spot_id}")


async def get_prizedraw(db, *, spot_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.PRIZEDRAW_TABLE_NAME}
        WHERE {schema.PRIZEDRAW_SPOT_ID} = ?;
        """,
        (int(spot_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def is_prizedraw(db, *, spot_id: int) -> bool:
    return await get_prizedraw(db, spot_id=spot_id) is not None


# ---------------------------------------------------------------------------
# CLAIM: writes
# ---------------------------------------------------------------------------

async def create_claim(
    db,
    *,
    spot_id: int,
    user_id: int,
    lat: float,
    long: float,
    accuracy: float,
    payout_address: str | None = None,
) -> int:
    """Create a pending CLAIM and return the claim id."""
    cur = await db.execute(
        f"""
        INSERT INTO {schema.CLAIM_TABLE_NAME} (
            {schema.CLAIM_SPOT_ID},
            {schema.CLAIM_RECIPIENT},
            {schema.CLAIM_PAYOUT_ADDRESS},
            {schema.CLAIM_LAT},
            {schema.CLAIM_LONG},
            {schema.CLAIM_ACCURACY},
            {schema.CLAIM_STATUS}
        )
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (
            int(spot_id),
            int(user_id),
            _clean_optional_nimiq_address(payout_address),
            float(lat),
            float(long),
            float(accuracy),
            const.CLAIM_STATUS_PENDING,
        ),
    )
    return int(cur.lastrowid)


async def modify_claim_accuracy(db, *, claim_id: int, accuracy: float) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_ACCURACY} = ?
        WHERE {schema.CLAIM_ID} = ?;
        """,
        (float(accuracy), int(claim_id)),
    )
    _require_one(cur.rowcount, f"Failed to update claim accuracy id={claim_id}")


async def modify_claim_status(db, *, claim_id: int, status: int) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_STATUS} = ?
        WHERE {schema.CLAIM_ID} = ?;
        """,
        (int(status), int(claim_id)),
    )
    _require_one(cur.rowcount, f"Failed to update claim status id={claim_id}")


async def set_claim_status_to_success(db, *, claim_id: int) -> None:
    await modify_claim_status(db, claim_id=claim_id, status=const.CLAIM_STATUS_SUCCESS)


async def set_claim_status_to_failed(db, *, claim_id: int) -> None:
    await modify_claim_status(db, claim_id=claim_id, status=const.CLAIM_STATUS_FAILED)


async def set_claim_status_to_pending(db, *, claim_id: int) -> None:
    await modify_claim_status(db, claim_id=claim_id, status=const.CLAIM_STATUS_PENDING)


async def promote_pending_claim_to_success_if_capacity_available(db, *, claim_id: int) -> RowDict | None:
    """Promote a pending CLAIM to SUCCESS without overfilling a standard SPOT.

    SQLite serialises writers, so this conditional UPDATE is the final capacity
    gate for standard Spot rewards. It closes the race where two duration claims
    could both observe one remaining reward and both become successful.

    Prizedraw entries use different accounting: SUCCESS means a valid draw entry,
    not a direct payout. Those claims are promoted normally here.
    """
    claim = await get_claim(db, claim_id=int(claim_id))
    if claim is None:
        return None
    if int(claim[schema.CLAIM_STATUS]) != const.CLAIM_STATUS_PENDING:
        return claim

    spot_id = int(claim[schema.CLAIM_SPOT_ID])
    spot = await get_spot(db, spot_id=spot_id)
    if spot is None:
        return claim

    if await is_prizedraw(db, spot_id=spot_id):
        await set_claim_status_to_success(db, claim_id=int(claim_id))
        claim_after = await get_claim(db, claim_id=int(claim_id))
        if claim_after is not None:
            claim_after["capacity_promotion"] = {
                "ok": True,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "prizedraw_entry_promoted",
            }
        return claim_after

    max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if max_total <= 0:
        await set_claim_status_to_success(db, claim_id=int(claim_id))
        claim_after = await get_claim(db, claim_id=int(claim_id))
        if claim_after is not None:
            claim_after["capacity_promotion"] = {
                "ok": True,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "unlimited_standard_spot",
            }
        return claim_after

    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_STATUS} = ?,
            {schema.CLAIM_UPDATED_AT} = unixepoch()
        WHERE {schema.CLAIM_ID} = ?
          AND {schema.CLAIM_STATUS} = ?
          AND (
                SELECT COUNT(*)
                FROM {schema.CLAIM_TABLE_NAME} existing
                WHERE existing.{schema.CLAIM_SPOT_ID} = ?
                  AND existing.{schema.CLAIM_STATUS} = ?
          ) < (
                SELECT s.{schema.SPOT_MAX_TOTAL_CLAIMS}
                FROM {schema.SPOT_TABLE_NAME} s
                WHERE s.{schema.SPOT_ID} = ?
          )
        RETURNING {schema.CLAIM_ID};
        """,
        (
            const.CLAIM_STATUS_SUCCESS,
            int(claim_id),
            const.CLAIM_STATUS_PENDING,
            spot_id,
            const.CLAIM_STATUS_SUCCESS,
            spot_id,
        ),
    )
    row = await cur.fetchone()
    if row is not None:
        claim_after = await get_claim(db, claim_id=int(claim_id))
        cleanup = await fail_pending_standard_duration_claims_if_capacity_full(db, spot_id=spot_id)
        if claim_after is not None:
            claim_after["capacity_promotion"] = {
                "ok": True,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "promoted_with_capacity",
            }
            claim_after["capacity_cleanup"] = cleanup
        return claim_after

    await set_claim_status_to_failed(db, claim_id=int(claim_id))
    claim_after = await get_claim(db, claim_id=int(claim_id))
    if claim_after is not None:
        claim_after["capacity_promotion"] = {
            "ok": False,
            "claim_id": int(claim_id),
            "spot_id": spot_id,
            "reason": "capacity_full_claim_failed",
            "max_total_claims": max_total,
        }
        claim_after["capacity_cleanup"] = {
            "ok": True,
            "spot_id": spot_id,
            "failed_count": 1,
            "reason": "capacity_full_current_claim_failed",
            "failed_claim_ids": [int(claim_id)],
            "failed_user_ids": [int(claim_after[schema.CLAIM_RECIPIENT])],
        }
    return claim_after


async def modify_claim_location_score(
    db,
    *,
    claim_id: int,
    lat: float,
    long: float,
    accuracy_score: float,
    status: int | None = None,
) -> None:
    """Update the latest recorded claim position and duration score."""
    score = max(0.0, min(1.0, float(accuracy_score)))
    if status is None:
        cur = await db.execute(
            f"""
            UPDATE {schema.CLAIM_TABLE_NAME}
            SET {schema.CLAIM_LAT} = ?,
                {schema.CLAIM_LONG} = ?,
                {schema.CLAIM_ACCURACY} = ?,
                {schema.CLAIM_UPDATED_AT} = unixepoch()
            WHERE {schema.CLAIM_ID} = ?;
            """,
            (float(lat), float(long), score, int(claim_id)),
        )
    else:
        cur = await db.execute(
            f"""
            UPDATE {schema.CLAIM_TABLE_NAME}
            SET {schema.CLAIM_LAT} = ?,
                {schema.CLAIM_LONG} = ?,
                {schema.CLAIM_ACCURACY} = ?,
                {schema.CLAIM_STATUS} = ?,
                {schema.CLAIM_UPDATED_AT} = unixepoch()
            WHERE {schema.CLAIM_ID} = ?;
            """,
            (float(lat), float(long), score, int(status), int(claim_id)),
        )
    _require_one(cur.rowcount, f"Failed to update claim location id={claim_id}")


# ---------------------------------------------------------------------------
# CLAIM: getters / checks / metrics
# ---------------------------------------------------------------------------

async def get_claim(db, *, claim_id: int) -> RowDict | None:
    """Return a CLAIM detail row with spot and claim-code context."""
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.CLAIM_VIEW_DETAIL}
        WHERE {schema.CLAIM_ID} = ?;
        """,
        (int(claim_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_claims(
    db,
    *,
    spot_id: int,
    include_failed: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return pending/successful CLAIMs for a SPOT by default."""
    statuses = [const.CLAIM_STATUS_PENDING, const.CLAIM_STATUS_SUCCESS]
    if include_failed:
        statuses.append(const.CLAIM_STATUS_FAILED)

    placeholders = _sql_placeholders(len(statuses))
    params: list[Any] = [int(spot_id), *statuses, _clamp_limit(limit), _normalise_offset(offset)]
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.CLAIM_VIEW_DETAIL}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} IN ({placeholders})
        ORDER BY {schema.CLAIM_CLAIMED_AT} DESC, {schema.CLAIM_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        tuple(params),
    )
    return _rows_to_dicts(rows)


async def get_claims_by_user(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return a user's claim history for the My History page."""
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.CLAIM_VIEW_DETAIL}
        WHERE {schema.CLAIM_RECIPIENT} = ?
        ORDER BY {schema.CLAIM_CLAIMED_AT} DESC, {schema.CLAIM_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        (int(user_id), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def count_claims_by_status_for_spot(db, *, spot_id: int, status: int) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.CLAIM_TABLE_NAME}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} = ?;
        """,
        (int(spot_id), int(status)),
    )
    row = await cur.fetchone()
    return int(row["n"])


async def get_claims_by_status_for_spot(
    db,
    *,
    spot_id: int,
    status: int,
    limit: int = MAX_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    """Return CLAIM detail rows for one SPOT with one exact status."""
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.CLAIM_VIEW_DETAIL}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} = ?
        ORDER BY {schema.CLAIM_CLAIMED_AT} ASC, {schema.CLAIM_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (int(spot_id), int(status), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_successful_claims_for_spot(db, *, spot_id: int, limit: int = MAX_LIMIT) -> list[RowDict]:
    return await get_claims_by_status_for_spot(
        db,
        spot_id=spot_id,
        status=const.CLAIM_STATUS_SUCCESS,
        limit=limit,
    )


async def get_pending_claims_for_spot(db, *, spot_id: int, limit: int = MAX_LIMIT) -> list[RowDict]:
    return await get_claims_by_status_for_spot(
        db,
        spot_id=spot_id,
        status=const.CLAIM_STATUS_PENDING,
        limit=limit,
    )


async def get_pending_duration_claim_ids(db, *, limit: int = MAX_LIMIT, offset: int = 0) -> list[int]:
    """Return pending duration-based claim ids for background settlement."""
    rows = await db.execute_fetchall(
        f"""
        SELECT c.{schema.CLAIM_ID} AS claim_id
        FROM {schema.CLAIM_TABLE_NAME} c
        JOIN {schema.SPOT_TABLE_NAME} s
            ON s.{schema.SPOT_ID} = c.{schema.CLAIM_SPOT_ID}
        WHERE c.{schema.CLAIM_STATUS} = ?
          AND s.{schema.SPOT_STATUS} = {const.SPOT_STATUS_PUBLISHED}
          AND s.{schema.SPOT_CLAIM_DURATION} > 0
        ORDER BY c.{schema.CLAIM_UPDATED_AT} ASC, c.{schema.CLAIM_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (const.CLAIM_STATUS_PENDING, _clamp_limit(limit), _normalise_offset(offset)),
    )
    return [int(row["claim_id"]) for row in rows]


async def fail_pending_claims_for_spot(db, *, spot_id: int) -> int:
    """Fail every still-pending claim for a SPOT and return the count.

    Settlement uses this when a Prizedraw closes. Pending duration entries that
    have not become SUCCESS by then are not eligible for the draw.
    """
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_STATUS} = ?,
            {schema.CLAIM_UPDATED_AT} = unixepoch()
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} = ?;
        """,
        (const.CLAIM_STATUS_FAILED, int(spot_id), const.CLAIM_STATUS_PENDING),
    )
    return int(cur.rowcount or 0)


async def fail_pending_standard_duration_claims_if_capacity_full(db, *, spot_id: int) -> RowDict:
    """Fail pending duration claims when a standard SPOT has no rewards left.

    Standard duration claims do not reserve global capacity when they start.
    That lets several people begin the waiting period while a reward is still
    available. The safety rule is: as soon as successful claims reach
    max_total_claims, every other still-pending duration claim for the same
    standard SPOT is failed. This prevents later background refreshes from
    turning too many pending claims into successful, payable claims.

    Prizedraws deliberately use different accounting and are skipped here.
    """
    spot = await get_spot(db, spot_id=int(spot_id))
    if spot is None:
        return {
            "ok": False,
            "spot_id": int(spot_id),
            "failed_count": 0,
            "reason": "spot_missing",
            "failed_claim_ids": [],
            "failed_user_ids": [],
        }

    if await is_prizedraw(db, spot_id=int(spot_id)):
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "failed_count": 0,
            "reason": "prizedraw_skipped",
            "failed_claim_ids": [],
            "failed_user_ids": [],
        }

    duration = int(spot.get(schema.SPOT_CLAIM_DURATION) or 0)
    max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if duration <= 0 or max_total <= 0:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "failed_count": 0,
            "reason": "not_limited_duration_spot",
            "failed_claim_ids": [],
            "failed_user_ids": [],
        }

    success_count = await count_claims_by_status_for_spot(
        db,
        spot_id=int(spot_id),
        status=const.CLAIM_STATUS_SUCCESS,
    )
    if int(success_count) < int(max_total):
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "failed_count": 0,
            "reason": "capacity_available",
            "success_count": int(success_count),
            "max_total_claims": int(max_total),
            "failed_claim_ids": [],
            "failed_user_ids": [],
        }

    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.CLAIM_ID} AS claim_id,
            {schema.CLAIM_RECIPIENT} AS user_id
        FROM {schema.CLAIM_TABLE_NAME}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} = ?
        ORDER BY {schema.CLAIM_CLAIMED_AT} ASC, {schema.CLAIM_ID} ASC;
        """,
        (int(spot_id), const.CLAIM_STATUS_PENDING),
    )

    failed_claim_ids = [int(row["claim_id"]) for row in rows]
    failed_user_ids = sorted({int(row["user_id"]) for row in rows})
    if not failed_claim_ids:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "failed_count": 0,
            "reason": "capacity_full_no_pending_claims",
            "success_count": int(success_count),
            "max_total_claims": int(max_total),
            "failed_claim_ids": [],
            "failed_user_ids": [],
        }

    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_STATUS} = ?,
            {schema.CLAIM_UPDATED_AT} = unixepoch()
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} = ?;
        """,
        (const.CLAIM_STATUS_FAILED, int(spot_id), const.CLAIM_STATUS_PENDING),
    )

    return {
        "ok": True,
        "spot_id": int(spot_id),
        "failed_count": int(cur.rowcount or 0),
        "reason": "capacity_full_pending_claims_failed",
        "success_count": int(success_count),
        "max_total_claims": int(max_total),
        "failed_claim_ids": failed_claim_ids,
        "failed_user_ids": failed_user_ids,
    }


async def has_nonfailed_claim_payout_transaction(db, *, claim_id: int) -> bool:
    """Return True if a claim already has a non-failed payout transaction."""
    cur = await db.execute(
        f"""
        SELECT 1
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_CLAIM_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} != ?
        LIMIT 1;
        """,
        (int(claim_id), const.TRANS_TYPE_CLAIM, const.TRANS_STATUS_FAILED),
    )
    return await cur.fetchone() is not None


async def has_spot_cancellation_started(db, *, spot_id: int) -> bool:
    """Return True once a standard Spot cancellation has been durably started."""
    cur = await db.execute(
        f"""
        SELECT 1
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_ID} = ?
          AND {schema.SPOT_CANCELLATION_STARTED_AT} IS NOT NULL
        UNION
        SELECT 1
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} IN (?, ?)
        LIMIT 1;
        """,
        (
            int(spot_id),
            int(spot_id),
            const.TRANS_TYPE_CANCEL_SPOT,
            const.TRANS_TYPE_PLAT_FEE,
        ),
    )
    return await cur.fetchone() is not None


async def has_confirmed_claim_payout_transaction(db, *, claim_id: int) -> bool:
    """Return True if a CLAIM payout transaction is confirmed on-chain."""
    cur = await db.execute(
        f"""
        SELECT 1
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_CLAIM_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?
        LIMIT 1;
        """,
        (int(claim_id), const.TRANS_TYPE_CLAIM, const.TRANS_STATUS_CONFIRMED),
    )
    return await cur.fetchone() is not None


async def latest_failed_claim_payout_amount(db, *, claim_id: int) -> int | None:
    """Return the most recent failed payout amount for retrying a winner."""
    cur = await db.execute(
        f"""
        SELECT {schema.TRANS_AMOUNT} AS amount
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_CLAIM_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?
        ORDER BY {schema.TRANS_CREATED_AT} DESC, {schema.TRANS_ID} DESC
        LIMIT 1;
        """,
        (int(claim_id), const.TRANS_TYPE_CLAIM, const.TRANS_STATUS_FAILED),
    )
    row = await cur.fetchone()
    return None if row is None else int(row["amount"] or 0)


async def mark_prizedraw_winners_pending(db, *, spot_id: int, winner_claim_ids: list[int]) -> RowDict:
    """Mark selected Prizedraw winners as pending until payout confirmation.

    At draw close, non-winning successful entries stay SUCCESS, which means the
    user entered validly but did not win. Chosen winners move back to PENDING so
    the transaction updater can mark them SUCCESS only after their reward
    transaction is confirmed.
    """
    clean_ids = sorted({int(claim_id) for claim_id in winner_claim_ids if int(claim_id) > 0})
    if not clean_ids:
        return {"ok": True, "spot_id": int(spot_id), "winner_claim_ids": [], "updated_count": 0}

    placeholders = _sql_placeholders(len(clean_ids))
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_STATUS} = ?,
            {schema.CLAIM_UPDATED_AT} = unixepoch()
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_STATUS} = ?
          AND {schema.CLAIM_ID} IN ({placeholders});
        """,
        (const.CLAIM_STATUS_PENDING, int(spot_id), const.CLAIM_STATUS_SUCCESS, *clean_ids),
    )
    return {
        "ok": True,
        "spot_id": int(spot_id),
        "winner_claim_ids": clean_ids,
        "updated_count": int(cur.rowcount or 0),
    }


async def get_prizedraw_winner_claim_ids(db, *, spot_id: int) -> list[int]:
    """Return the persisted winner set for a completed Prizedraw.

    Winners are represented by either a pending claim awaiting payout, or by any
    CLAIM transaction row already created for that claim. The union gives a
    stable set across retry passes, including after some winner payouts have
    already confirmed.
    """
    rows = await db.execute_fetchall(
        f"""
        SELECT claim_id
        FROM (
            SELECT c.{schema.CLAIM_ID} AS claim_id
            FROM {schema.CLAIM_TABLE_NAME} c
            WHERE c.{schema.CLAIM_SPOT_ID} = ?
              AND c.{schema.CLAIM_STATUS} = ?

            UNION

            SELECT t.{schema.TRANS_CLAIM_ID} AS claim_id
            FROM {schema.TRANS_TABLE_NAME} t
            WHERE t.{schema.TRANS_SPOT_ID} = ?
              AND t.{schema.TRANS_TYPE} = ?
              AND t.{schema.TRANS_CLAIM_ID} IS NOT NULL
        )
        ORDER BY claim_id ASC;
        """,
        (int(spot_id), const.CLAIM_STATUS_PENDING, int(spot_id), const.TRANS_TYPE_CLAIM),
    )
    return [int(row["claim_id"]) for row in rows]


async def get_unpaid_successful_standard_claim_ids(
    db,
    *,
    spot_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[int]:
    """Return successful standard claims without an active/confirmed payout.

    Failed payout rows remain retryable and therefore do not exclude a claim.
    A pending local intent or confirmed payout does exclude it, preserving the
    database-backed idempotency guarantee across worker processes.
    """
    params: list[Any] = [const.CLAIM_STATUS_SUCCESS, const.TRANS_TYPE_CLAIM, const.TRANS_STATUS_FAILED]
    spot_filter = ""
    if spot_id is not None:
        spot_filter = f"AND c.{schema.CLAIM_SPOT_ID} = ?"
        params.append(int(spot_id))
    params.extend([_clamp_limit(limit), _normalise_offset(offset)])

    rows = await db.execute_fetchall(
        f"""
        SELECT c.{schema.CLAIM_ID} AS claim_id
        FROM {schema.CLAIM_TABLE_NAME} c
        LEFT JOIN {schema.PRIZEDRAW_TABLE_NAME} pd
            ON pd.{schema.PRIZEDRAW_SPOT_ID} = c.{schema.CLAIM_SPOT_ID}
        WHERE c.{schema.CLAIM_STATUS} = ?
          AND pd.{schema.PRIZEDRAW_SPOT_ID} IS NULL
          AND NOT EXISTS (
                SELECT 1
                FROM {schema.TRANS_TABLE_NAME} t
                WHERE t.{schema.TRANS_CLAIM_ID} = c.{schema.CLAIM_ID}
                  AND t.{schema.TRANS_TYPE} = ?
                  AND t.{schema.TRANS_STATUS} != ?
          )
          {spot_filter}
        ORDER BY c.{schema.CLAIM_CLAIMED_AT} ASC, c.{schema.CLAIM_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        tuple(params),
    )
    return [int(row["claim_id"]) for row in rows]


async def get_completed_prizedraw_spot_ids_with_pending_winners(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[int]:
    """Return completed Prizedraws that still have winners awaiting payout."""
    rows = await db.execute_fetchall(
        f"""
        SELECT DISTINCT s.{schema.SPOT_ID} AS spot_id
        FROM {schema.SPOT_TABLE_NAME} s
        JOIN {schema.PRIZEDRAW_TABLE_NAME} pd
            ON pd.{schema.PRIZEDRAW_SPOT_ID} = s.{schema.SPOT_ID}
        JOIN {schema.CLAIM_TABLE_NAME} c
            ON c.{schema.CLAIM_SPOT_ID} = s.{schema.SPOT_ID}
        WHERE s.{schema.SPOT_STATUS} = ?
          AND c.{schema.CLAIM_STATUS} = ?
        ORDER BY s.{schema.SPOT_UPDATED_AT} ASC, s.{schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (
            const.SPOT_STATUS_COMPLETED,
            const.CLAIM_STATUS_PENDING,
            _clamp_limit(limit),
            _normalise_offset(offset),
        ),
    )
    return [int(row["spot_id"]) for row in rows]


async def count_successful_claims_for_user_spot(
    db,
    *,
    spot_id: int,
    user_id: int,
) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.CLAIM_TABLE_NAME}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_RECIPIENT} = ?
          AND {schema.CLAIM_STATUS} = ?;
        """,
        (int(spot_id), int(user_id), const.CLAIM_STATUS_SUCCESS),
    )
    row = await cur.fetchone()
    return int(row["n"])


async def count_active_claims_for_user_spot(
    db,
    *,
    spot_id: int,
    user_id: int,
) -> int:
    """Return claims/entries that count against this user's limit.

    Standard spots count only successful claims. Pending duration claims do not
    reserve the user's future capacity; when a standard duration Spot reaches
    global capacity, remaining pending claims are failed automatically. For
    Prizedraws, a pending entry is the user's actual entry, so it counts
    immediately.
    """
    statuses = [const.CLAIM_STATUS_SUCCESS]
    if await is_prizedraw(db, spot_id=spot_id):
        statuses.append(const.CLAIM_STATUS_PENDING)

    placeholders = _sql_placeholders(len(statuses))
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.CLAIM_TABLE_NAME}
        WHERE {schema.CLAIM_SPOT_ID} = ?
          AND {schema.CLAIM_RECIPIENT} = ?
          AND {schema.CLAIM_STATUS} IN ({placeholders});
        """,
        (int(spot_id), int(user_id), *statuses),
    )
    row = await cur.fetchone()
    return int(row["n"] or 0)


async def has_user_reached_claim_limit(db, *, spot_id: int, user_id: int) -> bool:
    spot = await get_spot(db, spot_id=spot_id)
    if not spot:
        return True

    max_per_user = int(spot[schema.SPOT_MAX_CLAIMS_PER_USER])
    if max_per_user <= 0:
        return False

    active_count = await count_active_claims_for_user_spot(
        db,
        spot_id=spot_id,
        user_id=user_id,
    )
    return active_count >= max_per_user


async def get_claim_distance_check(
    db,
    *,
    spot_id: int,
    lat: float,
    long: float,
    location_accuracy_metres: float | None = None,
) -> RowDict | None:
    """Return distance details for a possible claim or duration heartbeat.

    `effective_distance_metres` gives the GPS reading a capped mercy margin:
    distance from spot centre minus min(reported GPS accuracy, configured cap).
    """
    spot = await get_spot(db, spot_id=spot_id)
    if not spot:
        return None

    distance = distance_metres(
        float(lat),
        float(long),
        float(spot[schema.SPOT_LAT]),
        float(spot[schema.SPOT_LONG]),
    )
    radius = int(spot[schema.SPOT_RADIUS])
    accuracy_margin = _location_accuracy_margin_metres(location_accuracy_metres)
    effective_distance = max(0.0, float(distance) - accuracy_margin)
    outside_by = max(0.0, effective_distance - float(radius))
    return {
        "spot_id": int(spot_id),
        "distance_metres": distance,
        "radius_metres": radius,
        "location_accuracy_metres": None if location_accuracy_metres is None else float(location_accuracy_metres),
        "accuracy_margin_metres": accuracy_margin,
        "effective_distance_metres": effective_distance,
        "outside_by_metres": outside_by,
        "within_radius": effective_distance <= radius,
    }


async def get_claim_rule_check(
    db,
    *,
    spot_id: int,
    user_id: int,
    lat: float | None,
    long: float | None,
    location_accuracy_metres: float | None = None,
) -> RowDict:
    """Return a compact claim outcome check without writing anything.

    Location is optional so Find Spots can still report permanent blockers such
    as ownership, exhausted capacity, or the user's claim limit. Those blockers
    deliberately take precedence over the temporary absence of a GPS reading.
    """
    user_ok = await can_user_claim(db, user_id=user_id)
    public = await get_public_spot(db, spot_id=spot_id)
    spot = await get_spot(db, spot_id=spot_id)
    own_spot = bool(spot and int(spot[schema.SPOT_CREATED_BY]) == int(user_id))
    location_known = lat is not None and long is not None
    distance_check = None
    if location_known:
        distance_check = await get_claim_distance_check(
            db,
            spot_id=spot_id,
            lat=float(lat),
            long=float(long),
            location_accuracy_metres=location_accuracy_metres,
        )
    capacity_ok = await is_spot_claim_capacity_available(db, spot_id=spot_id)
    user_limit_ok = not await has_user_reached_claim_limit(db, spot_id=spot_id, user_id=user_id)
    cancellation_pending = await has_spot_cancellation_started(db, spot_id=spot_id)

    spot_current = bool(public and int(public.get("availability_rank", 1)) == 0)
    within_radius = bool(distance_check and distance_check["within_radius"])

    reason = None
    message = None
    if not user_ok:
        reason = "user_not_allowed"
        message = "This device account cannot claim spots."
    elif own_spot:
        reason = "own_spot"
        message = "You cannot claim your own spot."
    elif cancellation_pending:
        reason = "cancellation_pending"
        message = "This spot is being cancelled and can no longer be claimed."
    elif not spot_current:
        reason = "not_active"
        message = "This spot is not active right now."
    elif not capacity_ok:
        reason = "capacity_full"
        message = "This spot has no remaining claim capacity."
    elif not user_limit_ok:
        reason = "user_limit_reached"
        message = "You have already reached your claim limit for this spot."
    elif not location_known:
        reason = "location_unknown"
        message = "Your location is unknown."
    elif not within_radius:
        reason = "outside_radius"
        message = "Move inside the spot radius to claim."

    allowed = bool(
        user_ok
        and not own_spot
        and not cancellation_pending
        and spot_current
        and capacity_ok
        and user_limit_ok
        and location_known
        and within_radius
    )

    return {
        "allowed": allowed,
        "reason": reason,
        "message": message,
        "user_ok": user_ok,
        "own_spot": own_spot,
        "spot_current": spot_current,
        "cancellation_pending": cancellation_pending,
        "location_known": location_known,
        "within_radius": within_radius,
        "capacity_ok": capacity_ok,
        "user_limit_ok": user_limit_ok,
        "distance": distance_check,
    }




def _normalise_location_accuracy_score(*, reading_accuracy_metres: float | None, radius_metres: int) -> float:
    """Convert a location accuracy reading in metres into CLAIM.accuracy 0..1."""
    if reading_accuracy_metres is None:
        return 1.0
    try:
        reading = max(0.0, float(reading_accuracy_metres))
    except (TypeError, ValueError):
        return 1.0
    radius = max(1.0, float(radius_metres or 1))
    # Excellent readings score near 1; readings as vague as the whole radius score 0.
    return max(0.0, min(1.0, 1.0 - (reading / radius)))


async def create_claim_attempt(
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
    """Create a CLAIM/entry after checking all immediate claim rules.

    Immediate standard claims are marked successful at once. Duration claims and
    Prizedraw entries begin as pending because they require later completion or
    draw settlement. Password claim codes are consumed atomically with the CLAIM.
    """
    spot = await get_spot(db, spot_id=spot_id)
    if spot is None:
        raise ValueError("This spot could not be found.")

    rule = await get_claim_rule_check(
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
    clean_code = _normalise_claim_code(claim_code, required=use_password)
    if use_password:
        existing_code = await get_claim_code_by_code(db, spot_id=spot_id, claim_code=clean_code)
        if existing_code is None:
            raise ValueError("That claim code is not valid for this spot.")
        if existing_code.get(schema.CLAIM_CODE_USED_BY) is not None:
            raise ValueError("This code has already been used.")

    claim_duration = int(spot.get(schema.SPOT_CLAIM_DURATION) or 0)
    # CLAIM.accuracy now tracks the duration-claim health budget.
    # It starts full; later heartbeats deduct from it only when the user is
    # outside the Spot after the capped GPS mercy margin is applied.
    accuracy_score = 1.0

    # Recheck immediately before writing for a friendly error. The SQLite
    # trigger is the authoritative race-safe guard if cancellation begins after
    # this check but before the INSERT obtains the write lock.
    if await has_spot_cancellation_started(db, spot_id=spot_id):
        raise ValueError("This spot is being cancelled and can no longer be claimed.")

    try:
        claim_id = await create_claim(
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
            raise ValueError("This spot is being cancelled and can no longer be claimed.") from exc
        raise

    if use_password and clean_code:
        await claim_code_for_claim(
            db,
            spot_id=spot_id,
            claim_code=clean_code,
            claim_id=claim_id,
        )

    # A zero-duration claim/entry succeeds immediately.
    # For Prizedraws, SUCCESS means the user has successfully entered the draw;
    # winning is later inferred from a related CLAIM payout transaction.
    if claim_duration <= 0:
        claim_after = await promote_pending_claim_to_success_if_capacity_available(db, claim_id=claim_id)
        promotion = claim_after.get("capacity_promotion") if isinstance(claim_after, dict) else None
        if isinstance(promotion, dict) and promotion.get("ok") is False:
            raise ValueError("This spot has run out of rewards.")
        if claim_after is not None:
            return claim_after

    claim = await get_claim(db, claim_id=claim_id)
    if claim is None:
        raise RuntimeError("Claim was created but could not be reloaded")
    return claim


async def refresh_claim_status_from_conditions(db, *, claim_id: int) -> RowDict | None:
    """Resolve a pending claim when duration/staleness rules say it is done.

    Duration claims only succeed if the user kept this claim alive recently
    enough. If CLAIM.updated_at is older than the configured stale window, the
    claim fails instead of being allowed to complete silently in the background.
    """
    claim = await get_claim(db, claim_id=claim_id)
    if claim is None:
        return None
    if int(claim[schema.CLAIM_STATUS]) != const.CLAIM_STATUS_PENDING:
        return claim

    spot = await get_spot(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
    if spot is None:
        return claim

    # Once a Prizedraw is completed, any remaining PENDING claims are selected
    # winners awaiting a confirmed payout transaction. They must not be treated
    # as ordinary duration claims and auto-completed by the timer.
    if int(spot.get(schema.SPOT_STATUS) or -1) == const.SPOT_STATUS_COMPLETED and await is_prizedraw(
        db,
        spot_id=int(claim[schema.CLAIM_SPOT_ID]),
    ):
        return claim

    duration = int(spot.get(schema.SPOT_CLAIM_DURATION) or 0)
    if duration <= 0:
        return await promote_pending_claim_to_success_if_capacity_available(db, claim_id=claim_id)

    now = await get_unixepoch(db)
    stale_after = max(1, int(getattr(const, "CLAIM_LOCATION_STALE_AFTER_SECONDS", 180)))
    last_update = int(claim.get(schema.CLAIM_UPDATED_AT) or claim.get(schema.CLAIM_CLAIMED_AT) or now)

    if now - last_update > stale_after:
        await set_claim_status_to_failed(db, claim_id=claim_id)
        return await get_claim(db, claim_id=claim_id)

    if now >= int(claim[schema.CLAIM_CLAIMED_AT]) + duration:
        return await promote_pending_claim_to_success_if_capacity_available(db, claim_id=claim_id)

    return claim


async def process_duration_claim_location_heartbeat(
    db,
    *,
    claim_id: int,
    user_id: int,
    lat: float,
    long: float,
    location_accuracy_metres: float | None = None,
) -> RowDict:
    """Process one client location heartbeat for a pending duration claim.

    The user must be the claim recipient. The location is recorded on the CLAIM
    row so the Claim Detail page can show the latest server-seen position.
    """
    claim = await get_claim(db, claim_id=claim_id)
    if claim is None:
        raise ValueError("Claim not found.")
    if int(claim[schema.CLAIM_RECIPIENT]) != int(user_id):
        raise PermissionError("This claim does not belong to this device account.")

    refreshed = await refresh_claim_status_from_conditions(db, claim_id=claim_id)
    if refreshed is None:
        raise ValueError("Claim not found.")
    if int(refreshed[schema.CLAIM_STATUS]) != const.CLAIM_STATUS_PENDING:
        return refreshed

    spot = await get_spot(db, spot_id=int(refreshed[schema.CLAIM_SPOT_ID]))
    if spot is None:
        return refreshed

    duration = int(spot.get(schema.SPOT_CLAIM_DURATION) or 0)
    if duration <= 0:
        claim_after = await promote_pending_claim_to_success_if_capacity_available(db, claim_id=claim_id)
        if claim_after is None:
            raise RuntimeError("Claim disappeared during heartbeat")
        return claim_after

    lat, long = _validate_optional_coordinates(lat, long)
    if lat is None or long is None:
        raise ValueError("A fresh location is required.")

    distance_check = await get_claim_distance_check(
        db,
        spot_id=int(refreshed[schema.CLAIM_SPOT_ID]),
        lat=float(lat),
        long=float(long),
        location_accuracy_metres=location_accuracy_metres,
    )
    if distance_check is None:
        return refreshed

    penalty = _duration_claim_penalty(
        effective_distance=float(distance_check["effective_distance_metres"]),
        radius_metres=int(distance_check["radius_metres"]),
    )
    current_score = max(0.0, min(1.0, float(refreshed.get(schema.CLAIM_ACCURACY) or 0.0)))
    next_score = max(0.0, current_score - float(penalty))

    now = await get_unixepoch(db)
    should_promote = False
    next_status = const.CLAIM_STATUS_PENDING
    if next_score <= 0:
        next_status = const.CLAIM_STATUS_FAILED
    elif now >= int(refreshed[schema.CLAIM_CLAIMED_AT]) + duration:
        should_promote = True

    await modify_claim_location_score(
        db,
        claim_id=claim_id,
        lat=float(lat),
        long=float(long),
        accuracy_score=next_score,
        status=next_status,
    )

    if should_promote:
        claim_after = await promote_pending_claim_to_success_if_capacity_available(db, claim_id=claim_id)
    else:
        claim_after = await get_claim(db, claim_id=claim_id)
    if claim_after is None:
        raise RuntimeError("Claim disappeared during heartbeat")

    claim_after["distance"] = distance_check
    claim_after["location_penalty"] = penalty
    return claim_after


# ---------------------------------------------------------------------------
# CLAIM_CODE: writes / getters
# ---------------------------------------------------------------------------

async def create_claim_code(db, *, spot_id: int, claim_code: str) -> int:
    cur = await db.execute(
        f"""
        INSERT INTO {schema.CLAIM_CODE_TABLE_NAME} (
            {schema.CLAIM_CODE_SPOT_ID},
            {schema.CLAIM_CODE_CODE},
            {schema.CLAIM_CODE_USED_BY}
        )
        VALUES (?, ?, NULL);
        """,
        (int(spot_id), _normalise_claim_code(claim_code, required=True)),
    )
    return int(cur.lastrowid)


async def create_claim_codes(db, *, spot_id: int, claim_codes: list[str]) -> list[int]:
    """Create several claim codes and return their ids.

    Kept deliberately simple. If you need very large inserts later, switch to
    executemany plus a follow-up SELECT.
    """
    ids: list[int] = []
    for code in claim_codes:
        ids.append(await create_claim_code(db, spot_id=spot_id, claim_code=code))
    return ids


async def modify_claim_code_used_by(
    db,
    *,
    claim_code_id: int,
    claim_id: int | None,
) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_CODE_TABLE_NAME}
        SET {schema.CLAIM_CODE_USED_BY} = ?
        WHERE {schema.CLAIM_CODE_ID} = ?;
        """,
        (claim_id, int(claim_code_id)),
    )
    _require_one(cur.rowcount, f"Failed to update claim_code used_by id={claim_code_id}")


async def claim_code_for_claim(
    db,
    *,
    spot_id: int,
    claim_code: str,
    claim_id: int,
) -> int:
    """Mark a specific unused code as used by a claim and return its id.

    The table triggers guarantee the claim belongs to the same SPOT.
    """
    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_CODE_TABLE_NAME}
        SET {schema.CLAIM_CODE_USED_BY} = ?
        WHERE {schema.CLAIM_CODE_SPOT_ID} = ?
          AND {schema.CLAIM_CODE_CODE} = ?
          AND {schema.CLAIM_CODE_USED_BY} IS NULL
        RETURNING {schema.CLAIM_CODE_ID};
        """,
        (int(claim_id), int(spot_id), _normalise_claim_code(claim_code, required=True)),
    )
    row = await cur.fetchone()
    if row is None:
        existing = await get_claim_code_by_code(
            db,
            spot_id=int(spot_id),
            claim_code=claim_code,
        )
        if existing is not None and existing.get(schema.CLAIM_CODE_USED_BY) is not None:
            raise ValueError("This code has already been used.")
        raise ValueError("That claim code is not valid for this spot.")
    return int(row[schema.CLAIM_CODE_ID])


async def get_claim_code(db, *, claim_code_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.CLAIM_CODE_VIEW_DETAIL}
        WHERE {schema.CLAIM_CODE_ID} = ?;
        """,
        (int(claim_code_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_claim_code_by_code(
    db,
    *,
    spot_id: int,
    claim_code: str,
) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.CLAIM_CODE_VIEW_DETAIL}
        WHERE {schema.CLAIM_CODE_SPOT_ID} = ?
          AND {schema.CLAIM_CODE_CODE} = ?;
        """,
        (int(spot_id), _normalise_claim_code(claim_code, required=True)),
    )
    return _row_to_dict(await cur.fetchone())


async def get_claim_codes(db, *, spot_id: int) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.CLAIM_CODE_VIEW_DETAIL}
        WHERE {schema.CLAIM_CODE_SPOT_ID} = ?
        ORDER BY {schema.CLAIM_CODE_ID} ASC;
        """,
        (int(spot_id),),
    )
    return _rows_to_dicts(rows)


async def get_available_claim_codes(db, *, spot_id: int) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.CLAIM_CODE_VIEW_DETAIL}
        WHERE {schema.CLAIM_CODE_SPOT_ID} = ?
          AND {schema.CLAIM_CODE_USED_BY} IS NULL
        ORDER BY {schema.CLAIM_CODE_ID} ASC;
        """,
        (int(spot_id),),
    )
    return _rows_to_dicts(rows)


async def count_available_claim_codes(db, *, spot_id: int) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.CLAIM_CODE_TABLE_NAME}
        WHERE {schema.CLAIM_CODE_SPOT_ID} = ?
          AND {schema.CLAIM_CODE_USED_BY} IS NULL;
        """,
        (int(spot_id),),
    )
    row = await cur.fetchone()
    return int(row["n"])


def _make_placeholder_claim_code() -> str:
    return "".join(secrets.choice(_CLAIM_CODE_ALPHABET) for _ in range(_CLAIM_CODE_LENGTH))


async def _generate_unique_claim_codes_for_spot(db, *, spot_id: int, count: int) -> list[str]:
    codes: list[str] = []
    seen = set()
    while len(codes) < int(count):
        code = _make_placeholder_claim_code()
        if code in seen:
            continue
        seen.add(code)
        if await get_claim_code_by_code(db, spot_id=spot_id, claim_code=code) is None:
            codes.append(code)
    return codes


async def delete_unused_claim_codes_for_spot(db, *, spot_id: int) -> None:
    """Delete unused CLAIM_CODE rows for one SPOT.

    This is mainly a cleanup helper for old development drafts that may already
    have codes from the previous draft-time generation behaviour. Used/claimed
    codes are never removed by this helper.
    """
    await db.execute(
        f"""
        DELETE FROM {schema.CLAIM_CODE_TABLE_NAME}
        WHERE {schema.CLAIM_CODE_SPOT_ID} = ?
          AND {schema.CLAIM_CODE_USED_BY} IS NULL;
        """,
        (int(spot_id),),
    )


async def ensure_claim_codes_for_publish(db, *, spot_id: int) -> None:
    """Create/trim claim codes immediately before publishing a password spot.

    Drafts only store SPOT.use_password and SPOT.max_total_claims. The actual
    CLAIM_CODE rows are generated here so unpaid or abandoned drafts do not
    create hundreds of throwaway rows. Call this inside the same transaction as
    publish_spot().
    """
    spot = await get_spot(db, spot_id=spot_id)
    if not spot:
        raise ValueError(f"spot id={spot_id} does not exist")

    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
    if not use_password:
        return

    if await is_prizedraw(db, spot_id=spot_id):
        raise ValueError("Prizedraw spots do not use claim codes")

    target = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if target <= 0:
        raise ValueError("use_password requires a finite total participant count")

    rows = await get_claim_codes(db, spot_id=spot_id)
    unused = [row for row in rows if row.get(schema.CLAIM_CODE_USED_BY) is None]
    used_count = len(rows) - len(unused)
    if used_count > target:
        raise ValueError("existing used claim codes exceed total participant count")

    if len(rows) > target:
        delete_count = len(rows) - target
        delete_ids = [int(row[schema.CLAIM_CODE_ID]) for row in reversed(unused[:delete_count])]
        if delete_ids:
            placeholders = _sql_placeholders(len(delete_ids))
            await db.execute(
                f"""
                DELETE FROM {schema.CLAIM_CODE_TABLE_NAME}
                WHERE {schema.CLAIM_CODE_ID} IN ({placeholders})
                  AND {schema.CLAIM_CODE_USED_BY} IS NULL;
                """,
                tuple(delete_ids),
            )

    if len(rows) < target:
        codes = await _generate_unique_claim_codes_for_spot(db, spot_id=spot_id, count=target - len(rows))
        await create_claim_codes(db, spot_id=spot_id, claim_codes=codes)


async def sync_draft_claim_codes_for_password(db, *, spot_id: int) -> None:
    """Backward-compatible cleanup wrapper for old draft-time code flow.

    New draft edits do not create claim codes. Codes are created by
    ensure_claim_codes_for_publish() immediately before the SPOT is published.
    This wrapper only removes unused legacy codes when claim codes are disabled.
    """
    spot = await get_spot(db, spot_id=spot_id)
    if not spot or int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        return

    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
    if not use_password:
        await delete_unused_claim_codes_for_spot(db, spot_id=spot_id)


# ---------------------------------------------------------------------------
# TRANSACTION: writes
# ---------------------------------------------------------------------------

async def _create_transaction(
    db,
    *,
    user_id: int,
    trans_type: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
    spot_id: int | None = None,
    claim_id: int | None = None,
) -> int:
    cur = await db.execute(
        f"""
        INSERT INTO {schema.TRANS_TABLE_NAME} (
            {schema.TRANS_USER_ID},
            {schema.TRANS_SPOT_ID},
            {schema.TRANS_CLAIM_ID},
            {schema.TRANS_TYPE},
            {schema.TRANS_AMOUNT},
            {schema.TRANS_FROM_ADDRESS},
            {schema.TRANS_TO_ADDRESS},
            {schema.TRANS_TX_HASH},
            {schema.TRANS_BLOCK_NUMBER},
            {schema.TRANS_STATUS},
            {schema.TRANS_COMPLETED_AT}
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL);
        """,
        (
            int(user_id),
            spot_id,
            claim_id,
            int(trans_type),
            int(amount),
            str(from_address),
            str(to_address),
            str(tx_hash),
            const.TRANS_STATUS_PENDING,
        ),
    )
    return int(cur.lastrowid)




async def update_transaction_chain_details(
    db,
    *,
    trans_id: int,
    tx_hash: str | None = None,
    from_address: str | None = None,
    to_address: str | None = None,
    amount: int | None = None,
    block_number: int | None = None,
) -> None:
    """Update chain-facing TRANSACTION details before final status changes.

    trans_updater.py uses this in two places:
    - after a server-initiated send returns a real tx_hash for a durable local
      outbox row;
    - after RPC verification extracts the real sender/recipient/amount from a
      confirmed on-chain transaction.

    The helper intentionally does not change t_status. Confirmation/failure is
    still handled by set_transaction_status_to_confirmed()/failed().
    """
    updates: list[str] = []
    params: list[Any] = []

    if tx_hash is not None:
        clean_hash = str(tx_hash).strip()
        if not clean_hash:
            raise ValueError("tx_hash must be non-empty")
        updates.append(f"{schema.TRANS_TX_HASH} = ?")
        params.append(clean_hash)

    if from_address is not None:
        updates.append(f"{schema.TRANS_FROM_ADDRESS} = ?")
        params.append(str(from_address))

    if to_address is not None:
        updates.append(f"{schema.TRANS_TO_ADDRESS} = ?")
        params.append(str(to_address))

    if amount is not None:
        amount_i = int(amount)
        if amount_i < 0:
            raise ValueError("amount must be non-negative")
        updates.append(f"{schema.TRANS_AMOUNT} = ?")
        params.append(amount_i)

    if block_number is not None:
        block_number_i = int(block_number)
        if block_number_i < 0:
            raise ValueError("block_number must be non-negative")
        updates.append(f"{schema.TRANS_BLOCK_NUMBER} = ?")
        params.append(block_number_i)

    if not updates:
        return

    params.append(int(trans_id))
    cur = await db.execute(
        f"""
        UPDATE {schema.TRANS_TABLE_NAME}
        SET {", ".join(updates)}
        WHERE {schema.TRANS_ID} = ?;
        """,
        tuple(params),
    )
    _require_one(cur.rowcount, f"Failed to update transaction chain details id={trans_id}")

async def create_spot_deposit_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    tx_hash: str,
    to_address: str | None = None,
) -> int:
    if to_address is None:
        spot = await get_spot(db, spot_id=spot_id)
        if spot is None:
            raise ValueError(f"spot id={spot_id} does not exist")
        to_address = str(spot[schema.SPOT_DEPOSIT_ADDRESS])

    return await _create_transaction(
        db,
        user_id=user_id,
        spot_id=spot_id,
        claim_id=None,
        trans_type=const.TRANS_TYPE_FILL_SPOT,
        amount=amount,
        from_address=from_address,
        to_address=to_address,
        tx_hash=tx_hash,
    )


async def create_spot_refund_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
) -> int:
    return await _create_transaction(
        db,
        user_id=user_id,
        spot_id=spot_id,
        claim_id=None,
        trans_type=const.TRANS_TYPE_CANCEL_SPOT,
        amount=amount,
        from_address=from_address,
        to_address=to_address,
        tx_hash=tx_hash,
    )


async def create_claim_transaction(
    db,
    *,
    user_id: int,
    claim_id: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
) -> int:
    claim = await get_claim(db, claim_id=claim_id)
    if claim is None:
        raise RuntimeError(f"Claim not found id={claim_id}")
    if await has_nonfailed_claim_payout_transaction(db, claim_id=claim_id):
        raise RuntimeError(f"Claim id={claim_id} already has a non-failed payout transaction")

    try:
        return await _create_transaction(
            db,
            user_id=user_id,
            spot_id=int(claim[schema.CLAIM_SPOT_ID]),
            claim_id=claim_id,
            trans_type=const.TRANS_TYPE_CLAIM,
            amount=amount,
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
        )
    except sqlite3.IntegrityError as exc:
        if await has_nonfailed_claim_payout_transaction(db, claim_id=claim_id):
            raise RuntimeError(f"Claim id={claim_id} already has a non-failed payout transaction") from exc
        raise


async def create_platform_fee_transaction(
    db,
    *,
    user_id: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
    spot_id: int | None = None,
    claim_id: int | None = None,
) -> int:
    return await _create_transaction(
        db,
        user_id=user_id,
        spot_id=spot_id,
        claim_id=claim_id,
        trans_type=const.TRANS_TYPE_PLAT_FEE,
        amount=amount,
        from_address=from_address,
        to_address=to_address,
        tx_hash=tx_hash,
    )


async def create_spot_creation_fee_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    to_address: str,
    tx_hash: str,
) -> int:
    """Create one durable creation-fee intent for a fully funded draft.

    Re-check every financial prerequisite inside the same write transaction
    that inserts the intent. This serialises safely against draft cancellation
    and prevents a stale scheduler decision from charging a Spot after its
    cancellation marker has been established.
    """
    spot = await get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("creation fees can only be created for draft spots")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("creation fee cannot be created after cancellation has started")

    expected_owner_id = int(spot[schema.SPOT_CREATED_BY])
    if int(user_id) != expected_owner_id:
        raise ValueError("creation fee user does not match the Spot owner")

    expected_amount = spot_creation_fee_amount(spot)
    if expected_amount <= 0:
        raise ValueError("this Spot has no creation fee")
    if int(amount) != expected_amount:
        raise ValueError("creation fee amount does not match the Spot snapshot")

    allow_dev_placeholder = bool(
        getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)
    )
    expected_from_address = wallet.normalise_nimiq_address(
        str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="spot deposit address",
        allow_dev_placeholder=allow_dev_placeholder,
    )
    submitted_from_address = wallet.normalise_nimiq_address(
        from_address,
        field_name="creation fee from_address",
        allow_dev_placeholder=allow_dev_placeholder,
    )
    if submitted_from_address != expected_from_address:
        raise ValueError("creation fee sender does not match the Spot deposit address")

    expected_address = wallet.normalise_nimiq_address(
        str(spot.get(schema.SPOT_CREATION_FEE_ADDRESS) or ""),
        field_name="spot creation fee address",
        allow_dev_placeholder=allow_dev_placeholder,
    )
    submitted_address = wallet.normalise_nimiq_address(
        to_address,
        field_name="creation fee to_address",
        allow_dev_placeholder=allow_dev_placeholder,
    )
    if submitted_address != expected_address:
        raise ValueError("creation fee recipient does not match the Spot snapshot")

    confirmed_deposit_total = await get_confirmed_spot_deposit_total(
        db,
        spot_id=int(spot_id),
    )
    if confirmed_deposit_total < spot_required_deposit_amount(spot):
        raise ValueError("creation fee cannot be created before full funding confirms")
    if await has_nonfailed_spot_creation_fee_transaction(db, spot_id=int(spot_id)):
        raise RuntimeError(f"Spot id={spot_id} already has a non-failed creation fee transaction")

    try:
        return await _create_transaction(
            db,
            user_id=expected_owner_id,
            spot_id=spot_id,
            claim_id=None,
            trans_type=const.TRANS_TYPE_CREATION_FEE,
            amount=expected_amount,
            from_address=expected_from_address,
            to_address=expected_address,
            tx_hash=tx_hash,
        )
    except sqlite3.IntegrityError as exc:
        if await has_nonfailed_spot_creation_fee_transaction(db, spot_id=int(spot_id)):
            raise RuntimeError(
                f"Spot id={spot_id} already has a non-failed creation fee transaction"
            ) from exc
        raise




async def get_spot_deposit_key_record(db, *, spot_id: int) -> RowDict:
    """Return the stored public address + derivation metadata for a SPOT."""
    spot = await get_spot(db, spot_id=spot_id)
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    return {
        "spot_id": int(spot[schema.SPOT_ID]),
        "deposit_address": spot.get(schema.SPOT_DEPOSIT_ADDRESS),
        "deposit_key_index": spot.get(schema.SPOT_DEPOSIT_KEY_INDEX),
        "deposit_key_path": spot.get(schema.SPOT_DEPOSIT_KEY_PATH),
        "deposit_key_version": spot.get(schema.SPOT_DEPOSIT_KEY_VERSION),
    }


async def derive_spot_deposit_address_from_record(db, *, spot_id: int) -> wallet.DerivedSpotAddress:
    """Re-derive a SPOT deposit address from its stored key index.

    This is a sanity check and the future bridge to recovering the private key
    for a send. It does not return private key material.
    """
    record = await get_spot_deposit_key_record(db, spot_id=spot_id)
    key_index = record.get("deposit_key_index")
    if key_index is None:
        raise ValueError(f"spot id={spot_id} has no deposit_key_index")
    return wallet.derive_spot_deposit_address(
        int(key_index),
        key_version=int(record.get("deposit_key_version") or const.SPOT_DEPOSIT_KEY_VERSION),
    )




async def modify_transaction_status(db, *, trans_id: int, status: int) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.TRANS_TABLE_NAME}
        SET {schema.TRANS_STATUS} = ?
        WHERE {schema.TRANS_ID} = ?
          AND {schema.TRANS_STATUS} = ?;
        """,
        (int(status), int(trans_id), const.TRANS_STATUS_PENDING),
    )
    if cur.rowcount == 1:
        return
    current = await get_transaction(db, trans_id=int(trans_id))
    if current is not None and int(current[schema.TRANS_STATUS]) == int(status):
        return
    _require_one(cur.rowcount, f"Failed to update pending transaction status id={trans_id}")


async def set_transaction_status_to_confirmed(
    db,
    *,
    trans_id: int,
    block_number: int,
) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.TRANS_TABLE_NAME}
        SET {schema.TRANS_STATUS} = ?,
            {schema.TRANS_BLOCK_NUMBER} = ?,
            {schema.TRANS_COMPLETED_AT} = COALESCE({schema.TRANS_COMPLETED_AT}, unixepoch())
        WHERE {schema.TRANS_ID} = ?
          AND {schema.TRANS_STATUS} = ?;
        """,
        (
            const.TRANS_STATUS_CONFIRMED,
            int(block_number),
            int(trans_id),
            const.TRANS_STATUS_PENDING,
        ),
    )
    if cur.rowcount == 1:
        return
    current = await get_transaction(db, trans_id=int(trans_id))
    if current is not None and int(current[schema.TRANS_STATUS]) == const.TRANS_STATUS_CONFIRMED:
        return
    _require_one(cur.rowcount, f"Failed to confirm pending transaction id={trans_id}")


async def set_transaction_status_to_failed(db, *, trans_id: int) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.TRANS_TABLE_NAME}
        SET {schema.TRANS_STATUS} = ?,
            {schema.TRANS_COMPLETED_AT} = COALESCE({schema.TRANS_COMPLETED_AT}, unixepoch())
        WHERE {schema.TRANS_ID} = ?
          AND {schema.TRANS_STATUS} = ?;
        """,
        (const.TRANS_STATUS_FAILED, int(trans_id), const.TRANS_STATUS_PENDING),
    )
    if cur.rowcount == 1:
        return
    current = await get_transaction(db, trans_id=int(trans_id))
    if current is not None and int(current[schema.TRANS_STATUS]) == const.TRANS_STATUS_FAILED:
        return
    _require_one(cur.rowcount, f"Failed to fail pending transaction id={trans_id}")


# ---------------------------------------------------------------------------
# TRANSACTION: getters / metrics
# ---------------------------------------------------------------------------

async def get_transaction(db, *, trans_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_ID} = ?;
        """,
        (int(trans_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_transaction_by_hash(db, *, tx_hash: str) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_TX_HASH} = ?;
        """,
        (str(tx_hash),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_transactions_by_user(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_USER_ID} = ?
        ORDER BY {schema.TRANS_CREATED_AT} DESC, {schema.TRANS_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        (int(user_id), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_transactions_by_spot(
    db,
    *,
    spot_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_SPOT_ID} = ?
        ORDER BY {schema.TRANS_CREATED_AT} DESC, {schema.TRANS_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        (int(spot_id), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_transactions_by_claim(db, *, claim_id: int) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_CLAIM_ID} = ?
        ORDER BY {schema.TRANS_CREATED_AT} DESC, {schema.TRANS_ID} DESC;
        """,
        (int(claim_id),),
    )
    return _rows_to_dicts(rows)


async def get_transactions_by_status(
    db,
    *,
    status: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.TRANS_VIEW_DETAIL}
        WHERE {schema.TRANS_STATUS} = ?
        ORDER BY {schema.TRANS_CREATED_AT} ASC, {schema.TRANS_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (int(status), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def count_transactions_by_status(db, *, status: int) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_STATUS} = ?;
        """,
        (int(status),),
    )
    row = await cur.fetchone()
    return int(row["n"])


async def sum_transactions_by_type_status(
    db,
    *,
    trans_type: int,
    status: int,
) -> int:
    cur = await db.execute(
        f"""
        SELECT COALESCE(SUM({schema.TRANS_AMOUNT}), 0) AS total
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?;
        """,
        (int(trans_type), int(status)),
    )
    row = await cur.fetchone()
    return int(row["total"])


# ---------------------------------------------------------------------------
# REPORT: writes / getters / metrics
# ---------------------------------------------------------------------------

def _validate_report_reason(reason: int) -> int:
    reason = int(reason)
    allowed = getattr(const, "REPORT_REASON_VALUES", set())
    if reason not in allowed:
        raise ValueError("report reason is not valid")
    return reason


def _validate_report_details(details: str | None) -> str | None:
    details = _clean_optional_text(details)
    if details is None:
        return None

    max_chars = int(getattr(const, "REPORT_DETAILS_MAX_CHARS", 300))
    if len(details) > max_chars:
        raise ValueError(f"report details must be no more than {max_chars} characters")
    return details


async def create_report(
    db,
    *,
    spot_id: int,
    user_id: int,
    reason: int,
    details: str | None = None,
) -> int:
    spot = await get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    user = await get_user_by_id(db, user_id=int(user_id))
    if user is None:
        raise ValueError(f"user id={user_id} does not exist")

    reason = _validate_report_reason(reason)
    details = _validate_report_details(details)

    cur = await db.execute(
        f"""
        INSERT INTO {schema.REPORT_TABLE_NAME} (
            {schema.REPORT_SPOT_ID},
            {schema.REPORT_REPORTED_BY},
            {schema.REPORT_REASON},
            {schema.REPORT_DETAILS},
            {schema.REPORT_STATUS},
            {schema.REPORT_MODERATOR_NOTE},
            {schema.REPORT_REVIEWED_AT}
        )
        VALUES (?, ?, ?, ?, ?, NULL, NULL);
        """,
        (
            int(spot_id),
            int(user_id),
            reason,
            details,
            const.REPORT_STATUS_PENDING,
        ),
    )
    return int(cur.lastrowid)


async def modify_report_status(
    db,
    *,
    report_id: int,
    status: int,
    moderator_note: str | None = None,
) -> None:
    cur = await db.execute(
        f"""
        UPDATE {schema.REPORT_TABLE_NAME}
        SET {schema.REPORT_STATUS} = ?,
            {schema.REPORT_MODERATOR_NOTE} = ?
        WHERE {schema.REPORT_ID} = ?;
        """,
        (int(status), _clean_optional_text(moderator_note), int(report_id)),
    )
    _require_one(cur.rowcount, f"Failed to update report status id={report_id}")


async def approve_report(
    db,
    *,
    report_id: int,
    moderator_note: str | None = None,
) -> None:
    await modify_report_status(
        db,
        report_id=report_id,
        status=const.REPORT_STATUS_APPROVED,
        moderator_note=moderator_note,
    )


async def dismiss_report(
    db,
    *,
    report_id: int,
    moderator_note: str | None = None,
) -> None:
    await modify_report_status(
        db,
        report_id=report_id,
        status=const.REPORT_STATUS_DISMISSED,
        moderator_note=moderator_note,
    )


async def get_report(db, *, report_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT *
        FROM {schema.REPORT_VIEW_DETAIL}
        WHERE {schema.REPORT_ID} = ?;
        """,
        (int(report_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def get_reports_by_status(
    db,
    *,
    status: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.REPORT_VIEW_DETAIL}
        WHERE {schema.REPORT_STATUS} = ?
        ORDER BY {schema.REPORT_CREATED_AT} ASC, {schema.REPORT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (int(status), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_reports_for_spot(
    db,
    *,
    spot_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.REPORT_VIEW_DETAIL}
        WHERE {schema.REPORT_SPOT_ID} = ?
        ORDER BY {schema.REPORT_CREATED_AT} DESC, {schema.REPORT_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        (int(spot_id), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def get_reports_by_user(
    db,
    *,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[RowDict]:
    rows = await db.execute_fetchall(
        f"""
        SELECT *
        FROM {schema.REPORT_VIEW_DETAIL}
        WHERE {schema.REPORT_REPORTED_BY} = ?
        ORDER BY {schema.REPORT_CREATED_AT} DESC, {schema.REPORT_ID} DESC
        LIMIT ? OFFSET ?;
        """,
        (int(user_id), _clamp_limit(limit), _normalise_offset(offset)),
    )
    return _rows_to_dicts(rows)


async def has_user_reported_spot(db, *, spot_id: int, user_id: int) -> bool:
    cur = await db.execute(
        f"""
        SELECT 1
        FROM {schema.REPORT_TABLE_NAME}
        WHERE {schema.REPORT_SPOT_ID} = ?
          AND {schema.REPORT_REPORTED_BY} = ?
        LIMIT 1;
        """,
        (int(spot_id), int(user_id)),
    )
    return await cur.fetchone() is not None


async def count_reports_by_status(db, *, status: int) -> int:
    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.REPORT_TABLE_NAME}
        WHERE {schema.REPORT_STATUS} = ?;
        """,
        (int(status),),
    )
    row = await cur.fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Dashboard / metric helpers
# ---------------------------------------------------------------------------

async def get_user_dashboard_counts(db, *, user_id: int) -> RowDict:
    """Small set of counters for Home/My History/My Spots badges."""
    cur = await db.execute(
        f"""
        SELECT
            (SELECT COUNT(*) FROM {schema.SPOT_TABLE_NAME}
             WHERE {schema.SPOT_CREATED_BY} = ?) AS spot_count,
            (SELECT COUNT(*) FROM {schema.SPOT_TABLE_NAME}
             WHERE {schema.SPOT_CREATED_BY} = ?
               AND {schema.SPOT_STATUS} = {const.SPOT_STATUS_PUBLISHED}) AS published_spot_count,
            (SELECT COUNT(*) FROM {schema.CLAIM_TABLE_NAME}
             WHERE {schema.CLAIM_RECIPIENT} = ?) AS claim_count,
            (SELECT COUNT(*) FROM {schema.CLAIM_TABLE_NAME}
             WHERE {schema.CLAIM_RECIPIENT} = ?
               AND {schema.CLAIM_STATUS} = {const.CLAIM_STATUS_SUCCESS}) AS successful_claim_count;
        """,
        (int(user_id), int(user_id), int(user_id), int(user_id)),
    )
    row = await cur.fetchone()
    return dict(row)


async def get_platform_dashboard_counts(db) -> RowDict:
    """Coarse admin/health metrics."""
    cur = await db.execute(
        f"""
        SELECT
            (SELECT COUNT(*) FROM {schema.USER_TABLE_NAME}) AS user_count,
            (SELECT COUNT(*) FROM {schema.SPOT_TABLE_NAME}) AS spot_count,
            (SELECT COUNT(*) FROM {schema.SPOT_TABLE_NAME}
             WHERE {schema.SPOT_STATUS} = {const.SPOT_STATUS_PUBLISHED}) AS published_spot_count,
            (SELECT COUNT(*) FROM {schema.CLAIM_TABLE_NAME}) AS claim_count,
            (SELECT COUNT(*) FROM {schema.CLAIM_TABLE_NAME}
             WHERE {schema.CLAIM_STATUS} = {const.CLAIM_STATUS_SUCCESS}) AS successful_claim_count,
            (SELECT COUNT(*) FROM {schema.TRANS_TABLE_NAME}
             WHERE {schema.TRANS_STATUS} = {const.TRANS_STATUS_PENDING}) AS pending_transaction_count,
            (SELECT COUNT(*) FROM {schema.REPORT_TABLE_NAME}
             WHERE {schema.REPORT_STATUS} = {const.REPORT_STATUS_PENDING}) AS pending_report_count;
        """
    )
    row = await cur.fetchone()
    return dict(row)


async def get_spot_financial_summary(db, *, spot_id: int) -> RowDict:
    """Return transaction totals grouped by type/status for a SPOT."""
    rows = await db.execute_fetchall(
        f"""
        SELECT
            {schema.TRANS_TYPE} AS trans_type,
            {schema.TRANS_STATUS} AS trans_status,
            COUNT(*) AS trans_count,
            COALESCE(SUM({schema.TRANS_AMOUNT}), 0) AS total_amount
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
        GROUP BY {schema.TRANS_TYPE}, {schema.TRANS_STATUS}
        ORDER BY {schema.TRANS_TYPE} ASC, {schema.TRANS_STATUS} ASC;
        """,
        (int(spot_id),),
    )
    return {f"{int(r['trans_type'])}:{int(r['trans_status'])}": {
        "trans_type": int(r["trans_type"]),
        "trans_status": int(r["trans_status"]),
        "trans_count": int(r["trans_count"]),
        "total_amount": int(r["total_amount"]),
    } for r in rows}


async def get_due_spots_to_complete(
    db,
    *,
    now: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[int]:
    """Return published SPOT ids whose end time has passed.

    Useful for a scheduler/maintenance task that marks old SPOTs completed.
    """
    if now is None:
        now = await get_unixepoch(db)
    rows = await db.execute_fetchall(
        f"""
        SELECT {schema.SPOT_ID}
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_STATUS} = ?
          AND {schema.SPOT_STARTS_AT} IS NOT NULL
          AND {schema.SPOT_ENDS_AT} IS NOT NULL
          AND ({schema.SPOT_STARTS_AT} + {schema.SPOT_ENDS_AT}) <= ?
        ORDER BY ({schema.SPOT_STARTS_AT} + {schema.SPOT_ENDS_AT}) ASC, {schema.SPOT_ID} ASC
        LIMIT ? OFFSET ?;
        """,
        (
            const.SPOT_STATUS_PUBLISHED,
            int(now),
            _clamp_limit(limit),
            _normalise_offset(offset),
        ),
    )
    return [int(r[schema.SPOT_ID]) for r in rows]


async def complete_due_spots(
    db,
    *,
    now: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """Mark due published SPOTs as completed and return the number updated."""
    due_ids = await get_due_spots_to_complete(db, now=now, limit=limit)
    if not due_ids:
        return 0
    placeholders = _sql_placeholders(len(due_ids))
    cur = await db.execute(
        f"""
        UPDATE {schema.SPOT_TABLE_NAME}
        SET {schema.SPOT_STATUS} = ?
        WHERE {schema.SPOT_ID} IN ({placeholders});
        """,
        (const.SPOT_STATUS_COMPLETED, *due_ids),
    )
    return int(cur.rowcount)
