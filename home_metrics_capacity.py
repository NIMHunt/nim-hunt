"""Homepage Spot-count semantics.

The public Spot cache intentionally keeps published, non-expired Spots around
while settlement finishes.  That is useful for map/detail state, but it means a
Spot can remain technically current after every available claim/entry has been
taken.  The Home page's "Active Spots" metric should describe Spots a visitor
can still participate in, not settlement state.
"""

from __future__ import annotations

from typing import Any

import cache
import database as schema

RowDict = dict[str, Any]

_INSTALLED = False


def _spot_has_claim_capacity(record: cache.SpotCacheRecord) -> bool:
    """Return whether a cached current Spot can still accept another claim."""
    spot = record.spot
    max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if max_total <= 0:
        return True

    successful = int(spot.get("success_claim_count") or 0)
    if record.prizedraw is None:
        # Standard duration claims do not reserve capacity while pending; only
        # successful claims consume the configured total allowance.
        used = successful
    else:
        # Pending Prizedraw claims are already valid entries in the draw and do
        # consume participant capacity.
        used = successful + int(spot.get("pending_claim_count") or 0)

    return used < max_total


async def get_cached_home_metrics_with_claim_capacity(db) -> RowDict:
    """Return Home counters, excluding current Spots that are already full."""
    snapshot = await cache.ensure_spot_cache(db)
    active_spot_count = 0
    for spot_id in snapshot.current_spot_ids_by_start:
        record = snapshot.spots_by_id.get(int(spot_id))
        if record is not None and _spot_has_claim_capacity(record):
            active_spot_count += 1

    daily_users = await cache.get_cached_daily_user_count(db)
    return {
        "active_spot_count": int(active_spot_count),
        "daily_user_count": int(daily_users or 0),
    }


def install() -> None:
    """Make the capacity-aware Home metric authoritative at runtime."""
    global _INSTALLED
    if _INSTALLED:
        return
    cache.get_cached_home_metrics = get_cached_home_metrics_with_claim_capacity
    _INSTALLED = True


__all__ = [
    "_spot_has_claim_capacity",
    "get_cached_home_metrics_with_claim_capacity",
    "install",
]
