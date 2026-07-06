"""
public_html.py

Public FastAPI routes for the NimHunt webview.

This file is the first frontend-facing layer. The browser/webview calls these
routes, and these routes then call db_access.py/cache.py. The frontend should
not talk directly to the database.

Current scope:
- Home page HTML.
- Home/session API: identify or create the USER from the Nimiq Pay device id.
- Display-name update API.
- My Spots page/API for creator-owned spots.
- My Claims page/API for claims made by the current device.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import constants as const
import database as schema
from database import get_db
import db_access
import trans_updater

try:
    import cache
except Exception:  # pragma: no cover - cache is optional during early setup
    cache = None  # type: ignore[assignment]

try:
    import settlement_updater
except Exception:  # pragma: no cover - settlement is optional while bootstrapping
    settlement_updater = None  # type: ignore[assignment]


router = APIRouter()
templates = Jinja2Templates(directory="templates")

_ASSET_VERSION = "custom-404-create-url-v1-20260706"

_DEVICE_ID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_LANGUAGE_RE = re.compile(r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{2,8})*$")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class HomeSessionRequest(BaseModel):
    """Payload sent by the webview after it asks Nimiq Pay for context."""

    device_id_hash: str | None = Field(default=None, max_length=128)
    wallet_available: bool = False
    language: str | None = Field(default=None, max_length=32)

    location_available: bool = False
    lat: float | None = None
    long: float | None = None
    accuracy: float | None = None


class DisplayNameRequest(BaseModel):
    """Payload for changing the current user's display name."""

    device_id_hash: str | None = Field(default=None, max_length=64)
    display_name: str = Field(min_length=1, max_length=40)


class CreateDraftSpotRequest(HomeSessionRequest):
    """First Create Spot step: identify creator and create a titled draft."""

    title: str = Field(min_length=const.SPOT_TITLE_MIN_CHARS, max_length=const.SPOT_TITLE_MAX_CHARS)
    is_prizedraw: bool = False
    captcha_a: int = Field(ge=1, le=20)
    captcha_b: int = Field(ge=1, le=20)
    captcha_answer: int = Field(ge=0, le=40)


class ReportSpotRequest(HomeSessionRequest):
    """Report submission payload for a public SPOT detail page."""

    reason: int = Field(ge=1)
    details: str | None = Field(default=None, max_length=const.REPORT_DETAILS_MAX_CHARS)
    captcha_a: int = Field(ge=1, le=20)
    captcha_b: int = Field(ge=1, le=20)
    captcha_answer: int = Field(ge=0, le=40)


class UpdateDraftSpotRequest(HomeSessionRequest):
    """Full Create Spot form update for an existing DRAFT SPOT."""

    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    lat: float | None = Field(default=None, ge=-90, le=90)
    long: float | None = Field(default=None, ge=-180, le=180)
    radius: int | None = Field(default=None, ge=const.MIN_SPOT_RADIUS_METRES, le=const.MAX_SPOT_RADIUS_METRES)
    claim_duration: int | None = Field(
        default=None,
        ge=const.MIN_SPOT_CLAIM_DURATION_SECONDS,
        le=const.MAX_SPOT_CLAIM_DURATION_SECONDS,
    )
    max_claims_per_user: int | None = Field(
        default=None,
        ge=const.MIN_SPOT_MAX_CLAIMS_PER_USER,
        le=const.MAX_SPOT_MAX_CLAIMS_PER_USER,
    )
    max_total_claims: int | None = Field(
        default=None,
        ge=const.MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS,
        le=const.MAX_SPOT_MAX_TOTAL_CLAIMS,
    )
    prize_count: int | None = Field(
        default=None,
        ge=const.MIN_PRIZEDRAW_PRIZE_COUNT,
        le=const.MAX_PRIZEDRAW_PRIZE_COUNT,
    )
    total_value: int | None = Field(default=None, ge=const.MIN_SPOT_TOTAL_VALUE)
    starts_at: int | None = Field(default=None, ge=1)
    ends_at: int | None = Field(
        default=None,
        ge=const.MIN_SPOT_ENDS_AFTER_SECONDS,
        le=const.MAX_SPOT_ENDS_AFTER_SECONDS,
    )
    use_password: bool | None = None
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)


class DepositSubmittedRequest(HomeSessionRequest):
    """Payload sent after Nimiq Pay returns a deposit transaction hash."""

    tx_hash: str = Field(min_length=1, max_length=160)
    from_address: str | None = Field(default=None, max_length=160)
    amount: int | None = Field(default=None, ge=1)


class ClaimStatusRequest(HomeSessionRequest):
    """Payload for enriching visible spots with current-user claim rules."""

    spot_ids: list[int] = Field(default_factory=list, max_length=500)


class ClaimSpotRequest(HomeSessionRequest):
    """Payload for starting/entering a claim from the Find Spots page."""

    payout_address: str | None = Field(default=None, max_length=const.CLAIM_PAYOUT_ADDRESS_MAX_CHARS)
    claim_code: str | None = Field(default=None, max_length=120)
    captcha_a: int | None = Field(default=None, ge=1, le=20)
    captcha_b: int | None = Field(default=None, ge=1, le=20)
    captcha_answer: int | None = Field(default=None, ge=0, le=40)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean_language(language: str | None) -> str | None:
    """Return a safe language string or None.

    Nimiq Pay exposes a two-letter language such as "en". We allow slightly
    longer BCP-47-ish values so local browser fallbacks like "en-GB" do not
    cause needless rejection.
    """
    if language is None:
        return None

    language = language.strip()
    if not language:
        return None

    if not _ALLOWED_LANGUAGE_RE.fullmatch(language):
        return None

    return language.lower()


def _valid_device_id_hash(value: str | None) -> bool:
    return bool(value and _DEVICE_ID_RE.fullmatch(value.strip()))


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a USER row for frontend use.

    The device hash is deliberately not returned. The browser already has it;
    there is no need to echo it into every API response.
    """
    return {
        "id": int(row[schema.USER_ID]),
        "display_name": row.get(schema.USER_DISPLAY_NAME),
        "status": int(row[schema.USER_STATUS]),
        "created_at": int(row[schema.USER_CREATED_AT]),
        "last_seen_at": int(row[schema.USER_LAST_SEEN_AT]),
        "is_active": int(row[schema.USER_STATUS]) == const.USER_STATUS_ACTIVE,
        "is_limited": int(row[schema.USER_STATUS]) == const.USER_STATUS_LIMITED,
        "is_banned": int(row[schema.USER_STATUS]) == const.USER_STATUS_BANNED,
    }


def _payload_field_names(payload: BaseModel) -> set[str]:
    """Return fields explicitly supplied by the client for Pydantic v1/v2."""
    return set(
        getattr(
            payload,
            "model_fields_set",
            getattr(payload, "__fields_set__", set()),
        )
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


async def _notify_user_cache(db, *, user_id: int) -> None:
    """Refresh/invalidate user cache when available.

    cache.py has changed during development, so this intentionally uses a soft
    lookup. If the cache module is not ready yet, the home page still works.
    """
    if cache is None:
        return

    notify = getattr(cache, "notify_user_changed", None)
    mark_dirty = getattr(cache, "mark_user_cache_dirty", None)

    if callable(notify):
        await notify(db, user_id=int(user_id))
    elif callable(mark_dirty):
        mark_dirty()


async def _notify_spot_cache(db, *, spot_id: int) -> None:
    """Refresh/invalidate public SPOT cache when a SPOT lifecycle changes."""
    if cache is None:
        return

    notify = getattr(cache, "notify_spot_changed", None)
    mark_dirty = getattr(cache, "mark_spot_cache_dirty", None)

    if callable(notify):
        await notify(db, spot_id=int(spot_id))
    elif callable(mark_dirty):
        mark_dirty()


async def _notify_all_cache_for_spot_owner_change(db, *, user_id: int, spot_id: int) -> None:
    await _notify_user_cache(db, user_id=int(user_id))
    await _notify_spot_cache(db, spot_id=int(spot_id))


async def _notify_all_cache_if_user_display_changed(db, *, user_id: int) -> None:
    """A display-name change can affect user cache and public spot creator names."""
    if cache is None:
        return

    notify_user = getattr(cache, "notify_user_changed", None)
    mark_user_dirty = getattr(cache, "mark_user_cache_dirty", None)
    mark_spot_dirty = getattr(cache, "mark_spot_cache_dirty", None)

    if callable(notify_user):
        await notify_user(db, user_id=int(user_id))
    elif callable(mark_user_dirty):
        mark_user_dirty()

    if callable(mark_spot_dirty):
        mark_spot_dirty()


async def _home_metrics(db) -> dict[str, int]:
    """Return small public homepage metrics.

    Prefer cache.py when available so the Home page does not need bespoke SQL
    for common counters. Fall back to direct SQLite counts if the cache module
    is unavailable or still being developed.
    """
    if cache is not None:
        cached_metrics = getattr(cache, "get_cached_home_metrics", None)
        if callable(cached_metrics):
            metrics = await cached_metrics(db)
            return {
                "active_spot_count": int(metrics.get("active_spot_count", 0) or 0),
                "daily_user_count": int(metrics.get("daily_user_count", 0) or 0),
            }

        cached_spot_counts = getattr(cache, "get_cached_spot_counts", None)
        cached_daily_users = getattr(cache, "get_cached_daily_user_count", None)
        if callable(cached_spot_counts) and callable(cached_daily_users):
            spot_counts = await cached_spot_counts(db)
            daily_users = await cached_daily_users(db)
            return {
                "active_spot_count": int(spot_counts.get("current_spot_count", 0) or 0),
                "daily_user_count": int(daily_users or 0),
            }

    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.SPOT_TABLE_NAME}
        WHERE {schema.SPOT_STATUS} = ?
          AND {schema.SPOT_LAT} IS NOT NULL
          AND {schema.SPOT_LONG} IS NOT NULL
          AND ({schema.SPOT_STARTS_AT} IS NULL OR {schema.SPOT_STARTS_AT} <= unixepoch())
          AND ({schema.SPOT_STARTS_AT} IS NULL OR ({schema.SPOT_STARTS_AT} + {schema.SPOT_ENDS_AT}) > unixepoch());
        """,
        (const.SPOT_STATUS_PUBLISHED,),
    )
    active_row = await cur.fetchone()

    cur = await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {schema.USER_TABLE_NAME}
        WHERE {schema.USER_LAST_SEEN_AT} >= unixepoch() - 86400;
        """
    )
    daily_row = await cur.fetchone()

    return {
        "active_spot_count": int(active_row["n"] or 0),
        "daily_user_count": int(daily_row["n"] or 0),
    }


def _shared_template_context(request: Request, *, page_title: str | None = None) -> dict[str, Any]:
    """Common Jinja values for public pages."""
    return {
        "request": request,
        "page_title": page_title or const.APP_NAME,
        "app_name": const.APP_NAME,
        "app_icon_path": const.APP_ICON_PATH,
        "nimiq_pay_url": const.NIMIQ_PAY_URL,
        "display_name_min": const.DISPLAY_NAME_MIN_CHARS,
        "display_name_max": const.DISPLAY_NAME_MAX_CHARS,
        "spot_title_min": const.SPOT_TITLE_MIN_CHARS,
        "spot_title_max": const.SPOT_TITLE_MAX_CHARS,
        "max_draft_spots_per_user": int(getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3)),
        "min_standard_claim_payout_nim": int(getattr(const, "MIN_STANDARD_CLAIM_PAYOUT_NIM", 100)),
        "min_prizedraw_prize_payout_nim": int(getattr(const, "MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM", 1000)),
        "claim_captcha_min": int(getattr(const, "CLAIM_CAPTCHA_MIN", 1)),
        "claim_captcha_max": int(getattr(const, "CLAIM_CAPTCHA_MAX", 9)),
        "asset_version": _ASSET_VERSION,
        "nimiq_style_cdn": "https://cdn.jsdelivr.net/npm/@nimiq/style@v0.8.5/nimiq-style.min.css",
        "google_font_muli": "https://fonts.googleapis.com/css?family=Muli:400,600,700",
    }


def _is_spot_upcoming(spot: dict[str, Any], *, now: int) -> bool:
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    return starts_at is not None and int(starts_at) > int(now)


def _is_spot_active(spot: dict[str, Any], *, now: int) -> bool:
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    return starts_at is None or int(starts_at) <= int(now)


def _spot_status_label(spot: dict[str, Any], *, now: int) -> str:
    return "upcoming" if _is_spot_upcoming(spot, now=now) else "active"


def _spot_absolute_ends_at(spot: dict[str, Any]) -> int | None:
    """Return absolute end timestamp from relative SPOT.ends_at seconds."""
    starts_at = spot.get(schema.SPOT_STARTS_AT)
    ends_after = spot.get(schema.SPOT_ENDS_AT)
    if starts_at is None or ends_after is None:
        return None
    return int(starts_at) + int(ends_after)


def _normalise_cached_spot_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return the raw SPOT row whether the source is cache.py or db_access.py."""
    nested = item.get("spot")
    if isinstance(nested, dict):
        return nested
    return item


def _spot_has_prizedraw(item: dict[str, Any]) -> bool:
    """Return True when a SPOT has a matching PRIZEDRAW row."""
    prizedraw = item.get("prizedraw")
    if isinstance(prizedraw, dict):
        return True

    spot = _normalise_cached_spot_item(item)
    return spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None


def _spot_matches_filters(
    item: dict[str, Any],
    *,
    now: int,
    include_active: bool,
    include_upcoming: bool,
    include_prizedraws: bool,
) -> bool:
    if _spot_has_prizedraw(item) and not include_prizedraws:
        return False

    spot = _normalise_cached_spot_item(item)
    return (
        (_is_spot_active(spot, now=now) and include_active)
        or (_is_spot_upcoming(spot, now=now) and include_upcoming)
    )


def _serialise_spot_for_map(
    item: dict[str, Any],
    *,
    now: int,
    distance_from_lat: float | None = None,
    distance_from_long: float | None = None,
) -> dict[str, Any]:
    """Shape a public SPOT row for the Find Spots map/list.

    This deliberately exposes only what the map/list needs. More sensitive or
    owner-specific data should wait for the future Spot detail page.
    """
    spot = _normalise_cached_spot_item(item)
    lat = float(spot[schema.SPOT_LAT])
    long = float(spot[schema.SPOT_LONG])

    distance_m: int | None = None
    if distance_from_lat is not None and distance_from_long is not None:
        distance_m = round(db_access.distance_metres(distance_from_lat, distance_from_long, lat, long))

    link = spot.get(schema.SPOT_LINK)
    spot_id = int(spot[schema.SPOT_ID])
    detail_href = f"{const.SPOT_PAGE_URL_PREFIX}/{link or spot_id}"

    creator = item.get("creator") if isinstance(item.get("creator"), dict) else {}
    prizedraw = item.get("prizedraw") if isinstance(item.get("prizedraw"), dict) else None
    counts = item.get("counts") if isinstance(item.get("counts"), dict) else {}
    is_prizedraw = _spot_has_prizedraw(item)

    return {
        "id": spot_id,
        "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),
        "link": link,
        "title": spot.get(schema.SPOT_TITLE) or "NimHunt Spot",
        "description": spot.get(schema.SPOT_DESC),
        "lat": lat,
        "long": long,
        "city": spot.get(schema.SPOT_CITY),
        "country": spot.get(schema.SPOT_COUNTRY),
        "radius": int(spot.get(schema.SPOT_RADIUS) or 25),
        "claim_duration": int(spot.get(schema.SPOT_CLAIM_DURATION) or 0),
        "use_password": bool(int(spot.get(schema.SPOT_USE_PASSWORD) or 0)),
        "max_claims_per_user": int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 1),
        "max_total_claims": int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) if spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) is not None else 1),
        "total_value": int(spot.get(schema.SPOT_TOTAL_VALUE) or 0),
        "starts_at": spot.get(schema.SPOT_STARTS_AT),
        "ends_at": _spot_absolute_ends_at(spot),
        "ends_after": spot.get(schema.SPOT_ENDS_AT),
        "status_label": _spot_status_label(spot, now=now),
        "is_prizedraw": is_prizedraw,
        "prize_count": (
            prizedraw.get(schema.PRIZEDRAW_PRIZE_COUNT)
            if prizedraw is not None
            else spot.get(schema.PRIZEDRAW_PRIZE_COUNT)
        ),
        "claim_count": int(counts.get("claim_count", spot.get("claim_count") or 0) or 0),
        "pending_claim_count": int(counts.get("pending_claim_count", spot.get("pending_claim_count") or 0) or 0),
        "success_claim_count": int(counts.get("success_claim_count", spot.get("success_claim_count") or 0) or 0),
        "failed_claim_count": int(counts.get("failed_claim_count", spot.get("failed_claim_count") or 0) or 0),
        "claim_code_count": int(counts.get("claim_code_count", spot.get("claim_code_count") or 0) or 0),
        "unused_code_count": int(counts.get("unused_code_count", spot.get("unused_code_count") or 0) or 0),
        "used_code_count": int(counts.get("used_code_count", spot.get("used_code_count") or 0) or 0),
        "creator_display_name": (
            spot.get("creator_display_name")
            or creator.get(schema.USER_DISPLAY_NAME)
            or creator.get("display_name")
        ),
        "distance_m": distance_m,
        "href": detail_href,
        "claim_href": f"{getattr(const, 'CLAIM_PAGE_URL_PREFIX', '/claim')}/",
    }



async def _get_public_spot_detail_row(db, *, spot_ref: str) -> dict[str, Any] | None:
    """Return one public SPOT detail row by numeric id or public link/slug."""
    spot_ref = str(spot_ref or "").strip()
    if not spot_ref:
        return None

    if spot_ref.isdigit():
        cur = await db.execute(
            f"""
            SELECT
                s.*,
                u.{schema.USER_DISPLAY_NAME} AS creator_display_name,
                u.{schema.USER_STATUS} AS creator_status
            FROM {schema.SPOT_VIEW_PUBLIC_LIST} s
            JOIN {schema.USER_TABLE_NAME} u
                ON u.{schema.USER_ID} = s.{schema.SPOT_CREATED_BY}
            WHERE s.{schema.SPOT_ID} = ?
               OR s.{schema.SPOT_LINK} = ?;
            """,
            (int(spot_ref), spot_ref),
        )
    else:
        cur = await db.execute(
            f"""
            SELECT
                s.*,
                u.{schema.USER_DISPLAY_NAME} AS creator_display_name,
                u.{schema.USER_STATUS} AS creator_status
            FROM {schema.SPOT_VIEW_PUBLIC_LIST} s
            JOIN {schema.USER_TABLE_NAME} u
                ON u.{schema.USER_ID} = s.{schema.SPOT_CREATED_BY}
            WHERE s.{schema.SPOT_LINK} = ?;
            """,
            (spot_ref,),
        )

    row = await cur.fetchone()
    return dict(row) if row is not None else None


def _serialise_public_spot_for_detail(spot: dict[str, Any], *, now: int) -> dict[str, Any]:
    """Shape one public SPOT for the standalone Spot detail page."""
    link = spot.get(schema.SPOT_LINK)
    spot_id = int(spot[schema.SPOT_ID])

    return {
        "id": spot_id,
        "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),
        "link": link,
        "title": spot.get(schema.SPOT_TITLE) or "NimHunt Spot",
        "description": spot.get(schema.SPOT_DESC),
        "city": spot.get(schema.SPOT_CITY),
        "country": spot.get(schema.SPOT_COUNTRY),
        "lat": float(spot[schema.SPOT_LAT]),
        "long": float(spot[schema.SPOT_LONG]),
        "radius": int(spot.get(schema.SPOT_RADIUS) or 25),
        "claim_duration": int(spot.get(schema.SPOT_CLAIM_DURATION) or 0),
        "use_password": bool(int(spot.get(schema.SPOT_USE_PASSWORD) or 0)),
        "max_claims_per_user": int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 1),
        "max_total_claims": int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) if spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) is not None else 1),
        "total_value": int(spot.get(schema.SPOT_TOTAL_VALUE) or 0),
        "starts_at": spot.get(schema.SPOT_STARTS_AT),
        "ends_at": _spot_absolute_ends_at(spot),
        "ends_after": spot.get(schema.SPOT_ENDS_AT),
        "status_label": _spot_status_label(spot, now=now),
        "is_prizedraw": _spot_is_prizedraw_row(spot),
        "prize_count": spot.get(schema.PRIZEDRAW_PRIZE_COUNT),
        "claim_count": int(spot.get("claim_count") or 0),
        "pending_claim_count": int(spot.get("pending_claim_count") or 0),
        "success_claim_count": int(spot.get("success_claim_count") or 0),
        "failed_claim_count": int(spot.get("failed_claim_count") or 0),
        "claim_code_count": int(spot.get("claim_code_count") or 0),
        "unused_code_count": int(spot.get("unused_code_count") or 0),
        "used_code_count": int(spot.get("used_code_count") or 0),
        "creator_display_name": spot.get("creator_display_name"),
        "distance_m": None,
        "href": f"{const.SPOT_PAGE_URL_PREFIX}/{link or spot_id}",
    }

def _sort_spots_for_map(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Distance first, then active before upcoming, then soonest."""
    def key(item: dict[str, Any]) -> tuple[float, int, int, int]:
        distance = item.get("distance_m")
        distance_sort = float(distance) if distance is not None else float("inf")
        status_sort = 0 if item.get("status_label") == "active" else 1
        starts_at = item.get("starts_at")
        soon_sort = int(starts_at) if starts_at is not None else 0
        return (distance_sort, status_sort, soon_sort, int(item["id"]))

    return sorted(items, key=key)


def _spot_detail_href(spot: dict[str, Any]) -> str:
    """Return the normal public/detail URL for a SPOT row."""
    link = spot.get(schema.SPOT_LINK)
    spot_id = int(spot[schema.SPOT_ID])
    return f"{const.SPOT_PAGE_URL_PREFIX}/{link or spot_id}"


def _owner_spot_status_label(spot: dict[str, Any], *, now: int) -> str:
    """Return a human/JS-friendly owner status label for any SPOT state."""
    status_code = int(spot[schema.SPOT_STATUS])

    if status_code == const.SPOT_STATUS_DRAFT:
        return "draft"
    if status_code == const.SPOT_STATUS_COMPLETED:
        return "completed"
    if status_code == const.SPOT_STATUS_CANCELLED:
        return "cancelled"
    if status_code == const.SPOT_STATUS_BANNED:
        return "banned"

    if status_code == const.SPOT_STATUS_PUBLISHED:
        ends_at = _spot_absolute_ends_at(spot)
        if ends_at is not None and int(ends_at) <= int(now):
            return "ended"
        return _spot_status_label(spot, now=now)

    return "unknown"


def _owner_spot_bucket(spot: dict[str, Any], *, now: int, status_label: str) -> str:
    """Return which My Spots list should contain this SPOT.

    Buckets are deliberately separate from the visible status badge. Drafts now
    have their own My Spots section, because editable drafts and published
    upcoming spots need different owner actions.
    """
    status_code = int(spot[schema.SPOT_STATUS])
    ends_at = _spot_absolute_ends_at(spot)
    starts_at = spot.get(schema.SPOT_STARTS_AT)

    if status_code == const.SPOT_STATUS_DRAFT:
        return "draft"

    if status_label == "active":
        return "active"

    if status_code in (
        const.SPOT_STATUS_COMPLETED,
        const.SPOT_STATUS_CANCELLED,
        const.SPOT_STATUS_BANNED,
    ):
        return "previous"

    if ends_at is not None and int(ends_at) <= int(now):
        return "previous"

    if status_label == "upcoming":
        return "upcoming"

    if starts_at is not None and int(starts_at) > int(now):
        return "upcoming"

    return "previous"


def _spot_is_prizedraw_row(spot: dict[str, Any]) -> bool:
    return spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None


def _transaction_status_label(status_code: int | None) -> str:
    if status_code == const.TRANS_STATUS_PENDING:
        return "pending"
    if status_code == const.TRANS_STATUS_CONFIRMED:
        return "confirmed"
    if status_code == const.TRANS_STATUS_FAILED:
        return "failed"
    return "missing"


def _deposit_summary(transactions: list[dict[str, Any]], *, total_value: int = 0) -> dict[str, Any]:
    """Summarise fill/deposit transactions for a creator-owned SPOT.

    The frontend only shows this for drafts. We still return the raw useful
    totals so future owner controls can make the same decision server-side.
    """
    fill_transactions = [
        trans
        for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
    ]

    confirmed_amount = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in fill_transactions
        if int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    pending_amount = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in fill_transactions
        if int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_PENDING
    )
    failed_amount = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in fill_transactions
        if int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_FAILED
    )
    recorded_amount = confirmed_amount + pending_amount + failed_amount
    submitted_amount = confirmed_amount + pending_amount
    total_value = int(total_value or 0)

    # A top-up is only requestable when confirmed + pending deposits are still
    # below the target, and no pending deposit is currently waiting. Pending
    # deposits do not unlock publishing, but they do lock the value and block a
    # second payment request until they either confirm or fail.
    amount_due = max(0, total_value - submitted_amount)

    if submitted_amount <= 0:
        status_value = "missing"
        status_label = "No Deposit"
    elif confirmed_amount >= total_value and total_value > 0:
        status_value = "ready"
        status_label = "Ready"
    else:
        status_value = "partial"
        status_label = "Partial Deposit"

    latest = fill_transactions[0] if fill_transactions else None
    return {
        "status": status_value,
        "status_label": status_label,
        "paid": status_value == "ready",
        "amount": submitted_amount,
        "recorded_amount": recorded_amount,
        "submitted_amount": submitted_amount,
        "amount_due": amount_due,
        "has_any": bool(fill_transactions),
        "has_submitted": submitted_amount > 0,
        "has_pending": pending_amount > 0,
        "confirmed_amount": confirmed_amount,
        "pending_amount": pending_amount,
        "failed_amount": failed_amount,
        "tx_hash": latest.get(schema.TRANS_TX_HASH) if latest else None,
        "created_at": latest.get(schema.TRANS_CREATED_AT) if latest else None,
    }

def _cancellation_summary(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate remaining cancellation refund/fee from current transactions."""
    confirmed_deposit_amount = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    outgoing_types = {
        const.TRANS_TYPE_CLAIM,
        const.TRANS_TYPE_CANCEL_SPOT,
        const.TRANS_TYPE_PLAT_FEE,
    }
    nonfailed_outgoing_amount = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(trans.get(schema.TRANS_STATUS) or -1) != const.TRANS_STATUS_FAILED
    )
    remaining_amount = max(0, confirmed_deposit_amount - nonfailed_outgoing_amount)
    configured_fee = max(0, int(getattr(const, "SPOT_CANCELLATION_FEE", 0)))
    fee_amount = min(configured_fee, remaining_amount)
    refund_amount = max(0, remaining_amount - fee_amount)
    remaining_lost = remaining_amount > 0 and refund_amount <= 0
    return {
        "confirmed_deposit_amount": confirmed_deposit_amount,
        "nonfailed_outgoing_amount": nonfailed_outgoing_amount,
        "remaining_amount": remaining_amount,
        "configured_fee": configured_fee,
        "fee_amount": fee_amount,
        "refund_amount": refund_amount,
        "remaining_lost": remaining_lost,
        "fee_address": getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
    }


def _serialise_owner_spot(
    spot: dict[str, Any],
    *,
    now: int,
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Shape a creator-owned SPOT for the My Spots page.

    This intentionally returns owner-relevant counters and transaction summary,
    but not raw claim-code/password text.
    """
    status_label = _owner_spot_status_label(spot, now=now)
    total_value = int(spot.get(schema.SPOT_TOTAL_VALUE) or 0)
    deposit = _deposit_summary(transactions, total_value=total_value)
    confirmed_deposit_amount = int(deposit.get("confirmed_amount") or 0)
    is_prizedraw = _spot_is_prizedraw_row(spot)
    cancellation = _cancellation_summary(transactions)
    bucket = _owner_spot_bucket(spot, now=now, status_label=status_label)

    lat_value = spot.get(schema.SPOT_LAT)
    long_value = spot.get(schema.SPOT_LONG)
    starts_at_value = spot.get(schema.SPOT_STARTS_AT)
    draft_start_time_past = (
        status_label == "draft"
        and starts_at_value is not None
        and int(starts_at_value) <= int(now)
    )
    payout_divisor = (
        max(1, int(spot.get(schema.PRIZEDRAW_PRIZE_COUNT) or 1))
        if is_prizedraw
        else max(1, int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0))
    )
    minimum_payout = (
        int(getattr(const, "MIN_PRIZEDRAW_PRIZE_PAYOUT", 1000 * const.LUNA_PER_NIM))
        if is_prizedraw
        else int(getattr(const, "MIN_STANDARD_CLAIM_PAYOUT", 100 * const.LUNA_PER_NIM))
    )
    minimum_payout_ok = total_value >= minimum_payout * payout_divisor
    fully_funded = total_value > 0 and confirmed_deposit_amount >= total_value

    publish_block_reason = None
    publish_block_message = None
    if fully_funded and draft_start_time_past:
        publish_block_reason = "start_time_past"
        publish_block_message = "Change the start time before publishing."
    elif fully_funded and not minimum_payout_ok:
        publish_block_reason = "minimum_payout_too_low"
        kind = "prize" if is_prizedraw else "claim"
        minimum_nim = int(minimum_payout / const.LUNA_PER_NIM)
        publish_block_message = f"Per {kind} payout must be at least {minimum_nim} NIM."

    return {
        "id": int(spot[schema.SPOT_ID]),
        "link": spot.get(schema.SPOT_LINK),
        "title": spot.get(schema.SPOT_TITLE) or "NimHunt Spot",
        "description": spot.get(schema.SPOT_DESC),
        "city": spot.get(schema.SPOT_CITY),
        "country": spot.get(schema.SPOT_COUNTRY),
        "lat": _optional_float(lat_value),
        "long": _optional_float(long_value),
        "radius": int(spot.get(schema.SPOT_RADIUS) or 25),
        "claim_duration": int(spot.get(schema.SPOT_CLAIM_DURATION) or 0),
        "max_claims_per_user": int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 0),
        "max_total_claims": int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) if spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) is not None else 1),
        "total_value": total_value,
        "starts_at": spot.get(schema.SPOT_STARTS_AT),
        "ends_at": _spot_absolute_ends_at(spot),
        "ends_after": spot.get(schema.SPOT_ENDS_AT),
        "use_password": bool(int(spot.get(schema.SPOT_USE_PASSWORD) or 0)),
        "created_at": spot.get(schema.SPOT_CREATED_AT),
        "updated_at": spot.get(schema.SPOT_UPDATED_AT),
        "status_code": int(spot[schema.SPOT_STATUS]),
        "status_label": status_label,
        "bucket": bucket,
        "is_prizedraw": is_prizedraw,
        "prize_count": spot.get(schema.PRIZEDRAW_PRIZE_COUNT),
        "claim_count": int(spot.get("claim_count") or 0),
        "pending_claim_count": int(spot.get("pending_claim_count") or 0),
        "success_claim_count": int(spot.get("success_claim_count") or 0),
        "failed_claim_count": int(spot.get("failed_claim_count") or 0),
        "claim_code_count": int(spot.get("claim_code_count") or 0),
        "unused_code_count": int(spot.get("unused_code_count") or 0),
        "used_code_count": int(spot.get("used_code_count") or 0),
        "report_count": int(spot.get("report_count") or 0),
        "pending_report_count": int(spot.get("pending_report_count") or 0),
        "trans_count": int(spot.get("trans_count") or 0),
        "trans_total_amount": int(spot.get("trans_total_amount") or 0),
        "deposit": deposit,
        "total_value_locked": bool(deposit.get("has_submitted")),
        "can_edit": status_label == "draft",
        "can_deposit": (
            status_label == "draft"
            and total_value > 0
            and int(deposit.get("pending_amount") or 0) <= 0
            and int(deposit.get("amount_due") or 0) > 0
        ),
        "can_publish": (
            status_label == "draft"
            and fully_funded
            and not draft_start_time_past
            and minimum_payout_ok
        ),
        "publish_block_reason": publish_block_reason,
        "publish_block_message": publish_block_message,
        "can_cancel": (
            int(spot[schema.SPOT_STATUS]) == const.SPOT_STATUS_PUBLISHED
            and not is_prizedraw
        ),
        "cancellation": cancellation,
        "edit_href": f"{const.CREATE_SPOT_URL}/{int(spot[schema.SPOT_ID])}",
        "href": (
            f"{const.CREATE_SPOT_URL}/{int(spot[schema.SPOT_ID])}"
            if status_label == "draft"
            else _spot_detail_href(spot)
        ),
    }


async def _identify_private_page_user(
    db,
    payload: HomeSessionRequest,
) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    """Identify the current USER for private/self pages such as My Spots.

    The response metadata mirrors /api/home/session enough for page JS to show
    the same wallet/test-user/banned notices.
    """
    language = _clean_language(payload.language)

    if not payload.wallet_available or not _valid_device_id_hash(payload.device_id_hash):
        if bool(getattr(const, "DEFAULT_TO_TEST_USER", False)):
            test_user_id = int(getattr(const, "TEST_USER_ID", 0))
            user = await db_access.get_user_by_id(db, user_id=test_user_id)
            if user is None:
                return None, {
                    "ok": False,
                    "code": "test_user_missing",
                    "message": (
                        f"DEFAULT_TO_TEST_USER is enabled, but TEST_USER_ID={test_user_id} "
                        "does not exist. Run spoof.py to create the mock data."
                    ),
                    "user": None,
                    "test_user": True,
                    "language": language,
                }, status.HTTP_500_INTERNAL_SERVER_ERROR

            await db_access.touch_user_last_seen(db, user_id=test_user_id)
            user = await db_access.get_user_by_id(db, user_id=test_user_id)
            return user, {
                "ok": True,
                "code": "test_user",
                "message": f"Using desktop test user {test_user_id}.",
                "test_user": True,
                "language": language,
            }, status.HTTP_200_OK

        return None, {
            "ok": False,
            "code": "wallet_unavailable",
            "message": f"Open {const.APP_NAME} inside Nimiq Pay to identify this device.",
            "user": None,
            "test_user": False,
            "language": language,
        }, status.HTTP_200_OK

    assert payload.device_id_hash is not None
    device_id_hash = payload.device_id_hash.strip().lower()
    user_id, created = await db_access.get_or_create_user(db, device_id_hash=device_id_hash)
    await db_access.touch_user_last_seen(db, user_id=user_id)
    user = await db_access.get_user_by_id(db, user_id=user_id)

    if user is None:
        return None, {
            "ok": False,
            "code": "user_missing",
            "message": "User could not be loaded after creation.",
            "user": None,
            "test_user": False,
            "language": language,
        }, status.HTTP_500_INTERNAL_SERVER_ERROR

    return user, {
        "ok": True,
        "code": "ok",
        "message": "User ready.",
        "created": bool(created),
        "test_user": False,
        "language": language,
    }, status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Browser icon route
# ---------------------------------------------------------------------------

@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    """Serve the app icon at the browser's default favicon path.

    Browsers often request /favicon.ico even when the page links to a
    different icon path. Serving this route prevents harmless 404 noise in
    the Uvicorn terminal.
    """
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        _shared_template_context(request),
    )


@router.get("/home", response_class=HTMLResponse)
async def home_page_alias(request: Request) -> HTMLResponse:
    return await home_page(request)


def render_not_found_page(request: Request) -> HTMLResponse:
    """Return the branded NimHunt 404 page for missing browser pages."""
    return templates.TemplateResponse(
        request,
        "not_found.html",
        _shared_template_context(request, page_title=f"404 · {const.APP_NAME}"),
        status_code=status.HTTP_404_NOT_FOUND,
    )


@router.get("/spots", response_class=HTMLResponse)
async def find_spots_page(request: Request) -> HTMLResponse:
    context = _shared_template_context(request, page_title=f"Find Spots · {const.APP_NAME}")
    context.update(
        {
            "leaflet_css_url": const.LEAFLET_CSS_URL,
            "leaflet_js_url": const.LEAFLET_JS_URL,
            "map_tile_url": const.MAP_TILE_URL,
            "map_tile_attribution": const.MAP_TILE_ATTRIBUTION,
            "max_map_init_spots": const.MAX_MAP_INIT_SPOTS,
            "max_map_zoom_out": const.MAX_MAP_ZOOM_OUT,
            "create_spot_url": const.CREATE_SPOT_URL,
            "report_details_max": const.REPORT_DETAILS_MAX_CHARS,
            "claim_captcha_min": int(getattr(const, "CLAIM_CAPTCHA_MIN", 1)),
            "claim_captcha_max": int(getattr(const, "CLAIM_CAPTCHA_MAX", 9)),
        }
    )
    return templates.TemplateResponse(request, "find_spots.html", context)


@router.get("/my-spots", response_class=HTMLResponse)
async def my_spots_page(request: Request) -> HTMLResponse:
    context = _shared_template_context(request, page_title=f"My Spots · {const.APP_NAME}")
    context.update(
        {
            "leaflet_css_url": const.LEAFLET_CSS_URL,
            "leaflet_js_url": const.LEAFLET_JS_URL,
            "map_tile_url": const.MAP_TILE_URL,
            "map_tile_attribution": const.MAP_TILE_ATTRIBUTION,
            "create_spot_url": const.CREATE_SPOT_URL,
        }
    )
    return templates.TemplateResponse(request, "my_spots.html", context)


@router.get("/my-claims", response_class=HTMLResponse)
async def my_claims_page(request: Request) -> HTMLResponse:
    context = _shared_template_context(request, page_title=f"My Claims · {const.APP_NAME}")
    context.update(
        {
            "leaflet_css_url": const.LEAFLET_CSS_URL,
            "leaflet_js_url": const.LEAFLET_JS_URL,
            "map_tile_url": const.MAP_TILE_URL,
            "map_tile_attribution": const.MAP_TILE_ATTRIBUTION,
        }
    )
    return templates.TemplateResponse(request, "my_claims.html", context)


@router.get("/my-history")
async def my_history_redirect() -> RedirectResponse:
    return RedirectResponse(url="/my-claims", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/create", response_class=HTMLResponse)
async def create_spot_start_page() -> RedirectResponse:
    """Send generic Create Spot links to My Spots, where the start modal lives."""
    return RedirectResponse(url="/my-spots?create=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/create-spot", include_in_schema=False)
async def create_spot_start_legacy_redirect() -> RedirectResponse:
    """Redirect old Create Spot page links to the shorter public URL."""
    return RedirectResponse(url=const.CREATE_SPOT_URL, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/create/{spot_id}", response_class=HTMLResponse)
async def create_spot_full_form_page(request: Request, spot_id: int) -> HTMLResponse:
    """Full Create Spot form shell.

    The browser must identify the Nimiq Pay device before the app can know who
    is viewing the page. The JavaScript therefore performs the owner check
    immediately through /api/create-spot/{spot_id}/detail and redirects home if
    the current device did not create this SPOT.
    """
    async with get_db() as db:
        spot = await db_access.get_spot_owner_summary(db, spot_id=spot_id)

    if spot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Spot not found")

    context = _shared_template_context(request, page_title=f"Create Spot · {const.APP_NAME}")
    context.update(
        {
            "spot_id": int(spot_id),
            "leaflet_css_url": const.LEAFLET_CSS_URL,
            "leaflet_js_url": const.LEAFLET_JS_URL,
            "map_tile_url": const.MAP_TILE_URL,
            "map_tile_attribution": const.MAP_TILE_ATTRIBUTION,
            "spot_title_min": const.SPOT_TITLE_MIN_CHARS,
            "spot_title_max": const.SPOT_TITLE_MAX_CHARS,
            "min_spot_radius_metres": const.MIN_SPOT_RADIUS_METRES,
            "max_spot_radius_metres": const.MAX_SPOT_RADIUS_METRES,
            "min_claim_duration_seconds": const.MIN_SPOT_CLAIM_DURATION_SECONDS,
            "min_nonzero_claim_duration_seconds": const.MIN_SPOT_NONZERO_CLAIM_DURATION_SECONDS,
            "max_claim_duration_seconds": const.MAX_SPOT_CLAIM_DURATION_SECONDS,
            "min_max_claims_per_user": const.MIN_SPOT_MAX_CLAIMS_PER_USER,
            "max_max_claims_per_user": const.MAX_SPOT_MAX_CLAIMS_PER_USER,
            "min_prizedraw_total_participants": const.MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS,
            "min_standard_total_participants": const.MIN_SPOT_MAX_TOTAL_CLAIMS,
            "max_total_participants": const.MAX_SPOT_MAX_TOTAL_CLAIMS,
            "min_total_nim": const.MIN_SPOT_TOTAL_VALUE_NIM,
            "min_standard_claim_payout_nim": int(getattr(const, "MIN_STANDARD_CLAIM_PAYOUT_NIM", 100)),
            "min_prizedraw_prize_payout_nim": int(getattr(const, "MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM", 1000)),
            "min_ends_after_seconds": const.MIN_SPOT_ENDS_AFTER_SECONDS,
            "max_ends_after_seconds": const.MAX_SPOT_ENDS_AFTER_SECONDS,
            "default_ends_after_seconds": const.DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS,
            "prizedraw_prize_count_options": ",".join(str(v) for v in const.PRIZEDRAW_PRIZE_COUNT_OPTIONS),
        }
    )
    return templates.TemplateResponse(request, "create_spot.html", context)


@router.get("/create-spot/{spot_id}", include_in_schema=False)
async def create_spot_full_form_legacy_redirect(spot_id: int) -> RedirectResponse:
    """Redirect old draft-edit links to the shorter public Create URL."""
    return RedirectResponse(
        url=f"{const.CREATE_SPOT_URL}/{int(spot_id)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/spot/{spot_ref}", response_class=HTMLResponse)
async def spot_detail_page(request: Request, spot_ref: str) -> HTMLResponse:
    """Public standalone page for one published, non-expired SPOT."""
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        spot_row = await _get_public_spot_detail_row(db, spot_ref=spot_ref)

    if spot_row is None:
        existing_spot = None
        async with get_db() as db:
            if str(spot_ref).isdigit():
                existing_spot = await db_access.get_spot(db, spot_id=int(spot_ref))
            if existing_spot is None:
                existing_spot = await db_access.get_spot_by_link(db, link=str(spot_ref))

        if existing_spot is not None:
            return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Spot not found")

    spot = _serialise_public_spot_for_detail(spot_row, now=now)
    source = str(request.query_params.get("from") or "").strip().lower()
    if source == "my-spots":
        back_href = "/my-spots"
        back_label = "My Spots"
        back_aria_label = "Back to My Spots"
    else:
        back_href = "/spots"
        back_label = "Find Spots"
        back_aria_label = "Back to Find Spots"

    context = _shared_template_context(request, page_title=f"{spot['title']} · {const.APP_NAME}")
    context.update(
        {
            "spot": spot,
            "spot_json": json.dumps(spot),
            "back_href": back_href,
            "back_label": back_label,
            "back_aria_label": back_aria_label,
            "leaflet_css_url": const.LEAFLET_CSS_URL,
            "leaflet_js_url": const.LEAFLET_JS_URL,
            "map_tile_url": const.MAP_TILE_URL,
            "map_tile_attribution": const.MAP_TILE_ATTRIBUTION,
            "report_details_max": const.REPORT_DETAILS_MAX_CHARS,
        }
    )
    return templates.TemplateResponse(request, "spot.html", context)


@router.get("/claim/{claim_id}", response_class=HTMLResponse)
async def claim_detail_page(request: Request, claim_id: int) -> HTMLResponse:
    """Private-ish shell for one CLAIM detail page. JS identifies the viewer."""
    source = str(request.query_params.get("from") or "").strip().lower()
    if source in {"find-spots", "spots"}:
        back_href = "/spots"
        back_label = "Find Spots"
        back_aria_label = "Back to Find Spots"
    elif source == "my-spots":
        back_href = "/my-spots"
        back_label = "My Spots"
        back_aria_label = "Back to My Spots"
    else:
        back_href = "/my-claims"
        back_label = "My Claims"
        back_aria_label = "Back to My Claims"

    context = _shared_template_context(request, page_title=f"Claim · {const.APP_NAME}")
    context.update(
        {
            "claim_id": int(claim_id),
            "back_href": back_href,
            "back_label": back_label,
            "back_aria_label": back_aria_label,
            "leaflet_css_url": const.LEAFLET_CSS_URL,
            "leaflet_js_url": const.LEAFLET_JS_URL,
            "map_tile_url": const.MAP_TILE_URL,
            "map_tile_attribution": const.MAP_TILE_ATTRIBUTION,
        }
    )
    return templates.TemplateResponse(request, "claim.html", context)


def _claim_status_label(status_code: int | None) -> str:
    if status_code == const.CLAIM_STATUS_PENDING:
        return "pending"
    if status_code == const.CLAIM_STATUS_SUCCESS:
        return "success"
    if status_code == const.CLAIM_STATUS_FAILED:
        return "failed"
    return "unknown"


def _claim_reward_amount(spot: dict[str, Any], *, is_prizedraw: bool) -> int:
    total_value = int(spot.get(schema.SPOT_TOTAL_VALUE) or 0)
    if is_prizedraw:
        divisor = max(1, int(spot.get(schema.PRIZEDRAW_PRIZE_COUNT) or 1))
    else:
        divisor = max(1, int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 1))
    return int(total_value / divisor)


def _claim_payout_summary(claim_transactions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summarise non-failed CLAIM payout transactions for one claim.

    Prizedraw win/loss is intentionally inferred from these rows. A successful
    Prizedraw claim with a non-failed CLAIM transaction is a winner; a
    successful Prizedraw claim on a completed Spot without one is a losing entry.
    """
    payout_rows = [
        trans for trans in (claim_transactions or [])
        if int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CLAIM
        and int(trans.get(schema.TRANS_STATUS) or -1) != const.TRANS_STATUS_FAILED
    ]
    confirmed_rows = [
        trans for trans in payout_rows
        if int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    ]
    pending_rows = [
        trans for trans in payout_rows
        if int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_PENDING
    ]
    return {
        "has_payout": bool(payout_rows),
        "payout_count": len(payout_rows),
        "payout_confirmed_count": len(confirmed_rows),
        "payout_pending_count": len(pending_rows),
        "payout_amount": sum(int(trans.get(schema.TRANS_AMOUNT) or 0) for trans in payout_rows),
    }


def _claim_display_status(
    *,
    claim: dict[str, Any],
    spot: dict[str, Any],
    is_prizedraw: bool,
    status_label: str,
    payout: dict[str, Any],
) -> dict[str, str]:
    """Return the user-facing claim status text and colour class."""
    if not is_prizedraw:
        if status_label == "success":
            return {"label": "success", "text": "Success", "class": "success"}
        if status_label == "failed":
            return {"label": "failed", "text": "Failed", "class": "failed"}
        return {"label": "pending", "text": "Pending", "class": "pending"}

    # For Prizedraws, SUCCESS means a valid entry. Win/loss is only shown once
    # the Spot has been settled/completed. Pending and failed claims never show
    # Won/Lost.
    if status_label == "success":
        draw_settled = int(spot.get(schema.SPOT_STATUS) or -1) == const.SPOT_STATUS_COMPLETED
        if draw_settled:
            if bool(payout.get("has_payout")):
                return {"label": "won", "text": "Won!", "class": "success"}
            return {"label": "lost", "text": "Lost", "class": "failed"}
        return {"label": "waiting", "text": "Waiting", "class": "pending"}

    if status_label == "failed":
        return {"label": "failed", "text": "Failed", "class": "failed"}

    return {"label": "pending", "text": "Pending", "class": "pending"}


def _serialise_claim_detail(
    claim: dict[str, Any],
    spot: dict[str, Any],
    *,
    now: int,
    viewer_user_id: int | None = None,
    claim_transactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    is_prizedraw = _spot_is_prizedraw_row(spot)
    duration = int(spot.get(schema.SPOT_CLAIM_DURATION) or 0)
    claimed_at = int(claim.get(schema.CLAIM_CLAIMED_AT) or now)
    elapsed = max(0, int(now) - claimed_at)
    capped_elapsed = min(elapsed, duration) if duration > 0 else elapsed
    remaining = max(0, duration - elapsed) if duration > 0 else 0
    status_label = _claim_status_label(int(claim.get(schema.CLAIM_STATUS) or const.CLAIM_STATUS_PENDING))
    payout = _claim_payout_summary(claim_transactions)
    display_status = _claim_display_status(
        claim=claim,
        spot=spot,
        is_prizedraw=is_prizedraw,
        status_label=status_label,
        payout=payout,
    )
    link = spot.get(schema.SPOT_LINK)
    spot_id = int(spot[schema.SPOT_ID])

    return {
        "id": int(claim[schema.CLAIM_ID]),
        "spot_id": spot_id,
        "recipient": int(claim[schema.CLAIM_RECIPIENT]),
        "lat": float(claim[schema.CLAIM_LAT]),
        "long": float(claim[schema.CLAIM_LONG]),
        "accuracy": float(claim.get(schema.CLAIM_ACCURACY) or 0),
        "claimed_at": claimed_at,
        "updated_at": claim.get(schema.CLAIM_UPDATED_AT),
        "status_code": int(claim.get(schema.CLAIM_STATUS) or const.CLAIM_STATUS_PENDING),
        "status_label": status_label,
        "display_status_label": display_status["label"],
        "display_status_text": display_status["text"],
        "display_status_class": display_status["class"],
        "href": f"{getattr(const, 'CLAIM_PAGE_URL_PREFIX', '/claim')}/{int(claim[schema.CLAIM_ID])}",
        "claim_code_id": claim.get("claim_code_id"),
        "claim_code_used": bool(claim.get("claim_code_id") is not None),
        "spot": {
            "id": spot_id,
            "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),
            "link": link,
            "title": spot.get(schema.SPOT_TITLE) or "NimHunt Spot",
            "description": spot.get(schema.SPOT_DESC),
            "city": spot.get(schema.SPOT_CITY),
            "country": spot.get(schema.SPOT_COUNTRY),
            "lat": float(spot[schema.SPOT_LAT]),
            "long": float(spot[schema.SPOT_LONG]),
            "radius": int(spot.get(schema.SPOT_RADIUS) or 25),
            "claim_duration": duration,
            "use_password": bool(int(spot.get(schema.SPOT_USE_PASSWORD) or 0)),
            "max_claims_per_user": int(spot.get(schema.SPOT_MAX_CLAIMS_PER_USER) or 1),
            "max_total_claims": int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) if spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) is not None else 1),
            "total_value": int(spot.get(schema.SPOT_TOTAL_VALUE) or 0),
            "starts_at": spot.get(schema.SPOT_STARTS_AT),
            "ends_at": _spot_absolute_ends_at(spot),
            "is_prizedraw": is_prizedraw,
            "prize_count": spot.get(schema.PRIZEDRAW_PRIZE_COUNT),
            "claim_count": int(spot.get("claim_count") or 0),
            "pending_claim_count": int(spot.get("pending_claim_count") or 0),
            "success_claim_count": int(spot.get("success_claim_count") or 0),
            "status_code": int(spot.get(schema.SPOT_STATUS) or 0),
            "is_completed": int(spot.get(schema.SPOT_STATUS) or -1) == const.SPOT_STATUS_COMPLETED,
            "href": f"{const.SPOT_PAGE_URL_PREFIX}/{link or spot_id}",
        },
        "reward_amount": _claim_reward_amount(spot, is_prizedraw=is_prizedraw),
        "is_prizedraw": is_prizedraw,
        "duration_required": duration,
        "duration_elapsed": capped_elapsed,
        "duration_elapsed_raw": elapsed,
        "duration_remaining": remaining,
        "duration_score": float(claim.get(schema.CLAIM_ACCURACY) or 0),
        "location_monitoring_required": bool(duration > 0 and status_label == "pending" and remaining > 0),
        "location_check_interval": int(getattr(const, "CLAIM_LOCATION_CHECK_INTERVAL_SECONDS", 60)),
        "location_stale_after": int(getattr(const, "CLAIM_LOCATION_STALE_AFTER_SECONDS", 180)),
        "location_max_accuracy_margin": int(getattr(const, "CLAIM_LOCATION_MAX_ACCURACY_MARGIN_METRES", 50)),
        "payout_transaction_count": int(payout["payout_count"]),
        "payout_pending_count": int(payout["payout_pending_count"]),
        "payout_confirmed_count": int(payout["payout_confirmed_count"]),
        "payout_amount": int(payout["payout_amount"]),
        "progress_label": display_status["text"] if is_prizedraw else None,
        "viewer_is_owner": viewer_user_id is not None and int(spot.get(schema.SPOT_CREATED_BY) or -1) == int(viewer_user_id),
        "viewer_is_recipient": viewer_user_id is not None and int(claim[schema.CLAIM_RECIPIENT]) == int(viewer_user_id),
    }


def _serialise_owner_claim_code(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one claim-code/password row for the spot creator only."""
    used_by = row.get(schema.CLAIM_CODE_USED_BY)
    return {
        "id": int(row[schema.CLAIM_CODE_ID]),
        "code": row.get(schema.CLAIM_CODE_CODE),
        "used": used_by is not None,
        "used_by_claim_id": int(used_by) if used_by is not None else None,
        "recipient_id": (
            int(row[schema.CLAIM_RECIPIENT])
            if row.get(schema.CLAIM_RECIPIENT) is not None
            else None
        ),
        "recipient_display_name": row.get("recipient_display_name"),
        "claimed_at": row.get(schema.CLAIM_CLAIMED_AT),
    }


@router.post("/api/spot/{spot_id}/claim-codes")
async def spot_claim_codes_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Return generated claim codes to the SPOT creator only."""
    async with get_db() as db:
        user, meta, status_code = await _identify_private_page_user(db, payload)
        if user is None:
            return JSONResponse(meta, status_code=status_code)

        spot = await db_access.get_spot(db, spot_id=int(spot_id))
        if spot is None:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "spot_missing",
                    "message": "This spot could not be found.",
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )

        user_id = int(user[schema.USER_ID])
        if int(spot[schema.SPOT_CREATED_BY]) != user_id:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "not_owner",
                    "message": "Claim codes are only visible to the spot creator.",
                    "user": _public_user(user),
                    "claim_codes": [],
                },
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if int(spot.get(schema.SPOT_USE_PASSWORD) or 0) != 1:
            return JSONResponse(
                {
                    **meta,
                    "ok": True,
                    "user": _public_user(user),
                    "claim_codes": [],
                }
            )

        rows = await db_access.get_claim_codes(db, spot_id=int(spot_id))
        rows = sorted(
            rows,
            key=lambda row: (
                0 if row.get(schema.CLAIM_CODE_USED_BY) is not None else 1,
                int(row.get(schema.CLAIM_CLAIMED_AT) or 0),
                int(row.get(schema.CLAIM_CODE_ID) or 0),
            ),
        )

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "claim_codes": [_serialise_owner_claim_code(row) for row in rows],
        }
    )


def _claim_action_label_for_spot(spot: dict[str, Any], *, allowed: bool) -> str:
    if not allowed:
        return "unavailable"
    if _spot_has_prizedraw(spot):
        return "enter"
    if int(spot.get(schema.SPOT_CLAIM_DURATION) or 0) > 0:
        return "begin"
    return "claim"


def _claim_kind_for_spot(spot: dict[str, Any], *, allowed: bool) -> str:
    if not allowed:
        return "unavailable"
    if _spot_has_prizedraw(spot):
        return "prizedraw"
    if int(spot.get(schema.SPOT_CLAIM_DURATION) or 0) > 0 or bool(int(spot.get(schema.SPOT_USE_PASSWORD) or 0)):
        return "conditional"
    return "standard"


@router.post("/api/spots/claim-status")
async def spots_claim_status_api(payload: ClaimStatusRequest) -> JSONResponse:
    """Return current-user claim state for visible Find Spots entries."""
    ids = [int(v) for v in payload.spot_ids[:500] if int(v) > 0]
    if not ids:
        return JSONResponse({"ok": True, "statuses": {}})

    async with get_db() as db:
        user, meta, http_status = await _identify_private_page_user(db, payload)
        if user is None:
            return JSONResponse({**meta, "statuses": {}}, status_code=http_status)

        if payload.lat is None or payload.long is None:
            return JSONResponse({**meta, "ok": True, "user": _public_user(user), "statuses": {}})

        now = await db_access.get_unixepoch(db)
        statuses: dict[str, Any] = {}
        for spot_id in ids:
            spot = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
            if spot is None:
                continue
            rule = await db_access.get_claim_rule_check(
                db,
                spot_id=spot_id,
                user_id=int(user[schema.USER_ID]),
                lat=float(payload.lat),
                long=float(payload.long),
                location_accuracy_metres=payload.accuracy,
            )
            allowed = bool(rule.get("allowed"))
            is_prizedraw = _spot_is_prizedraw_row(spot)
            counted_claims = int(spot.get("success_claim_count") or 0)
            if is_prizedraw:
                counted_claims += int(spot.get("pending_claim_count") or 0)
            max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) if spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) is not None else 1)
            reward_amount = _claim_reward_amount(spot, is_prizedraw=is_prizedraw)
            statuses[str(spot_id)] = {
                "allowed": allowed,
                "reason": rule.get("reason"),
                "message": rule.get("message"),
                "action": _claim_action_label_for_spot(spot, allowed=allowed),
                "kind": _claim_kind_for_spot(spot, allowed=allowed),
                "within_radius": bool(rule.get("within_radius")),
                "requires_password": bool(int(spot.get(schema.SPOT_USE_PASSWORD) or 0)),
                "requires_duration": int(spot.get(schema.SPOT_CLAIM_DURATION) or 0) > 0,
                "is_prizedraw": is_prizedraw,
                "reward_amount": reward_amount,
                "participant_count": counted_claims,
                "max_participants": max_total,
                "prize_count": spot.get(schema.PRIZEDRAW_PRIZE_COUNT),
                "distance": rule.get("distance"),
                "user_ok": bool(rule.get("user_ok")),
                "own_spot": bool(rule.get("own_spot")),
                "spot_current": bool(rule.get("spot_current")),
                "capacity_ok": bool(rule.get("capacity_ok")),
                "user_limit_ok": bool(rule.get("user_limit_ok")),
            }

    return JSONResponse({**meta, "ok": True, "user": _public_user(user), "statuses": statuses, "now": now})


@router.post("/api/spot/{spot_id}/claim")
async def claim_spot_api(spot_id: int, payload: ClaimSpotRequest) -> JSONResponse:
    """Start a CLAIM or Prizedraw entry for the current user."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            if payload.lat is None or payload.long is None:
                return JSONResponse(
                    {**meta, "ok": False, "code": "location_missing", "message": "Your location is required before claiming."},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            spot = await db_access.get_spot(db, spot_id=int(spot_id))
            if spot is None:
                return JSONResponse({**meta, "ok": False, "code": "spot_missing", "message": "Spot not found."}, status_code=status.HTTP_404_NOT_FOUND)

            requires_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
            if requires_password:
                if payload.captcha_a is None or payload.captcha_b is None or payload.captcha_answer is None:
                    return JSONResponse({**meta, "ok": False, "code": "captcha_required", "message": "Complete the captcha before claiming."}, status_code=status.HTTP_400_BAD_REQUEST)
                if int(payload.captcha_a) + int(payload.captcha_b) != int(payload.captcha_answer):
                    return JSONResponse({**meta, "ok": False, "code": "captcha_failed", "message": "The captcha answer was incorrect."}, status_code=status.HTTP_400_BAD_REQUEST)

            try:
                claim = await db_access.create_claim_attempt(
                    db,
                    spot_id=int(spot_id),
                    user_id=int(user[schema.USER_ID]),
                    lat=float(payload.lat),
                    long=float(payload.long),
                    location_accuracy_metres=payload.accuracy,
                    claim_code=payload.claim_code,
                    payout_address=payload.payout_address,
                )
            except ValueError as exc:
                code = "invalid_claim_code" if "claim code" in str(exc).lower() else "claim_failed"
                return JSONResponse({**meta, "ok": False, "code": code, "message": str(exc)}, status_code=status.HTTP_409_CONFLICT)

        await _notify_all_cache_for_spot_owner_change(
            db,
            user_id=int(user[schema.USER_ID]),
            spot_id=int(spot_id),
        )

        if settlement_updater is not None:
            await settlement_updater.settle_prizedraw_spot_if_ready(spot_id=int(spot_id))

        refreshed_claim = await db_access.get_claim(db, claim_id=int(claim[schema.CLAIM_ID]))
        claim_transactions = await db_access.get_transactions_by_claim(db, claim_id=int(claim[schema.CLAIM_ID]))
        spot_detail = await db_access.get_spot_owner_summary(db, spot_id=int(spot_id))
        now = await db_access.get_unixepoch(db)

    success_now = refreshed_claim is not None and int(refreshed_claim[schema.CLAIM_STATUS]) == const.CLAIM_STATUS_SUCCESS
    claim_url = f"{getattr(const, 'CLAIM_PAGE_URL_PREFIX', '/claim')}/{int(claim[schema.CLAIM_ID])}"
    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "claim": _serialise_claim_detail(
                refreshed_claim or claim,
                spot_detail or spot,
                now=now,
                viewer_user_id=int(user[schema.USER_ID]),
                claim_transactions=claim_transactions,
            ),
            "claim_url": claim_url,
            "success_now": success_now,
        }
    )


@router.post("/api/claim/{claim_id}/detail")
async def claim_detail_api(claim_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Return one CLAIM to its recipient or the SPOT creator."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            claim = await db_access.refresh_claim_status_from_conditions(db, claim_id=int(claim_id))
            if claim is None:
                return JSONResponse({**meta, "ok": False, "code": "claim_missing", "message": "Claim not found."}, status_code=status.HTTP_404_NOT_FOUND)

            spot = await db_access.get_spot_owner_summary(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
            if spot is None:
                return JSONResponse({**meta, "ok": False, "code": "spot_missing", "message": "Spot not found."}, status_code=status.HTTP_404_NOT_FOUND)

            user_id = int(user[schema.USER_ID])
            can_view = user_id == int(claim[schema.CLAIM_RECIPIENT]) or user_id == int(spot[schema.SPOT_CREATED_BY])
            if not can_view:
                return JSONResponse({**meta, "ok": False, "code": "not_allowed", "message": "This claim is not visible to this device account."}, status_code=status.HTTP_403_FORBIDDEN)

        claim_transactions = await db_access.get_transactions_by_claim(db, claim_id=int(claim_id))
        now = await db_access.get_unixepoch(db)
        await _notify_all_cache_for_spot_owner_change(db, user_id=user_id, spot_id=int(claim[schema.CLAIM_SPOT_ID]))

    return JSONResponse({
        **meta,
        "ok": True,
        "user": _public_user(user),
        "claim": _serialise_claim_detail(
            claim,
            spot,
            now=now,
            viewer_user_id=user_id,
            claim_transactions=claim_transactions,
        ),
    })


@router.post("/api/claim/{claim_id}/location")
async def claim_location_heartbeat_api(claim_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Record one fresh location ping for a pending duration-based claim."""
    if payload.lat is None or payload.long is None:
        return JSONResponse(
            {"ok": False, "code": "location_missing", "message": "A fresh location is required for this claim."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            try:
                claim = await db_access.process_duration_claim_location_heartbeat(
                    db,
                    claim_id=int(claim_id),
                    user_id=user_id,
                    lat=float(payload.lat),
                    long=float(payload.long),
                    location_accuracy_metres=payload.accuracy,
                )
            except PermissionError as exc:
                return JSONResponse({**meta, "ok": False, "code": "not_allowed", "message": str(exc)}, status_code=status.HTTP_403_FORBIDDEN)
            except ValueError as exc:
                return JSONResponse({**meta, "ok": False, "code": "claim_location_failed", "message": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)

            spot = await db_access.get_spot_owner_summary(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
            if spot is None:
                return JSONResponse({**meta, "ok": False, "code": "spot_missing", "message": "Spot not found."}, status_code=status.HTTP_404_NOT_FOUND)

        await _notify_all_cache_for_spot_owner_change(db, user_id=user_id, spot_id=int(claim[schema.CLAIM_SPOT_ID]))

    if settlement_updater is not None and int(claim.get(schema.CLAIM_STATUS) or const.CLAIM_STATUS_PENDING) == const.CLAIM_STATUS_SUCCESS:
        await settlement_updater.settle_prizedraw_spot_if_ready(spot_id=int(claim[schema.CLAIM_SPOT_ID]))

    async with get_db() as db:
        refreshed_claim = await db_access.get_claim(db, claim_id=int(claim_id)) or claim
        refreshed_spot = await db_access.get_spot_owner_summary(db, spot_id=int(refreshed_claim[schema.CLAIM_SPOT_ID])) or spot
        claim_transactions = await db_access.get_transactions_by_claim(db, claim_id=int(claim_id))
        now = await db_access.get_unixepoch(db)

    return JSONResponse({
        **meta,
        "ok": True,
        "user": _public_user(user),
        "claim": _serialise_claim_detail(
            refreshed_claim,
            refreshed_spot,
            now=now,
            viewer_user_id=user_id,
            claim_transactions=claim_transactions,
        ),
    })


@router.post("/api/spot/{spot_id}/report-status")
async def report_spot_status_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Return whether the current user may report this SPOT."""
    async with get_db() as db:
        user, meta, status_code = await _identify_private_page_user(db, payload)
        if user is None:
            return JSONResponse(meta, status_code=status_code)

        public_spot = await db_access.get_spot(db, spot_id=int(spot_id))
        if public_spot is None:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "spot_missing",
                    "message": "This spot could not be found.",
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )

        user_id = int(user[schema.USER_ID])
        is_owner = int(public_spot[schema.SPOT_CREATED_BY]) == user_id
        already_reported = await db_access.has_user_reported_spot(
            db,
            spot_id=int(spot_id),
            user_id=user_id,
        )

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "spot_id": int(spot_id),
            "is_owner": is_owner,
            "already_reported": bool(already_reported),
            "can_report": (
                not is_owner
                and not bool(already_reported)
                and int(user[schema.USER_STATUS]) != const.USER_STATUS_BANNED
            ),
        }
    )


@router.post("/api/spot/{spot_id}/report")
async def report_spot_api(spot_id: int, payload: ReportSpotRequest) -> JSONResponse:
    """Create one pending REPORT for a public SPOT."""
    if int(payload.captcha_answer) != int(payload.captcha_a) + int(payload.captcha_b):
        return JSONResponse(
            {
                "ok": False,
                "code": "captcha_failed",
                "message": "The captcha answer was not correct.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async with get_db() as db:
        user, meta, status_code = await _identify_private_page_user(db, payload)
        if user is None:
            return JSONResponse(meta, status_code=status_code)

        if int(user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "banned",
                    "message": f"This device account can no longer use {const.APP_NAME}.",
                    "user": _public_user(user),
                },
                status_code=status.HTTP_403_FORBIDDEN,
            )

        public_spot = await db_access.get_spot(db, spot_id=int(spot_id))
        if public_spot is None:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "spot_missing",
                    "message": "This spot could not be found.",
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )

        user_id = int(user[schema.USER_ID])
        if int(public_spot[schema.SPOT_CREATED_BY]) == user_id:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "own_spot",
                    "message": "You cannot report your own spot.",
                    "user": _public_user(user),
                },
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if await db_access.has_user_reported_spot(db, spot_id=int(spot_id), user_id=user_id):
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "already_reported",
                    "message": "You have already reported this spot.",
                    "user": _public_user(user),
                },
                status_code=status.HTTP_409_CONFLICT,
            )

        try:
            async with db_access.transaction(db):
                report_id = await db_access.create_report(
                    db,
                    spot_id=int(spot_id),
                    user_id=user_id,
                    reason=int(payload.reason),
                    details=payload.details,
                )
        except ValueError as e:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "report_failed",
                    "message": str(e),
                    "user": _public_user(user),
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await _notify_all_cache_for_spot_owner_change(
            db,
            user_id=int(public_spot[schema.SPOT_CREATED_BY]),
            spot_id=int(spot_id),
        )

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "code": "reported",
            "message": "Thank you. Your report has been submitted.",
            "report_id": report_id,
            "user": _public_user(user),
        }
    )


@router.get("/api/spots/initial")
async def initial_spots_for_map(
    lat: float | None = Query(default=None, ge=-90, le=90),
    long: float | None = Query(default=None, ge=-180, le=180),
    include_active: bool = True,
    include_upcoming: bool = True,
    include_prizedraws: bool = True,
) -> JSONResponse:
    """Return the nearest public spots for deciding the initial map view."""
    if not include_active and not include_upcoming:
        return JSONResponse({"ok": True, "spots": [], "config": _map_config_payload()})

    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        if cache is not None and hasattr(cache, "get_all_public_spots"):
            raw_items = await cache.get_all_public_spots(db, limit=500)
        else:
            raw_items = await db_access.get_public_spots_in_bounds(
                db,
                min_lat=-90,
                max_lat=90,
                min_long=-180,
                max_long=180,
                limit=500,
            )

    items = [
        _serialise_spot_for_map(
            item,
            now=now,
            distance_from_lat=lat,
            distance_from_long=long,
        )
        for item in raw_items
        if _spot_matches_filters(
            item,
            now=now,
            include_active=include_active,
            include_upcoming=include_upcoming,
            include_prizedraws=include_prizedraws,
        )
    ]
    spots = _sort_spots_for_map(items)[: int(const.MAX_MAP_INIT_SPOTS)]
    return JSONResponse({"ok": True, "spots": spots, "config": _map_config_payload()})


@router.get("/api/spots/search")
async def search_spots_for_map(
    min_lat: float = Query(ge=-90, le=90),
    max_lat: float = Query(ge=-90, le=90),
    min_long: float = Query(ge=-180, le=180),
    max_long: float = Query(ge=-180, le=180),
    include_active: bool = True,
    include_upcoming: bool = True,
    include_prizedraws: bool = True,
    distance_lat: float | None = Query(default=None, ge=-90, le=90),
    distance_long: float | None = Query(default=None, ge=-180, le=180),
    limit: int = Query(default=100, ge=1, le=500),
) -> JSONResponse:
    """Return public SPOTs visible inside the current map viewport."""
    if not include_active and not include_upcoming:
        return JSONResponse({"ok": True, "spots": []})

    current_only = include_active and not include_upcoming
    upcoming_only = include_upcoming and not include_active

    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        if cache is not None and hasattr(cache, "get_spots_in_bounds"):
            raw_items = await cache.get_spots_in_bounds(
                db,
                min_lat=min_lat,
                max_lat=max_lat,
                min_long=min_long,
                max_long=max_long,
                current_only=current_only,
                upcoming_only=upcoming_only,
                limit=limit,
            )
        else:
            raw_items = await db_access.get_public_spots_in_bounds(
                db,
                min_lat=min_lat,
                max_lat=max_lat,
                min_long=min_long,
                max_long=max_long,
                current_only=current_only,
                upcoming_only=upcoming_only,
                limit=limit,
            )

    spots = [
        _serialise_spot_for_map(
            item,
            now=now,
            distance_from_lat=distance_lat,
            distance_from_long=distance_long,
        )
        for item in raw_items
        if _spot_matches_filters(
            item,
            now=now,
            include_active=include_active,
            include_upcoming=include_upcoming,
            include_prizedraws=include_prizedraws,
        )
    ]
    return JSONResponse({"ok": True, "spots": _sort_spots_for_map(spots)})


def _map_config_payload() -> dict[str, Any]:
    return {
        "max_map_init_spots": int(const.MAX_MAP_INIT_SPOTS),
        "max_map_zoom_out": int(const.MAX_MAP_ZOOM_OUT),
        "create_spot_url": const.CREATE_SPOT_URL,
    }



# ---------------------------------------------------------------------------
# API routes used by Create Spot
# ---------------------------------------------------------------------------

async def _creator_api_user_or_response(db, payload: HomeSessionRequest) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    user, meta, http_status = await _identify_private_page_user(db, payload)
    if user is None:
        return None, meta, http_status

    public_user = _public_user(user)
    if public_user["is_banned"]:
        return None, {
            **meta,
            "ok": False,
            "code": "banned",
            "message": f"This device account is banned and can no longer use {const.APP_NAME}.",
            "user": public_user,
        }, status.HTTP_403_FORBIDDEN

    if not await db_access.can_user_create_spot(db, user_id=int(user[schema.USER_ID])):
        return None, {
            **meta,
            "ok": False,
            "code": "creator_limited",
            "message": "This device account cannot create spots.",
            "user": public_user,
        }, status.HTTP_403_FORBIDDEN

    return user, meta, status.HTTP_200_OK




# Small, replaceable reverse-geocoding helper for the Create Spot map.
# It tries OpenStreetMap/Nominatim for development convenience, then falls back
# to a tiny local list so the form still behaves predictably offline.
_FALLBACK_PLACES = (
    ("London", "United Kingdom", 51.5074, -0.1278),
    ("Edinburgh", "United Kingdom", 55.9533, -3.1883),
    ("Glasgow", "United Kingdom", 55.8642, -4.2518),
    ("Manchester", "United Kingdom", 53.4808, -2.2426),
    ("Cardiff", "United Kingdom", 51.4816, -3.1791),
    ("Birmingham", "United Kingdom", 52.4862, -1.8904),
    ("Belfast", "United Kingdom", 54.5973, -5.9301),
    ("Newcastle upon Tyne", "United Kingdom", 54.9783, -1.6178),
)


def _fallback_place_for_coordinates(lat: float, long: float) -> tuple[str | None, str | None]:
    best: tuple[float, str, str] | None = None
    for city, country, city_lat, city_long in _FALLBACK_PLACES:
        distance = db_access.distance_metres(lat, long, city_lat, city_long)
        if best is None or distance < best[0]:
            best = (distance, city, country)

    if best is None or best[0] > 75_000:
        return None, None
    return best[1], best[2]


def _reverse_geocode_sync(lat: float, long: float) -> tuple[str | None, str | None]:
    query = urllib.parse.urlencode(
        {
            "format": "jsonv2",
            "lat": f"{float(lat):.7f}",
            "lon": f"{float(long):.7f}",
            "zoom": "10",
            "addressdetails": "1",
        }
    )
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{query}",
        headers={
            "User-Agent": f"{const.APP_NAME}/development-create-spot",
            "Accept": "application/json",
        },
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=6) as response:
        data = json.loads(response.read().decode("utf-8"))

    address = data.get("address") if isinstance(data, dict) else None
    if not isinstance(address, dict):
        return None, None

    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("suburb")
        or address.get("county")
    )
    country = address.get("country")
    return city, country


async def _derive_place_for_create_map(lat: float, long: float) -> tuple[str | None, str | None]:
    try:
        city, country = await asyncio.to_thread(_reverse_geocode_sync, float(lat), float(long))
    except (TimeoutError, urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        city, country = None, None

    if city or country:
        return city, country
    return _fallback_place_for_coordinates(float(lat), float(long))


@router.get("/api/location/reverse")
async def reverse_location_label(
    lat: float = Query(ge=-90, le=90),
    long: float = Query(ge=-180, le=180),
) -> JSONResponse:
    """Return a best-effort city/country label for the Create Spot map centre."""
    city, country = await _derive_place_for_create_map(float(lat), float(long))
    return JSONResponse({"ok": True, "city": city, "country": country})


@router.post("/api/create-spot/{spot_id}/detail")
async def create_spot_form_detail_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Return one creator-owned SPOT for the full Create Spot form.

    If the current device is not the creator, the frontend redirects home.
    """
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _creator_api_user_or_response(db, payload)

        if user is None:
            return JSONResponse(meta, status_code=http_status)

        user_id = int(user[schema.USER_ID])
        if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "not_owner",
                    "message": "This spot was not created by this device account.",
                    "redirect_url": "/",
                },
                status_code=status.HTTP_403_FORBIDDEN,
            )

        spot = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
        if spot is None:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "spot_missing",
                    "message": "Spot not found.",
                    "redirect_url": "/",
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )

        transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
        now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "spot": _serialise_owner_spot(spot, now=now, transactions=transactions),
        }
    )


@router.post("/api/create-spot/draft")
async def create_draft_spot_api(payload: CreateDraftSpotRequest) -> JSONResponse:
    """Create the first-step DRAFT SPOT from title + current creator."""
    if int(payload.captcha_answer) != int(payload.captcha_a) + int(payload.captcha_b):
        return JSONResponse(
            {
                "ok": False,
                "code": "captcha_failed",
                "message": "The captcha answer was not correct.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _creator_api_user_or_response(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            draft_count = await db_access.count_draft_spots_by_user(db, user_id=user_id)
            draft_limit = int(getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3))
            if draft_count >= draft_limit:
                return JSONResponse(
                    {
                        **meta,
                        "ok": False,
                        "code": "draft_limit_reached",
                        "message": f"You already have {draft_count} draft spots. Publish or delete one before creating another.",
                        "user": _public_user(user),
                        "draft_count": draft_count,
                        "draft_limit": draft_limit,
                    },
                    status_code=status.HTTP_409_CONFLICT,
                )

            if payload.is_prizedraw:
                spot_id = await db_access.create_prizedraw(
                    db,
                    created_by=user_id,
                    title=payload.title,
                )
            else:
                spot_id = await db_access.create_spot(
                    db,
                    created_by=user_id,
                    title=payload.title,
                )

        await _notify_user_cache(db, user_id=user_id)
        spot = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
        now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "spot": _serialise_owner_spot(spot, now=now, transactions=[]) if spot else None,
            "edit_url": f"{const.CREATE_SPOT_URL}/{spot_id}",
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.patch("/api/create-spot/{spot_id}")
async def update_draft_spot_api(spot_id: int, payload: UpdateDraftSpotRequest) -> JSONResponse:
    """Update the full Create Spot form, but only while the SPOT is DRAFT."""
    field_names = _payload_field_names(payload)
    update_field_names = {
        "title",
        "description",
        "lat",
        "long",
        "radius",
        "claim_duration",
        "max_claims_per_user",
        "max_total_claims",
        "total_value",
        "starts_at",
        "ends_at",
        "use_password",
        "city",
        "country",
    }
    update_kwargs: dict[str, Any] = {}
    for name in sorted(update_field_names & field_names):
        key = "desc" if name == "description" else name
        update_kwargs[key] = getattr(payload, name)

    prize_count_supplied = "prize_count" in field_names
    prize_count_value = payload.prize_count if prize_count_supplied else None

    async with get_db() as db:
        try:
            async with db_access.transaction(db):
                user, meta, http_status = await _creator_api_user_or_response(db, payload)
                if user is None:
                    return JSONResponse(meta, status_code=http_status)

                user_id = int(user[schema.USER_ID])
                if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
                    return JSONResponse(
                        {
                            **meta,
                            "ok": False,
                            "code": "not_owner",
                            "message": "This spot was not created by this device account.",
                        },
                        status_code=status.HTTP_403_FORBIDDEN,
                    )

                current_spot = await db_access.get_spot(db, spot_id=spot_id)
                current_pd = await db_access.get_prizedraw(db, spot_id=spot_id)
                if current_spot is None:
                    return JSONResponse(
                        {
                            **meta,
                            "ok": False,
                            "code": "spot_missing",
                            "message": "Spot not found.",
                            "redirect_url": "/",
                        },
                        status_code=status.HTTP_404_NOT_FOUND,
                    )

                if current_pd is not None:
                    current_prize_count = int(current_pd[schema.PRIZEDRAW_PRIZE_COUNT])
                    target_max_total = int(update_kwargs.get(
                        "max_total_claims",
                        current_spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0,
                    ))
                    target_prize_count = int(prize_count_value if prize_count_supplied else current_prize_count)

                    if target_max_total > 0 and target_prize_count > target_max_total:
                        if prize_count_supplied:
                            update_kwargs["max_total_claims"] = target_prize_count
                        else:
                            prize_count_value = target_max_total
                            prize_count_supplied = True

                await db_access.modify_draft_spot(db, spot_id=spot_id, **update_kwargs)

                if prize_count_supplied:
                    await db_access.modify_draft_prizedraw(
                        db,
                        spot_id=spot_id,
                        prize_count=int(prize_count_value),
                    )

                await db_access.require_spot_minimum_payout(db, spot_id=spot_id)

        except ValueError as exc:
            message = str(exc)
            code = (
                "minimum_payout_too_low"
                if "Minimum payout" in message
                else "save_failed"
            )
            return JSONResponse(
                {
                    "ok": False,
                    "code": code,
                    "message": message,
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await _notify_user_cache(db, user_id=user_id)
        spot = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
        transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
        now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "spot": _serialise_owner_spot(spot, now=now, transactions=transactions) if spot else None,
        }
    )


@router.delete("/api/create-spot/{spot_id}")
async def delete_draft_spot_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Delete a creator-owned DRAFT SPOT and return to My Spots.

    Only drafts may be deleted. Published/completed/cancelled/banned spots keep
    their record for audit/history purposes.
    """
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _creator_api_user_or_response(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
                return JSONResponse(
                    {
                        **meta,
                        "ok": False,
                        "code": "not_owner",
                        "message": "This spot was not created by this device account.",
                        "redirect_url": "/",
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            try:
                await db_access.delete_draft_spot(db, spot_id=spot_id)
            except ValueError as e:
                return JSONResponse(
                    {
                        **meta,
                        "ok": False,
                        "code": "delete_failed",
                        "message": str(e),
                    },
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        await _notify_user_cache(db, user_id=user_id)
        if cache is not None:
            mark_spot_dirty = getattr(cache, "mark_spot_cache_dirty", None)
            if callable(mark_spot_dirty):
                mark_spot_dirty()

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "redirect_url": "/my-spots",
        }
    )


# ---------------------------------------------------------------------------
# API routes used by My Claims
# ---------------------------------------------------------------------------

@router.post("/api/my-claims")
async def my_claims_api(
    payload: HomeSessionRequest,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    """Return claims made by the current USER, newest first."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            raw_claims = await db_access.get_claims_by_user(
                db,
                user_id=user_id,
                limit=limit,
                offset=offset,
            )

            refreshed_claims: list[dict[str, Any]] = []
            for row in raw_claims:
                claim = await db_access.refresh_claim_status_from_conditions(
                    db,
                    claim_id=int(row[schema.CLAIM_ID]),
                )
                if claim is not None:
                    refreshed_claims.append(claim)

        now = await db_access.get_unixepoch(db)
        await _notify_user_cache(db, user_id=user_id)

        claims: list[dict[str, Any]] = []
        touched_spot_ids: set[int] = set()
        for claim in refreshed_claims:
            spot_id = int(claim[schema.CLAIM_SPOT_ID])
            spot = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
            if spot is None:
                continue
            touched_spot_ids.add(spot_id)
            claim_transactions = await db_access.get_transactions_by_claim(db, claim_id=int(claim[schema.CLAIM_ID]))
            claims.append(
                _serialise_claim_detail(
                    claim,
                    spot,
                    now=now,
                    viewer_user_id=user_id,
                    claim_transactions=claim_transactions,
                )
            )

        if cache is not None and touched_spot_ids:
            mark_spot_dirty = getattr(cache, "mark_spot_cache_dirty", None)
            if callable(mark_spot_dirty):
                mark_spot_dirty()

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "claims": claims,
        }
    )


# ---------------------------------------------------------------------------
# API routes used by My Spots
# ---------------------------------------------------------------------------

@router.post("/api/my-spots")
async def my_spots_api(
    payload: HomeSessionRequest,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    """Return all SPOTs created by the current USER, past and present."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)

        if user is None:
            return JSONResponse(meta, status_code=http_status)

        user_id = int(user[schema.USER_ID])
        await _notify_user_cache(db, user_id=user_id)

        public_user = _public_user(user)
        if public_user["is_banned"]:
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "banned",
                    "message": f"This device account is banned and can no longer use {const.APP_NAME}.",
                    "user": public_user,
                    "spots": [],
                    "counts": {},
                    "draft_count": 0,
                    "draft_limit": int(getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3)),
                },
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if cache is not None and hasattr(cache, "get_cached_user_spots"):
            raw_spots = await cache.get_cached_user_spots(
                db,
                user_id=user_id,
                limit=limit,
                offset=offset,
            )
        else:
            raw_spots = None

        if raw_spots is None:
            raw_spots = await db_access.get_spots_by_user(
                db,
                user_id=user_id,
                limit=limit,
                offset=offset,
            )

        now = await db_access.get_unixepoch(db)
        spots: list[dict[str, Any]] = []
        for spot in raw_spots:
            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=int(spot[schema.SPOT_ID]),
                limit=50,
            )
            spots.append(_serialise_owner_spot(spot, now=now, transactions=transactions))

        counts = await db_access.get_user_dashboard_counts(db, user_id=user_id)
        draft_count = await db_access.count_draft_spots_by_user(db, user_id=user_id)
        draft_limit = int(getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3))

    bucket_rank = {"active": 0, "upcoming": 1, "previous": 2}

    def owner_spot_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        bucket = str(item.get("bucket") or "previous")
        starts_at = item.get("starts_at")
        ends_at = item.get("ends_at")
        created_at = int(item.get("created_at") or 0)

        if bucket == "active":
            soon_sort = int(ends_at) if ends_at is not None else 0
        elif bucket == "upcoming":
            soon_sort = int(starts_at) if starts_at is not None else created_at
        else:
            soon_sort = -(int(ends_at) if ends_at is not None else created_at)

        return (bucket_rank.get(bucket, 2), soon_sort, -int(item["id"]))

    spots.sort(key=owner_spot_sort_key)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": public_user,
            "spots": spots,
            "counts": counts,
            "draft_count": draft_count,
            "draft_limit": draft_limit,
        }
    )


@router.post("/api/my-spots/{spot_id}/deposit-intent")
async def my_spots_deposit_intent_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Return the current draft deposit request for one creator-owned SPOT."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)

        if user is None:
            return JSONResponse(meta, status_code=http_status)

        user_id = int(user[schema.USER_ID])
        if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
            return JSONResponse(
                {**meta, "ok": False, "code": "not_owner", "message": "This spot was not created by this device account."},
                status_code=status.HTTP_403_FORBIDDEN,
            )

        spot = await db_access.get_spot(db, spot_id=spot_id)
        if spot is None:
            return JSONResponse({**meta, "ok": False, "code": "spot_missing", "message": "Spot not found."}, status_code=status.HTTP_404_NOT_FOUND)

        if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
            return JSONResponse({**meta, "ok": False, "code": "not_draft", "message": "Only draft spots can be funded from this page."}, status_code=status.HTTP_409_CONFLICT)

        transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
        deposit = _deposit_summary(transactions, total_value=int(spot.get(schema.SPOT_TOTAL_VALUE) or 0))
        if int(deposit.get("pending_amount") or 0) > 0:
            return JSONResponse({**meta, "ok": False, "code": "deposit_pending", "message": "This draft already has a pending deposit. Wait for it to confirm or fail before making another deposit."}, status_code=status.HTTP_409_CONFLICT)

        amount_due = int(deposit.get("amount_due") or 0)
        if amount_due <= 0:
            return JSONResponse({**meta, "ok": False, "code": "deposit_covered", "message": "This draft already has submitted deposits covering its total value."}, status_code=status.HTTP_409_CONFLICT)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "spot": {
                "id": int(spot[schema.SPOT_ID]),
                "title": spot.get(schema.SPOT_TITLE) or "NimHunt Spot",
            },
            "amount": amount_due,
            "recipient": spot.get(schema.SPOT_DEPOSIT_ADDRESS),
            "deposit": deposit,
        }
    )


@router.post("/api/my-spots/{spot_id}/deposit-submitted")
async def my_spots_deposit_submitted_api(spot_id: int, payload: DepositSubmittedRequest) -> JSONResponse:
    """Record a pending SPOT deposit after Nimiq Pay returns a transaction hash."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
                return JSONResponse(
                    {**meta, "ok": False, "code": "not_owner", "message": "This spot was not created by this device account."},
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            spot = await db_access.get_spot(db, spot_id=spot_id)
            if spot is None:
                return JSONResponse({**meta, "ok": False, "code": "spot_missing", "message": "Spot not found."}, status_code=status.HTTP_404_NOT_FOUND)

            if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
                return JSONResponse({**meta, "ok": False, "code": "not_draft", "message": "Only draft spots can receive creator deposits."}, status_code=status.HTTP_409_CONFLICT)

            transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
            deposit = _deposit_summary(transactions, total_value=int(spot.get(schema.SPOT_TOTAL_VALUE) or 0))
            if int(deposit.get("pending_amount") or 0) > 0:
                return JSONResponse({**meta, "ok": False, "code": "deposit_pending", "message": "This draft already has a pending deposit. Wait for it to confirm or fail before making another deposit."}, status_code=status.HTTP_409_CONFLICT)

            amount_due = int(deposit.get("amount_due") or 0)
            if amount_due <= 0:
                return JSONResponse({**meta, "ok": False, "code": "deposit_covered", "message": "This draft already has submitted deposits covering its total value."}, status_code=status.HTTP_409_CONFLICT)

            await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=user_id,
                spot_id=spot_id,
                amount=amount_due,
                from_address=payload.from_address or "Nimiq Pay",
                tx_hash=payload.tx_hash,
                to_address=spot.get(schema.SPOT_DEPOSIT_ADDRESS),
            )

        await _notify_user_cache(db, user_id=user_id)
        spot_summary = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
        transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
        now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "spot": _serialise_owner_spot(spot_summary, now=now, transactions=transactions) if spot_summary else None,
        }
    )


@router.post("/api/my-spots/{spot_id}/publish")
async def my_spots_publish_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Publish one complete, fully funded draft SPOT."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
                return JSONResponse(
                    {**meta, "ok": False, "code": "not_owner", "message": "This spot was not created by this device account."},
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            try:
                await db_access.publish_spot(db, spot_id=spot_id)
            except ValueError as exc:
                return JSONResponse({**meta, "ok": False, "code": "publish_failed", "message": str(exc)}, status_code=status.HTTP_409_CONFLICT)

        await _notify_all_cache_for_spot_owner_change(db, user_id=user_id, spot_id=spot_id)
        spot_summary = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
        transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
        now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "spot": _serialise_owner_spot(spot_summary, now=now, transactions=transactions) if spot_summary else None,
        }
    )


@router.post("/api/my-spots/{spot_id}/cancel")
async def my_spots_cancel_api(spot_id: int, payload: HomeSessionRequest) -> JSONResponse:
    """Cancel one owner-created published standard SPOT and submit refund/fee sends."""
    async with get_db() as db:
        async with db_access.transaction(db):
            user, meta, http_status = await _identify_private_page_user(db, payload)
            if user is None:
                return JSONResponse(meta, status_code=http_status)

            user_id = int(user[schema.USER_ID])
            if not await db_access.is_spot_owned_by_user(db, spot_id=spot_id, user_id=user_id):
                return JSONResponse(
                    {**meta, "ok": False, "code": "not_owner", "message": "This spot was not created by this device account."},
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        # Chain-facing cancellation work deliberately happens outside the
        # short identity-check transaction above. trans_updater.py records the
        # resulting app transactions and then changes the SPOT status.
        try:
            cancellation = await trans_updater.submit_spot_cancellation_transactions(
                db,
                spot_id=spot_id,
                cancellation_fee=getattr(const, "SPOT_CANCELLATION_FEE", 0),
                fee_address=getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
            )
            await db.commit()
        except ValueError as exc:
            await db.rollback()
            return JSONResponse({**meta, "ok": False, "code": "cancel_failed", "message": str(exc)}, status_code=status.HTTP_409_CONFLICT)
        except RuntimeError as exc:
            await db.rollback()
            return JSONResponse({**meta, "ok": False, "code": "cancel_failed", "message": str(exc)}, status_code=status.HTTP_409_CONFLICT)

        await _notify_all_cache_for_spot_owner_change(db, user_id=user_id, spot_id=spot_id)
        spot_summary = await db_access.get_spot_owner_summary(db, spot_id=spot_id)
        transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)
        now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "cancellation": cancellation,
            "spot": _serialise_owner_spot(spot_summary, now=now, transactions=transactions) if spot_summary else None,
        }
    )


# ---------------------------------------------------------------------------
# API routes used by the home page
# ---------------------------------------------------------------------------

@router.get("/api/home/metrics")
async def home_metrics() -> JSONResponse:
    """Return small public counters for the Home page."""
    async with get_db() as db:
        metrics = await _home_metrics(db)

    return JSONResponse({
        "ok": True,
        "metrics": metrics,
    })


@router.post("/api/home/session")
async def home_session(payload: HomeSessionRequest) -> JSONResponse:
    """Create or retrieve the USER for this webview session.

    The JavaScript side asks Nimiq Pay for the device identifier. This route
    receives the 64-character hash, finds the matching USER, or creates one.
    """
    language = _clean_language(payload.language)

    if not payload.wallet_available or not _valid_device_id_hash(payload.device_id_hash):
        if bool(getattr(const, "DEFAULT_TO_TEST_USER", False)):
            test_user_id = int(getattr(const, "TEST_USER_ID", 0))

            async with get_db() as db:
                async with db_access.transaction(db):
                    user = await db_access.get_user_by_id(db, user_id=test_user_id)
                    if user is not None:
                        await db_access.touch_user_last_seen(db, user_id=test_user_id)
                        user = await db_access.get_user_by_id(db, user_id=test_user_id)

                if user is not None:
                    await _notify_user_cache(db, user_id=test_user_id)

            if user is None:
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "test_user_missing",
                        "message": (
                            f"DEFAULT_TO_TEST_USER is enabled, but TEST_USER_ID={test_user_id} "
                            "does not exist. Run spoof.py to create the mock data."
                        ),
                        "user": None,
                        "created": False,
                        "test_user": True,
                        "language": language,
                        "location_available": bool(payload.location_available),
                    },
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            public_user = _public_user(user)
            banned = bool(public_user["is_banned"])
            return JSONResponse(
                {
                    "ok": not banned,
                    "code": "banned" if banned else "test_user",
                    "message": (
                        f"Test user {test_user_id} is banned and can no longer use {const.APP_NAME}."
                        if banned
                        else f"Using desktop test user {test_user_id}."
                    ),
                    "user": public_user,
                    "created": False,
                    "test_user": True,
                    "language": language,
                    "location_available": bool(payload.location_available),
                }
            )

        return JSONResponse(
            {
                "ok": False,
                "code": "wallet_unavailable",
                "message": f"Open {const.APP_NAME} inside Nimiq Pay to identify this device.",
                "user": None,
                "created": False,
                "test_user": False,
                "language": language,
                "location_available": bool(payload.location_available),
            },
            status_code=status.HTTP_200_OK,
        )

    assert payload.device_id_hash is not None
    device_id_hash = payload.device_id_hash.strip().lower()

    async with get_db() as db:
        async with db_access.transaction(db):
            user_id, created = await db_access.get_or_create_user(
                db,
                device_id_hash=device_id_hash,
            )
            await db_access.touch_user_last_seen(db, user_id=user_id)
            user = await db_access.get_user_by_id(db, user_id=user_id)

        if user is not None:
            await _notify_user_cache(db, user_id=int(user_id))

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User could not be loaded after creation.",
        )

    public_user = _public_user(user)
    banned = bool(public_user["is_banned"])

    return JSONResponse(
        {
            "ok": not banned,
            "code": "banned" if banned else "ok",
            "message": (
                f"This device account is banned and can no longer use {const.APP_NAME}."
                if banned
                else "User ready."
            ),
            "user": public_user,
            "created": bool(created),
            "test_user": False,
            "language": language,
            "location_available": bool(payload.location_available),
        }
    )


@router.patch("/api/home/display-name")
async def update_display_name(payload: DisplayNameRequest) -> JSONResponse:
    """Update the current user's display name.

    In normal use the user is identified by the Nimiq Pay device hash. During
    desktop development, DEFAULT_TO_TEST_USER lets this endpoint update the
    spoof/test user when no device hash is available.
    """
    display_name = payload.display_name.strip()
    if not (const.DISPLAY_NAME_MIN_CHARS <= len(display_name) <= const.DISPLAY_NAME_MAX_CHARS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Display name must be between "
                f"{const.DISPLAY_NAME_MIN_CHARS} and "
                f"{const.DISPLAY_NAME_MAX_CHARS} characters"
            ),
        )

    device_id_hash = payload.device_id_hash.strip().lower() if payload.device_id_hash else None
    use_test_user = device_id_hash is None and bool(const.DEFAULT_TO_TEST_USER)

    if device_id_hash is not None and not _valid_device_id_hash(device_id_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid device id hash")

    async with get_db() as db:
        async with db_access.transaction(db):
            if use_test_user:
                user = await db_access.get_user_by_id(db, user_id=int(const.TEST_USER_ID))
                if user is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Desktop test user not found. Run spoof.py, then reload the page.",
                    )
            else:
                if device_id_hash is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Device id hash is required")
                user = await db_access.get_user(db, device_id_hash=device_id_hash)
                if user is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

            user_id = int(user[schema.USER_ID])
            if int(user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Banned users cannot edit their profile")

            await db_access.modify_user_display_name(db, user_id=user_id, display_name=display_name)
            await db_access.touch_user_last_seen(db, user_id=user_id)
            updated_user = await db_access.get_user_by_id(db, user_id=user_id)

        await _notify_all_cache_if_user_display_changed(db, user_id=user_id)

    if updated_user is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User disappeared after update")

    return JSONResponse({"ok": True, "user": _public_user(updated_user)})
