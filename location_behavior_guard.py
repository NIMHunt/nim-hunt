"""Privacy-preserving public-Spot location behaviour guard.

Raw browser coordinates are used transiently for server-side geometry and are
never persisted.  The durable state contains only saturated counters, a few
Spot IDs (strictly capped at the rule thresholds), and deduplication timestamps.
"""

from __future__ import annotations

import json
from typing import Any

import database as schema
import db_access

STATE_PREFIX = "location_behavior_guard:user:"
STATE_VERSION = 2

# 1.5m is deliberately far tighter than a venue or claim radius.  It is only
# supporting evidence and needs three distinct physical reward locations.
NEAR_EXACT_CENTRE_METRES = 1.5
CENTRE_DISTINCT_LOCATIONS_REQUIRED = 3
INSIDE_STREAK_LOCATIONS_REQUIRED = 4

# Canonical Spot centres within 25m represent one physical reward location.
# This matches the minimum normal Spot radius and prevents overlapping rewards
# at one venue from manufacturing independent behavioural events.
SAME_REWARD_LOCATION_METRES = 25.0

SIGNED_RETRY_WINDOW_SECONDS = 30 * 60
OUTSIDE_POLL_WINDOW_SECONDS = 6 * 60 * 60
TEMPORARY_RESTRICTION_SECONDS = 24 * 60 * 60
MAX_DIAGNOSTIC_COUNT = 255


class PublicClaimBehaviorRestrictionError(ValueError):
    """Raised for a temporary public-claim-only behaviour restriction."""

    code = "public_temporarily_unavailable"

    def __init__(self) -> None:
        super().__init__("Public Spots are temporarily unavailable for this account. Please try again later.")


def _key(user_id: int) -> str:
    return f"{STATE_PREFIX}{int(user_id)}"


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "meaningful_observations": 0,
        "signed_inside_observations": 0,
        "browser_outside_observations": 0,
        "centre_location_spot_ids": [],
        "inside_streak_spot_ids": [],
        "outside_since_last_inside": True,
    }


def _saturated(value: Any, increment: int = 0) -> int:
    try:
        current = max(0, int(value or 0))
    except (TypeError, ValueError):
        current = 0
    return min(MAX_DIAGNOSTIC_COUNT, current + int(increment))


def _migrate(value: Any) -> dict[str, Any]:
    """Conservatively migrate v1 without importing punitive transient evidence."""
    if not isinstance(value, dict):
        return _empty_state()
    if int(value.get("version") or 0) != STATE_VERSION:
        state = _empty_state()
        for name in (
            "meaningful_observations", "signed_inside_observations",
            "browser_outside_observations",
        ):
            state[name] = _saturated(value.get(name))
        # Honour an already-issued finite restriction, but consume old lists so
        # v1 lifetime evidence cannot re-arm it after expiry.
        state["restricted_until"] = max(0, int(value.get("restricted_until") or 0))
        return state
    state = _empty_state()
    state.update(value)
    state["version"] = STATE_VERSION
    state["centre_location_spot_ids"] = [
        int(v) for v in list(state.get("centre_location_spot_ids") or [])
        [:CENTRE_DISTINCT_LOCATIONS_REQUIRED]
    ]
    state["inside_streak_spot_ids"] = [
        int(v) for v in list(state.get("inside_streak_spot_ids") or [])
        [:INSIDE_STREAK_LOCATIONS_REQUIRED]
    ]
    for name in (
        "meaningful_observations", "signed_inside_observations",
        "browser_outside_observations",
    ):
        state[name] = _saturated(state.get(name))
    return state


async def get_state(db, *, user_id: int) -> dict[str, Any]:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?", (_key(user_id),)
    )
    row = await cur.fetchone()
    if row is None:
        return _empty_state()
    try:
        return _migrate(json.loads(str(row[0])))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _empty_state()


async def _save(db, *, user_id: int, state: dict[str, Any]) -> None:
    await db.execute(
        f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} "
        f"({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) VALUES (?, ?) "
        f"ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET "
        f"{schema.APP_METADATA_VALUE}=excluded.{schema.APP_METADATA_VALUE}",
        (_key(user_id), json.dumps(_migrate(state), separators=(",", ":"), sort_keys=True)),
    )


async def _claimable_public_spots(db, *, user_id: int) -> list[dict[str, Any]]:
    """Read canonical eligible Spots, explicitly excluding the user's own."""
    rows = await db.execute_fetchall(
        f"SELECT {schema.SPOT_ID}, {schema.SPOT_CREATED_BY}, {schema.SPOT_LAT}, "
        f"{schema.SPOT_LONG}, {schema.SPOT_RADIUS} FROM {schema.SPOT_VIEW_PUBLIC_LIST} "
        f"WHERE availability_rank = 0 AND {schema.SPOT_USE_PASSWORD} = 0 "
        f"AND {schema.SPOT_CREATED_BY} != ?", (int(user_id),)
    )
    result = []
    for row in rows:
        spot_id = int(row[schema.SPOT_ID])
        if await db_access.is_spot_claim_capacity_available(db, spot_id=spot_id):
            result.append(dict(row))
    return result


def _inside_any(spots: list[dict[str, Any]], *, lat: float, long: float) -> bool:
    return any(
        db_access.distance_metres(
            float(lat), float(long), float(spot[schema.SPOT_LAT]), float(spot[schema.SPOT_LONG])
        ) <= max(0.0, float(spot[schema.SPOT_RADIUS]))
        for spot in spots
    )


async def _same_reward_location(db, *, spot: dict[str, Any], prior_spot_ids: list[int]) -> bool:
    """Compare bounded canonical Spot centres; submitted coordinates are irrelevant."""
    for prior_id in prior_spot_ids:
        if int(prior_id) == int(spot[schema.SPOT_ID]):
            return True
        prior = await db_access.get_spot(db, spot_id=int(prior_id))
        if prior is None or prior.get(schema.SPOT_LAT) is None or prior.get(schema.SPOT_LONG) is None:
            continue
        if db_access.distance_metres(
            float(spot[schema.SPOT_LAT]), float(spot[schema.SPOT_LONG]),
            float(prior[schema.SPOT_LAT]), float(prior[schema.SPOT_LONG]),
        ) <= SAME_REWARD_LOCATION_METRES:
            return True
    return False


def _currently_restricted(state: dict[str, Any], *, now: int) -> bool:
    return int(state.get("restricted_until") or 0) > int(now)


def _new_evidence_matured(state: dict[str, Any], *, corroborated: bool) -> bool:
    centre = len(state["centre_location_spot_ids"]) >= CENTRE_DISTINCT_LOCATIONS_REQUIRED
    inside = len(state["inside_streak_spot_ids"]) >= INSIDE_STREAK_LOCATIONS_REQUIRED
    return (centre and inside) or (corroborated and (centre or inside))


def _consume_episode(state: dict[str, Any], *, now: int) -> None:
    state["restricted_until"] = int(now) + TEMPORARY_RESTRICTION_SECONDS
    state["centre_location_spot_ids"] = []
    state["inside_streak_spot_ids"] = []
    state["outside_since_last_inside"] = True
    state.pop("last_signed_spot_id", None)
    state.pop("last_signed_observed_at", None)


async def observe_find_location(
    db, *, user_id: int, lat: float, long: float, now: int | None = None,
) -> dict[str, Any]:
    """Record semantic outside transitions without holding a lock for geometry."""
    checked_at = int(now if now is not None else await db_access.get_unixepoch(db))
    # Potentially broad public-Spot reads and capacity checks intentionally run
    # before acquiring SQLite's global write reservation.
    spots = await _claimable_public_spots(db, user_id=int(user_id))
    if _inside_any(spots, lat=lat, long=long):
        return await get_state(db, user_id=user_id)  # browser-inside is non-evidence

    async with db_access.transaction(db, immediate=True):
        state = await get_state(db, user_id=user_id)
        transition = not bool(state.get("outside_since_last_inside"))
        recently_outside = (
            checked_at - int(state.get("last_outside_observed_at") or 0)
            < OUTSIDE_POLL_WINDOW_SECONDS
        )
        if not transition and recently_outside:
            return state
        state["meaningful_observations"] = _saturated(state.get("meaningful_observations"), 1)
        state["browser_outside_observations"] = _saturated(
            state.get("browser_outside_observations"), 1
        )
        state["outside_since_last_inside"] = True
        state["inside_streak_spot_ids"] = []
        state["last_outside_observed_at"] = checked_at
        await _save(db, user_id=user_id, state=state)
        return state


async def observe_signed_claim(
    db, *, user_id: int, spot: dict[str, Any], lat: float, long: float,
    location_accuracy_metres: float | None = None, now: int | None = None,
    corroborated: bool = False,
) -> dict[str, Any]:
    """Record one signed public claim event and return its restriction decision."""
    if not db.in_transaction:
        async with db_access.transaction(db, immediate=True):
            return await observe_signed_claim(
                db, user_id=user_id, spot=spot, lat=lat, long=long,
                location_accuracy_metres=location_accuracy_metres, now=now,
                corroborated=corroborated,
            )
    del location_accuracy_metres  # forged poor accuracy must not erase exact evidence
    if int(spot.get(schema.SPOT_CREATED_BY) or -1) == int(user_id):
        return {"restricted": False, "reason": "own_spot_exempt"}
    if int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1:
        return {"restricted": False, "reason": "password_exempt"}
    if not await db_access.is_spot_currently_claimable(db, spot_id=int(spot[schema.SPOT_ID])):
        return {"restricted": False, "reason": "spot_not_claimable"}
    checked_at = int(now if now is not None else await db_access.get_unixepoch(db))
    state = await get_state(db, user_id=user_id)
    # Active restrictions are fixed expiries: do not mutate evidence or slide time.
    if _currently_restricted(state, now=checked_at):
        return {"restricted": True, "state": state}

    spot_id = int(spot[schema.SPOT_ID])
    retry = (
        int(state.get("last_signed_spot_id") or 0) == spot_id
        and checked_at - int(state.get("last_signed_observed_at") or 0)
        < SIGNED_RETRY_WINDOW_SECONDS
    )
    distance = db_access.distance_metres(
        float(lat), float(long), float(spot[schema.SPOT_LAT]), float(spot[schema.SPOT_LONG])
    )
    if not retry and distance <= float(spot[schema.SPOT_RADIUS]):
        streak = list(state["inside_streak_spot_ids"])
        same_as_streak = await _same_reward_location(db, spot=spot, prior_spot_ids=streak)
        if not same_as_streak:
            streak.append(spot_id)
            state["inside_streak_spot_ids"] = streak[:INSIDE_STREAK_LOCATIONS_REQUIRED]
        centres = list(state["centre_location_spot_ids"])
        if distance <= NEAR_EXACT_CENTRE_METRES and not await _same_reward_location(
            db, spot=spot, prior_spot_ids=centres
        ):
            centres.append(spot_id)
            state["centre_location_spot_ids"] = centres[:CENTRE_DISTINCT_LOCATIONS_REQUIRED]
        state["outside_since_last_inside"] = False
        state["meaningful_observations"] = _saturated(state.get("meaningful_observations"), 1)
        state["signed_inside_observations"] = _saturated(
            state.get("signed_inside_observations"), 1
        )
        state["last_signed_spot_id"] = spot_id
        state["last_signed_observed_at"] = checked_at

    if _new_evidence_matured(state, corroborated=corroborated):
        _consume_episode(state, now=checked_at)
    await _save(db, user_id=user_id, state=state)
    return {"restricted": _currently_restricted(state, now=checked_at), "state": state}


async def current_decision(db, *, user_id: int, now: int | None = None) -> dict[str, Any]:
    checked_at = int(now if now is not None else await db_access.get_unixepoch(db))
    state = await get_state(db, user_id=user_id)
    return {"restricted": _currently_restricted(state, now=checked_at), "state": state}
