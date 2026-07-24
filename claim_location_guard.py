"""Ban only extreme impossible travel between claim-validated Spot locations.

The browser may report location while a user explores the map, but harmless
browsing is not relevant to abuse prevention. This guard therefore considers
only locations that reached NimHunt's normal claim creation boundary.

It wraps ``db_access.create_claim`` rather than the public route. By the time
that function is reached, ordinary eligibility, radius, password and
cancellation checks have already passed. A detected jump can therefore ban the
user in the same database transaction before a CLAIM row or claim-code attempt
is written.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]
ClaimCreator = Callable[..., Awaitable[int]]

# These thresholds are intentionally severe. The user must have travelled at
# least 100 miles outside the mercy offered by both Spot radii, and the required
# speed must exceed roughly 1,118 mph. Commercial flights remain below this.
MINIMUM_TRAVEL_DISTANCE_METRES = 160_934.0
MAXIMUM_PLAUSIBLE_SPEED_METRES_PER_SECOND = 500.0
RECENT_CLAIM_LIMIT = 5

_ORIGINAL_CREATE_CLAIM: ClaimCreator = db_access.create_claim
_INSTALLED = False
logger = logging.getLogger(__name__)


async def get_impossible_claim_travel_check(
    db,
    *,
    user_id: int,
    target_spot_id: int,
) -> RowDict:
    """Return details when recent claim locations require impossible travel.

    The distance is measured between Spot centres and then reduced by both Spot
    radii. This gives the user the shortest plausible journey between the two
    claim areas rather than assuming they stood at each exact centre.
    """
    target_spot = await db_access.get_spot(db, spot_id=int(target_spot_id))
    if (
        target_spot is None
        or target_spot.get(schema.SPOT_LAT) is None
        or target_spot.get(schema.SPOT_LONG) is None
    ):
        return {"detected": False, "current_spot_id": int(target_spot_id)}

    rows = await db.execute_fetchall(
        f"""
        SELECT
            c.{schema.CLAIM_ID} AS claim_id,
            c.{schema.CLAIM_SPOT_ID} AS spot_id,
            c.{schema.CLAIM_STATUS} AS claim_status,
            c.{schema.CLAIM_CLAIMED_AT} AS claimed_at,
            c.{schema.CLAIM_UPDATED_AT} AS updated_at,
            s.{schema.SPOT_LAT} AS spot_lat,
            s.{schema.SPOT_LONG} AS spot_long,
            s.{schema.SPOT_RADIUS} AS spot_radius,
            s.{schema.SPOT_CLAIM_DURATION} AS claim_duration
        FROM {schema.CLAIM_TABLE_NAME} c
        JOIN {schema.SPOT_TABLE_NAME} s
          ON s.{schema.SPOT_ID} = c.{schema.CLAIM_SPOT_ID}
        WHERE c.{schema.CLAIM_RECIPIENT} = ?
          AND s.{schema.SPOT_LAT} IS NOT NULL
          AND s.{schema.SPOT_LONG} IS NOT NULL
        ORDER BY c.{schema.CLAIM_CLAIMED_AT} DESC, c.{schema.CLAIM_ID} DESC
        LIMIT ?;
        """,
        (int(user_id), RECENT_CLAIM_LIMIT),
    )

    now = await db_access.get_unixepoch(db)
    target_radius = max(0.0, float(target_spot.get(schema.SPOT_RADIUS) or 0))
    strongest: RowDict | None = None

    for row in rows:
        claimed_at = int(row["claimed_at"] or now)
        observed_at = claimed_at
        if (
            int(row["claim_status"]) == const.CLAIM_STATUS_PENDING
            and int(row["claim_duration"] or 0) > 0
        ):
            # A pending duration claim's updated_at follows its latest location
            # heartbeat. Terminal status changes deliberately do not make an old
            # claim location appear recent.
            observed_at = max(claimed_at, int(row["updated_at"] or claimed_at))

        elapsed_seconds = max(0, int(now) - observed_at)
        centre_distance = db_access.distance_metres(
            float(row["spot_lat"]),
            float(row["spot_long"]),
            float(target_spot[schema.SPOT_LAT]),
            float(target_spot[schema.SPOT_LONG]),
        )
        minimum_travel_distance = max(
            0.0,
            float(centre_distance)
            - max(0.0, float(row["spot_radius"] or 0))
            - target_radius,
        )
        implied_speed = minimum_travel_distance / max(1, elapsed_seconds)
        detected = (
            minimum_travel_distance >= MINIMUM_TRAVEL_DISTANCE_METRES
            and implied_speed > MAXIMUM_PLAUSIBLE_SPEED_METRES_PER_SECOND
        )
        if not detected:
            continue

        candidate = {
            "detected": True,
            "previous_claim_id": int(row["claim_id"]),
            "previous_spot_id": int(row["spot_id"]),
            "current_spot_id": int(target_spot_id),
            "observed_at": observed_at,
            "checked_at": int(now),
            "elapsed_seconds": elapsed_seconds,
            "centre_distance_metres": float(centre_distance),
            "minimum_travel_distance_metres": minimum_travel_distance,
            "implied_speed_metres_per_second": implied_speed,
        }
        if (
            strongest is None
            or implied_speed > float(strongest["implied_speed_metres_per_second"])
        ):
            strongest = candidate

    return strongest or {
        "detected": False,
        "current_spot_id": int(target_spot_id),
    }


async def create_claim_with_impossible_travel_guard(
    db,
    *,
    spot_id: int,
    user_id: int,
    lat: float,
    long: float,
    accuracy: float,
    payout_address: str | None = None,
) -> int:
    """Create a claim unless recent accepted claim locations are impossible."""
    # Preserve the existing payout-address validation order. An invalid address
    # should fail normally rather than reaching the automatic-ban decision.
    clean_payout_address = db_access._clean_optional_nimiq_address(payout_address)
    check = await get_impossible_claim_travel_check(
        db,
        user_id=int(user_id),
        target_spot_id=int(spot_id),
    )
    if check.get("detected"):
        await db_access.set_user_status_to_banned(db, user_id=int(user_id))
        logger.warning(
            "Banned user %s for impossible claim travel: previous_claim=%s "
            "previous_spot=%s current_spot=%s distance=%.0fm elapsed=%ss "
            "speed=%.1fm/s",
            int(user_id),
            check.get("previous_claim_id"),
            check.get("previous_spot_id"),
            check.get("current_spot_id"),
            float(check.get("minimum_travel_distance_metres") or 0),
            int(check.get("elapsed_seconds") or 0),
            float(check.get("implied_speed_metres_per_second") or 0),
        )
        raise ValueError(
            "This device account has been banned because two recent claim "
            "locations could not have been reached in the time available."
        )

    return await _ORIGINAL_CREATE_CLAIM(
        db,
        spot_id=int(spot_id),
        user_id=int(user_id),
        lat=float(lat),
        long=float(long),
        accuracy=float(accuracy),
        payout_address=clean_payout_address,
    )


def install() -> None:
    """Install the claim creation guard once for public runtime routes."""
    global _INSTALLED
    if _INSTALLED:
        return
    db_access.create_claim = create_claim_with_impossible_travel_guard
    _INSTALLED = True


__all__ = [
    "MAXIMUM_PLAUSIBLE_SPEED_METRES_PER_SECOND",
    "MINIMUM_TRAVEL_DISTANCE_METRES",
    "RECENT_CLAIM_LIMIT",
    "create_claim_with_impossible_travel_guard",
    "get_impossible_claim_travel_check",
    "install",
]
