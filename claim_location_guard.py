"""Block implausible travel between claim-validated Spot locations.

The browser may report location while a user explores the map, but harmless
browsing is not relevant to abuse prevention. This guard therefore considers
only locations that reach NimHunt's normal claim-creation boundary.

A first moderately implausible jump starts a global claim cooldown. A second
distinct jump during that cooldown bans only when it is inconsistent with both
the last trusted claim and the original suspicious Spot. This mercy rule keeps
one bad GPS reading from turning a legitimate return to the trusted location
into an automatic ban.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Awaitable, Callable

import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]
ClaimCreator = Callable[..., Awaitable[int]]

SUSPICION_METADATA_KEY_PREFIX = "claim_location_suspicion:"
RECENT_CLAIM_LIMIT = int(getattr(const, "CLAIM_LOCATION_RECENT_CLAIM_LIMIT", 5))
HARD_BAN_MIN_DISTANCE_METRES = float(
    getattr(const, "CLAIM_LOCATION_HARD_BAN_MIN_DISTANCE_METRES", 3_000)
)
HARD_BAN_MAX_SPEED_METRES_PER_SECOND = float(
    getattr(const, "CLAIM_LOCATION_HARD_BAN_MAX_SPEED_METRES_PER_SECOND", 500)
)
SOFT_COOLDOWN_MIN_DISTANCE_METRES = float(
    getattr(const, "CLAIM_LOCATION_SOFT_COOLDOWN_MIN_DISTANCE_METRES", 1_000)
)
SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND = float(
    getattr(const, "CLAIM_LOCATION_SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND", 75)
)

_ORIGINAL_CREATE_CLAIM: ClaimCreator = db_access.create_claim
_INSTALLED = False
logger = logging.getLogger(__name__)


class ClaimLocationCooldownError(ValueError):
    """Raised after a suspicious jump starts or remains inside a cooldown."""

    code = "claim_location_cooldown"

    def __init__(self, *, retry_at: int, checked_at: int):
        self.retry_at = int(retry_at)
        self.retry_after_seconds = max(1, self.retry_at - int(checked_at))
        wait = _format_wait(self.retry_after_seconds)
        super().__init__(
            "This claim has been postponed because this Spot is too far from "
            "your recent claim location to verify yet. You cannot claim any "
            f"Spot for another {wait}."
        )


def _format_wait(seconds: int) -> str:
    seconds = max(1, int(seconds))
    minutes, remaining = divmod(seconds, 60)
    if minutes <= 0:
        return f"{remaining} second" if remaining == 1 else f"{remaining} seconds"
    minute_text = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    if remaining <= 0:
        return minute_text
    second_text = f"{remaining} second" if remaining == 1 else f"{remaining} seconds"
    return f"{minute_text} {second_text}"


def _suspicion_key(user_id: int) -> str:
    return f"{SUSPICION_METADATA_KEY_PREFIX}{int(user_id)}"


async def _load_suspicion(db, *, user_id: int) -> RowDict | None:
    cur = await db.execute(
        f"""
        SELECT {schema.APP_METADATA_VALUE} AS value
        FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} = ?;
        """,
        (_suspicion_key(user_id),),
    )
    row = await cur.fetchone()
    if row is None:
        return None

    try:
        value = json.loads(str(row["value"]))
        required = {
            "trusted_claim_id",
            "suspicious_spot_id",
            "attempted_at",
            "retry_at",
            "last_attempted_spot_id",
            "last_attempted_at",
        }
        if not isinstance(value, dict) or not required.issubset(value):
            raise ValueError("incomplete suspicion marker")
        return {name: int(value[name]) for name in required}
    except (TypeError, ValueError):
        logger.warning("Discarding malformed claim-location marker for user %s", user_id)
        await _clear_suspicion(db, user_id=user_id)
        return None


async def _save_suspicion(db, *, user_id: int, value: RowDict) -> None:
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
        (_suspicion_key(user_id), payload),
    )


async def _clear_suspicion(db, *, user_id: int) -> None:
    await db.execute(
        f"""
        DELETE FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} = ?;
        """,
        (_suspicion_key(user_id),),
    )


async def get_claim_location_suspicion(db, *, user_id: int) -> RowDict | None:
    """Return the current durable cooldown marker for tests/admin diagnostics."""
    return await _load_suspicion(db, user_id=user_id)


def _claim_observed_at(row: RowDict, *, now: int) -> int:
    claimed_at = int(row.get("claimed_at") or now)
    if (
        int(row.get("claim_status") or -1) == const.CLAIM_STATUS_PENDING
        and int(row.get("claim_duration") or 0) > 0
    ):
        return max(claimed_at, int(row.get("updated_at") or claimed_at))
    return claimed_at


async def _recent_claim_anchors(db, *, user_id: int) -> list[RowDict]:
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
    return [dict(row) for row in rows]


async def _claim_anchor(db, *, user_id: int, claim_id: int) -> RowDict | None:
    cur = await db.execute(
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
        WHERE c.{schema.CLAIM_ID} = ?
          AND c.{schema.CLAIM_RECIPIENT} = ?;
        """,
        (int(claim_id), int(user_id)),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _spot_anchor(db, *, spot_id: int) -> RowDict | None:
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if (
        spot is None
        or spot.get(schema.SPOT_LAT) is None
        or spot.get(schema.SPOT_LONG) is None
    ):
        return None
    return {
        "spot_id": int(spot_id),
        "spot_lat": float(spot[schema.SPOT_LAT]),
        "spot_long": float(spot[schema.SPOT_LONG]),
        "spot_radius": max(0.0, float(spot.get(schema.SPOT_RADIUS) or 0)),
    }


def _travel_metrics(
    previous: RowDict,
    target: RowDict,
    *,
    observed_at: int,
    checked_at: int,
) -> RowDict:
    centre_distance = db_access.distance_metres(
        float(previous["spot_lat"]),
        float(previous["spot_long"]),
        float(target["spot_lat"]),
        float(target["spot_long"]),
    )
    minimum_distance = max(
        0.0,
        float(centre_distance)
        - max(0.0, float(previous.get("spot_radius") or 0))
        - max(0.0, float(target.get("spot_radius") or 0)),
    )

    # A backwards server clock is not evidence against the user. Same-second
    # attempts still use a one-second denominator and can be assessed normally.
    if int(checked_at) < int(observed_at):
        return {
            "elapsed_seconds": 0,
            "centre_distance_metres": float(centre_distance),
            "minimum_travel_distance_metres": minimum_distance,
            "implied_speed_metres_per_second": 0.0,
            "hard_violation": False,
            "soft_violation": False,
            "retry_at": int(checked_at),
            "clock_anomaly": True,
        }

    elapsed_seconds = max(0, int(checked_at) - int(observed_at))
    implied_speed = minimum_distance / max(1, elapsed_seconds)
    hard_violation = (
        minimum_distance >= HARD_BAN_MIN_DISTANCE_METRES
        and implied_speed > HARD_BAN_MAX_SPEED_METRES_PER_SECOND
    )
    soft_violation = (
        minimum_distance >= SOFT_COOLDOWN_MIN_DISTANCE_METRES
        and implied_speed > SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND
    )
    required_seconds = math.ceil(
        minimum_distance / SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND
    ) + 1
    return {
        "elapsed_seconds": elapsed_seconds,
        "centre_distance_metres": float(centre_distance),
        "minimum_travel_distance_metres": minimum_distance,
        "implied_speed_metres_per_second": implied_speed,
        "hard_violation": hard_violation,
        "soft_violation": soft_violation,
        "retry_at": int(observed_at) + max(1, required_seconds),
        "clock_anomaly": False,
    }


async def _trusted_travel_decision(
    db,
    *,
    user_id: int,
    target: RowDict,
    checked_at: int,
) -> RowDict:
    hard: RowDict | None = None
    soft: RowDict | None = None
    for row in await _recent_claim_anchors(db, user_id=user_id):
        observed_at = _claim_observed_at(row, now=checked_at)
        metrics = _travel_metrics(
            row,
            target,
            observed_at=observed_at,
            checked_at=checked_at,
        )
        candidate = {
            **metrics,
            "previous_claim_id": int(row["claim_id"]),
            "previous_spot_id": int(row["spot_id"]),
            "current_spot_id": int(target["spot_id"]),
            "observed_at": observed_at,
            "checked_at": int(checked_at),
        }
        if metrics["hard_violation"] and (
            hard is None
            or float(candidate["implied_speed_metres_per_second"])
            > float(hard["implied_speed_metres_per_second"])
        ):
            hard = candidate
        if metrics["soft_violation"] and (
            soft is None or int(candidate["retry_at"]) > int(soft["retry_at"])
        ):
            soft = candidate

    if hard is not None:
        return {"action": "ban", "reason": "hard_trusted_jump", **hard}
    if soft is not None:
        return {"action": "new_cooldown", "reason": "soft_trusted_jump", **soft}
    return {"action": "allow", "current_spot_id": int(target["spot_id"])}


async def _active_cooldown_decision(
    db,
    *,
    user_id: int,
    target: RowDict,
    suspicion: RowDict,
    checked_at: int,
) -> RowDict | None:
    retry_at = int(suspicion["retry_at"])
    if int(checked_at) >= retry_at:
        await _clear_suspicion(db, user_id=user_id)
        return None

    target_spot_id = int(target["spot_id"])
    if target_spot_id == int(suspicion["last_attempted_spot_id"]):
        return {
            "action": "cooldown",
            "reason": "consecutive_duplicate",
            "retry_at": retry_at,
            "checked_at": int(checked_at),
        }

    trusted = await _claim_anchor(
        db,
        user_id=user_id,
        claim_id=int(suspicion["trusted_claim_id"]),
    )
    original = await _spot_anchor(
        db,
        spot_id=int(suspicion["suspicious_spot_id"]),
    )
    previous_attempt = await _spot_anchor(
        db,
        spot_id=int(suspicion["last_attempted_spot_id"]),
    )
    if trusted is None or original is None or previous_attempt is None:
        await _clear_suspicion(db, user_id=user_id)
        return None

    previous_metrics = _travel_metrics(
        previous_attempt,
        target,
        observed_at=int(suspicion["last_attempted_at"]),
        checked_at=checked_at,
    )
    trusted_metrics = _travel_metrics(
        trusted,
        target,
        observed_at=_claim_observed_at(trusted, now=checked_at),
        checked_at=checked_at,
    )
    original_metrics = _travel_metrics(
        original,
        target,
        observed_at=int(suspicion["attempted_at"]),
        checked_at=checked_at,
    )

    consistent_with_trusted = not bool(trusted_metrics["soft_violation"])
    consistent_with_original = not bool(original_metrics["soft_violation"])
    if (
        bool(previous_metrics["soft_violation"])
        and not consistent_with_trusted
        and not consistent_with_original
    ):
        return {
            "action": "ban",
            "reason": "second_suspicious_jump",
            "previous_spot_id": int(previous_attempt["spot_id"]),
            "current_spot_id": target_spot_id,
            "checked_at": int(checked_at),
            **previous_metrics,
        }

    updated = dict(suspicion)
    updated["last_attempted_spot_id"] = target_spot_id
    updated["last_attempted_at"] = int(checked_at)
    await _save_suspicion(db, user_id=user_id, value=updated)
    return {
        "action": "cooldown",
        "reason": (
            "plausible_cooldown_movement"
            if not bool(previous_metrics["soft_violation"])
            else "mercy_rule"
        ),
        "retry_at": retry_at,
        "checked_at": int(checked_at),
    }


async def get_claim_location_decision(
    db,
    *,
    user_id: int,
    target_spot_id: int,
) -> RowDict:
    """Evaluate and persist the location-safety decision for one valid attempt."""
    target = await _spot_anchor(db, spot_id=int(target_spot_id))
    if target is None:
        return {"action": "allow", "current_spot_id": int(target_spot_id)}

    checked_at = await db_access.get_unixepoch(db)
    trusted_decision = await _trusted_travel_decision(
        db,
        user_id=int(user_id),
        target=target,
        checked_at=checked_at,
    )
    if trusted_decision["action"] == "ban":
        return trusted_decision

    suspicion = await _load_suspicion(db, user_id=int(user_id))
    if suspicion is not None:
        active = await _active_cooldown_decision(
            db,
            user_id=int(user_id),
            target=target,
            suspicion=suspicion,
            checked_at=checked_at,
        )
        if active is not None:
            return active

    if trusted_decision["action"] == "new_cooldown":
        marker = {
            "trusted_claim_id": int(trusted_decision["previous_claim_id"]),
            "suspicious_spot_id": int(target_spot_id),
            "attempted_at": int(checked_at),
            "retry_at": int(trusted_decision["retry_at"]),
            "last_attempted_spot_id": int(target_spot_id),
            "last_attempted_at": int(checked_at),
        }
        await _save_suspicion(db, user_id=int(user_id), value=marker)
        logger.warning(
            "Postponed claim for suspicious travel: user=%s previous_claim=%s "
            "previous_spot=%s current_spot=%s distance=%.0fm elapsed=%ss "
            "speed=%.1fm/s retry_at=%s",
            int(user_id),
            trusted_decision.get("previous_claim_id"),
            trusted_decision.get("previous_spot_id"),
            int(target_spot_id),
            float(trusted_decision.get("minimum_travel_distance_metres") or 0),
            int(trusted_decision.get("elapsed_seconds") or 0),
            float(trusted_decision.get("implied_speed_metres_per_second") or 0),
            int(trusted_decision["retry_at"]),
        )
        return {
            **trusted_decision,
            "action": "cooldown",
            "reason": "new_suspicious_cooldown",
        }

    return trusted_decision


async def _ban_for_decision(db, *, user_id: int, decision: RowDict) -> None:
    await db_access.set_user_status_to_banned(db, user_id=int(user_id))
    await _clear_suspicion(db, user_id=int(user_id))
    logger.warning(
        "Banned user %s for impossible claim travel: reason=%s previous_claim=%s "
        "previous_spot=%s current_spot=%s distance=%.0fm elapsed=%ss speed=%.1fm/s",
        int(user_id),
        decision.get("reason"),
        decision.get("previous_claim_id"),
        decision.get("previous_spot_id"),
        decision.get("current_spot_id"),
        float(decision.get("minimum_travel_distance_metres") or 0),
        int(decision.get("elapsed_seconds") or 0),
        float(decision.get("implied_speed_metres_per_second") or 0),
    )
    raise ValueError(
        "This device account has been banned because recent claim locations "
        "could not have been reached in the time available."
    )


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
    """Create a claim unless trusted travel or an active cooldown blocks it."""
    # Preserve the existing payout-address validation order. An invalid address
    # should fail normally rather than reaching an automatic security decision.
    clean_payout_address = db_access._clean_optional_nimiq_address(payout_address)
    decision = await get_claim_location_decision(
        db,
        user_id=int(user_id),
        target_spot_id=int(spot_id),
    )
    if decision["action"] == "ban":
        await _ban_for_decision(db, user_id=int(user_id), decision=decision)
    if decision["action"] == "cooldown":
        raise ClaimLocationCooldownError(
            retry_at=int(decision["retry_at"]),
            checked_at=int(decision.get("checked_at") or await db_access.get_unixepoch(db)),
        )

    claim_id = await _ORIGINAL_CREATE_CLAIM(
        db,
        spot_id=int(spot_id),
        user_id=int(user_id),
        lat=float(lat),
        long=float(long),
        accuracy=float(accuracy),
        payout_address=clean_payout_address,
    )
    await _clear_suspicion(db, user_id=int(user_id))
    return int(claim_id)


def install() -> None:
    """Install the claim-creation guard once for public runtime routes."""
    global _INSTALLED
    if _INSTALLED:
        return
    db_access.create_claim = create_claim_with_impossible_travel_guard
    _INSTALLED = True


__all__ = [
    "ClaimLocationCooldownError",
    "HARD_BAN_MAX_SPEED_METRES_PER_SECOND",
    "HARD_BAN_MIN_DISTANCE_METRES",
    "RECENT_CLAIM_LIMIT",
    "SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND",
    "SOFT_COOLDOWN_MIN_DISTANCE_METRES",
    "create_claim_with_impossible_travel_guard",
    "get_claim_location_decision",
    "get_claim_location_suspicion",
    "install",
]
