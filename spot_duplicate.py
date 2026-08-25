"""Create clean draft copies of creator-owned Spots."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

import constants as const
import database as schema
import db_access
from database import get_db
from public_html import (
    CreateDraftSpotRequest,
    _creator_api_user_or_response,
    _notify_user_cache,
    _public_user,
    _serialise_owner_spot,
)

router = APIRouter()


class DuplicateSpotError(Exception):
    """Expected creator-facing failure while preparing a duplicate draft."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        draft_count: int | None = None,
        draft_limit: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = int(http_status)
        self.draft_count = draft_count
        self.draft_limit = draft_limit


def duplicate_spot_configuration(
    source: dict[str, Any],
    *,
    title: str,
    now: int,
) -> dict[str, Any]:
    """Return the strict configuration whitelist for a new draft.

    Operational identifiers, statuses, deposits, transactions, claims, reports,
    claim codes and draw outcomes are deliberately absent. Creation helpers
    generate fresh identity, wallet and fee fields for the copy.
    """
    is_prizedraw = source.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    starts_at = source.get(schema.SPOT_STARTS_AT)
    copied_starts_at = (
        int(starts_at)
        if starts_at is not None and int(starts_at) > int(now)
        else None
    )

    kwargs: dict[str, Any] = {
        "title": str(title).strip(),
        "desc": source.get(schema.SPOT_DESC),
        "lat": source.get(schema.SPOT_LAT),
        "long": source.get(schema.SPOT_LONG),
        "radius": source.get(schema.SPOT_RADIUS),
        "claim_duration": source.get(schema.SPOT_CLAIM_DURATION),
        "max_claims_per_user": source.get(schema.SPOT_MAX_CLAIMS_PER_USER),
        "max_total_claims": source.get(schema.SPOT_MAX_TOTAL_CLAIMS),
        "total_value": source.get(schema.SPOT_TOTAL_VALUE),
        "starts_at": copied_starts_at,
        "ends_at": source.get(schema.SPOT_ENDS_AT),
        "use_password": (
            False
            if is_prizedraw
            else bool(int(source.get(schema.SPOT_USE_PASSWORD) or 0))
        ),
        "city": source.get(schema.SPOT_CITY),
        "country": source.get(schema.SPOT_COUNTRY),
        "auto_reverse_geocode": False,
    }
    if is_prizedraw:
        kwargs["prize_count"] = int(source[schema.PRIZEDRAW_PRIZE_COUNT])

    return {"is_prizedraw": is_prizedraw, "create_kwargs": kwargs}


async def duplicate_owned_spot_as_draft(
    db,
    *,
    source_spot_id: int,
    user_id: int,
    title: str,
    now: int,
    draft_limit: int,
) -> int:
    """Create one clean duplicate after ownership and draft-limit checks."""
    source = await db_access.get_spot_owner_summary(
        db,
        spot_id=int(source_spot_id),
    )
    if source is None:
        raise DuplicateSpotError(
            "spot_missing",
            "This spot could not be found.",
            status.HTTP_404_NOT_FOUND,
        )
    if int(source[schema.SPOT_CREATED_BY]) != int(user_id):
        raise DuplicateSpotError(
            "not_owner",
            "This spot was not created by this device account.",
            status.HTTP_403_FORBIDDEN,
        )

    draft_count = await db_access.count_draft_spots_by_user(
        db,
        user_id=int(user_id),
    )
    if draft_count >= int(draft_limit):
        raise DuplicateSpotError(
            "draft_limit_reached",
            (
                f"You already have {draft_count} draft spots. "
                "Publish or delete one before creating another."
            ),
            status.HTTP_409_CONFLICT,
            draft_count=draft_count,
            draft_limit=int(draft_limit),
        )

    config = duplicate_spot_configuration(source, title=title, now=int(now))
    create_kwargs = config["create_kwargs"]
    if config["is_prizedraw"]:
        return await db_access.create_prizedraw(
            db,
            created_by=int(user_id),
            **create_kwargs,
        )
    return await db_access.create_spot(
        db,
        created_by=int(user_id),
        **create_kwargs,
    )


@router.post("/api/my-spots/{spot_id}/duplicate")
async def duplicate_spot_api(
    spot_id: int,
    payload: CreateDraftSpotRequest,
) -> JSONResponse:
    """Duplicate one creator-owned Spot into a clean, unfunded draft."""
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
        try:
            async with db_access.transaction(db, immediate=True):
                user, meta, http_status = await _creator_api_user_or_response(
                    db,
                    payload,
                )
                if user is None:
                    return JSONResponse(meta, status_code=http_status)

                user_id = int(user[schema.USER_ID])
                now = await db_access.get_unixepoch(db)
                draft_limit = int(
                    getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3)
                )
                new_spot_id = await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=int(spot_id),
                    user_id=user_id,
                    title=payload.title,
                    now=now,
                    draft_limit=draft_limit,
                )
        except DuplicateSpotError as exc:
            error = {
                **meta,
                "ok": False,
                "code": exc.code,
                "message": exc.message,
                "user": _public_user(user),
            }
            if exc.draft_count is not None:
                error["draft_count"] = int(exc.draft_count)
            if exc.draft_limit is not None:
                error["draft_limit"] = int(exc.draft_limit)
            return JSONResponse(error, status_code=exc.http_status)
        except ValueError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "duplicate_failed",
                    "message": str(exc),
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await _notify_user_cache(db, user_id=user_id)
        spot = await db_access.get_spot_owner_summary(
            db,
            spot_id=new_spot_id,
        )
        response_now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "spot": (
                _serialise_owner_spot(spot, now=response_now, transactions=[])
                if spot
                else None
            ),
            "edit_url": f"{const.CREATE_SPOT_URL}/{new_spot_id}",
        },
        status_code=status.HTTP_201_CREATED,
    )
