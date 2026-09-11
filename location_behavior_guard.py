"""Privacy-preserving public-Spot location behaviour guard.

Only bounded counters, distinct Spot identifiers and the last deduplication
marker are retained.  Raw browser coordinates are used for server-side geometry
and then discarded; they are never written to the database.
"""

from __future__ import annotations

import json
from typing import Any

import database as schema
import db_access

STATE_PREFIX = "location_behavior_guard:user:"

# Deliberately much tighter than a venue or Spot radius.  Consumer GPS readings
# can occasionally fall this close by chance, which is why three distinct Spots
# are required and centre proximity can never ban an account by itself.
NEAR_EXACT_CENTRE_METRES = 1.5
CENTRE_DISTINCT_SPOTS_REQUIRED = 3
INSIDE_ONLY_DISTINCT_SPOTS_REQUIRED = 4
OBSERVATION_WINDOW_SECONDS = 30 * 60
FIND_OBSERVATION_WINDOW_SECONDS = 6 * 60 * 60
TEMPORARY_RESTRICTION_SECONDS = 24 * 60 * 60


class PublicClaimBehaviorRestrictionError(ValueError):
    """Raised for a temporary public-claim-only behaviour restriction."""

    code = "public_temporarily_unavailable"

    def __init__(self) -> None:
        super().__init__("Public Spots are temporarily unavailable for this account. Please try again later.")


def _key(user_id: int) -> str:
    return f"{STATE_PREFIX}{int(user_id)}"


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "meaningful_observations": 0,
        "signed_inside_observations": 0,
        "browser_outside_observations": 0,
        "inside_spot_ids": [],
        "near_centre_spot_ids": [],
        "outside_since_last_inside": False,
        "inside_transitions_without_outside": 0,
    }


async def get_state(db, *, user_id: int) -> dict[str, Any]:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?", (_key(user_id),)
    )
    row = await cur.fetchone()
    if row is None:
        return _empty_state()
    try:
        value = json.loads(str(row[0]))
    except (TypeError, json.JSONDecodeError):
        return _empty_state()
    return value if isinstance(value, dict) else _empty_state()


async def _save(db, *, user_id: int, state: dict[str, Any]) -> None:
    await db.execute(
        f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} "
        f"({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) VALUES (?, ?) "
        f"ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET "
        f"{schema.APP_METADATA_VALUE}=excluded.{schema.APP_METADATA_VALUE}",
        (_key(user_id), json.dumps(state, separators=(",", ":"), sort_keys=True)),
    )


async def _claimable_public_spots(db) -> list[dict[str, Any]]:
    """Return canonical, currently claimable, non-password public Spots."""
    rows = await db.execute_fetchall(
        f"SELECT {schema.SPOT_ID}, {schema.SPOT_LAT}, {schema.SPOT_LONG}, {schema.SPOT_RADIUS} "
        f"FROM {schema.SPOT_VIEW_PUBLIC_LIST} "
        f"WHERE availability_rank = 0 AND {schema.SPOT_USE_PASSWORD} = 0"
    )
    result = []
    for row in rows:
        spot_id = int(row[schema.SPOT_ID])
        if await db_access.is_spot_claim_capacity_available(db, spot_id=spot_id):
            result.append(dict(row))
    return result


def _inside_spots(spots: list[dict[str, Any]], *, lat: float, long: float) -> list[tuple[dict[str, Any], float]]:
    inside = []
    for spot in spots:
        distance = db_access.distance_metres(
            float(lat), float(long), float(spot[schema.SPOT_LAT]), float(spot[schema.SPOT_LONG])
        )
        # No client accuracy mercy is used to classify behavioural evidence.
        if distance <= max(0.0, float(spot[schema.SPOT_RADIUS])):
            inside.append((spot, distance))
    return inside


def _restricted(state: dict[str, Any], *, now: int, corroborated: bool = False) -> bool:
    if int(state.get("restricted_until") or 0) > int(now):
        return True
    centres = len(set(state.get("near_centre_spot_ids") or []))
    inside = len(set(state.get("inside_spot_ids") or []))
    inside_only = (
        inside >= INSIDE_ONLY_DISTINCT_SPOTS_REQUIRED
        and int(state.get("inside_transitions_without_outside") or 0)
        >= INSIDE_ONLY_DISTINCT_SPOTS_REQUIRED - 1
        and int(state.get("browser_outside_observations") or 0) == 0
    )
    return (centres >= CENTRE_DISTINCT_SPOTS_REQUIRED and inside_only) or (
        corroborated and (centres >= CENTRE_DISTINCT_SPOTS_REQUIRED or inside_only)
    )


async def observe_find_location(db, *, user_id: int, lat: float, long: float, now: int | None = None) -> dict[str, Any]:
    """Record at most one weak Find Spots observation per six-hour window."""
    checked_at = int(now if now is not None else await db_access.get_unixepoch(db))
    async with db_access.transaction(db, immediate=True):
        state = await get_state(db, user_id=user_id)
        if checked_at - int(state.get("last_find_observed_at") or 0) < FIND_OBSERVATION_WINDOW_SECONDS:
            return state
        inside = _inside_spots(await _claimable_public_spots(db), lat=lat, long=long)
        state["last_find_observed_at"] = checked_at
        state["meaningful_observations"] = int(state.get("meaningful_observations") or 0) + 1
        if not inside:
            state["browser_outside_observations"] = int(state.get("browser_outside_observations") or 0) + 1
            state["outside_since_last_inside"] = True
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
                db,
                user_id=user_id,
                spot=spot,
                lat=lat,
                long=long,
                location_accuracy_metres=location_accuracy_metres,
                now=now,
                corroborated=corroborated,
            )
    del location_accuracy_metres  # A forged poor value must not erase exact-coordinate evidence.
    if int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1:
        return {"restricted": False, "reason": "password_exempt"}
    if not await db_access.is_spot_currently_claimable(db, spot_id=int(spot[schema.SPOT_ID])):
        return {"restricted": False, "reason": "spot_not_claimable"}
    checked_at = int(now if now is not None else await db_access.get_unixepoch(db))
    state = await get_state(db, user_id=user_id)
    spot_id = int(spot[schema.SPOT_ID])
    # Same-Spot retries in the meaningful window are the same physical event.
    duplicate = (
        int(state.get("last_signed_spot_id") or 0) == spot_id
        and checked_at - int(state.get("last_signed_observed_at") or 0) < OBSERVATION_WINDOW_SECONDS
    )
    if not duplicate:
        distance = db_access.distance_metres(
            float(lat), float(long), float(spot[schema.SPOT_LAT]), float(spot[schema.SPOT_LONG])
        )
        if distance <= float(spot[schema.SPOT_RADIUS]):
            prior_spot = state.get("last_qualifying_spot_id")
            if prior_spot is not None and int(prior_spot) != spot_id:
                if not bool(state.get("outside_since_last_inside")):
                    state["inside_transitions_without_outside"] = int(
                        state.get("inside_transitions_without_outside") or 0
                    ) + 1
            state["outside_since_last_inside"] = False
            state["last_qualifying_spot_id"] = spot_id
            state["inside_spot_ids"] = sorted(set(state.get("inside_spot_ids") or []) | {spot_id})
            state["signed_inside_observations"] = int(state.get("signed_inside_observations") or 0) + 1
            if distance <= NEAR_EXACT_CENTRE_METRES:
                state["near_centre_spot_ids"] = sorted(
                    set(state.get("near_centre_spot_ids") or []) | {spot_id}
                )
        state["meaningful_observations"] = int(state.get("meaningful_observations") or 0) + 1
        state["last_signed_spot_id"] = spot_id
        state["last_signed_observed_at"] = checked_at
    if _restricted(state, now=checked_at, corroborated=corroborated):
        state["restricted_until"] = max(
            int(state.get("restricted_until") or 0), checked_at + TEMPORARY_RESTRICTION_SECONDS
        )
    await _save(db, user_id=user_id, state=state)
    return {"restricted": int(state.get("restricted_until") or 0) > checked_at, "state": state}


async def current_decision(db, *, user_id: int, now: int | None = None) -> dict[str, Any]:
    checked_at = int(now if now is not None else await db_access.get_unixepoch(db))
    state = await get_state(db, user_id=user_id)
    return {"restricted": int(state.get("restricted_until") or 0) > checked_at, "state": state}
