"""Fresh-identity signer history and first-location claim safeguards.

The durable records live in ``app_metadata``, alongside the existing impossible-
travel cooldown.  This avoids changing USER moderation semantics and permits an
additive rollout on existing SQLite databases.  Password-protected Spots bypass
only decisions made here; the ordinary rule check and impossible-travel guard
remain authoritative.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

import constants as const
import database as schema
import db_access
import trans_updater

logger = logging.getLogger(__name__)
ROLLOUT_KEY = "fresh_claim_guard:activated_at"
ACTIVITY_ROLLOUT_KEY = "fresh_claim_guard:activity_rollout"
SIGNER_PREFIX = "fresh_claim_guard:signer:"
LOCATION_PREFIX = "fresh_claim_guard:location:"
ACTIVITY_PREFIX = "fresh_claim_guard:activity:"
GPS_PREFIX = "fresh_claim_guard:gps:"
GENERIC_MESSAGE = "Public Spots are temporarily unavailable for this account. Please try again later."


def validate_ip_geolocation_configuration() -> None:
    """Reject an unusable public-provider URL without exposing credentials."""
    template = os.getenv("NIMHUNT_IP_GEOLOCATION_URL", "").strip()
    try:
        parsed = urlparse(template)
    except ValueError as exc:
        raise RuntimeError(
            "NIMHUNT_IP_GEOLOCATION_URL must be a valid HTTPS URL containing {ip}"
        ) from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or "{ip}" not in template:
        raise RuntimeError(
            "NIMHUNT_IP_GEOLOCATION_URL must be a valid HTTPS URL containing {ip}"
        )


def _key(prefix: str, value: str | int) -> str:
    digest = hashlib.sha256(str(value).encode()).hexdigest()
    return f"{prefix}{digest}"


async def _get(db, key: str) -> Any:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?", (key,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row[0]))
    except (TypeError, json.JSONDecodeError):
        return None


async def _set(db, key: str, value: Any) -> None:
    await db.execute(
        f"INSERT INTO {schema.APP_METADATA_TABLE_NAME} "
        f"({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) VALUES (?, ?) "
        f"ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET "
        f"{schema.APP_METADATA_VALUE}=excluded.{schema.APP_METADATA_VALUE}",
        (key, json.dumps(value, separators=(",", ":"), sort_keys=True)),
    )


async def ensure_rollout_marker(db) -> dict[str, int]:
    """Atomically mark deployment time; users already present are legacy."""
    now = await db_access.get_unixepoch(db)
    cur = await db.execute(f"SELECT COALESCE(MAX({schema.USER_ID}), 0) FROM {schema.USER_TABLE_NAME}")
    legacy_max_user_id = int((await cur.fetchone())[0])
    marker = {"activated_at": int(now), "legacy_max_user_id": legacy_max_user_id}
    await db.execute(
        f"INSERT OR IGNORE INTO {schema.APP_METADATA_TABLE_NAME} "
        f"({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) VALUES (?, ?)",
        (ROLLOUT_KEY, json.dumps(marker, separators=(",", ":"), sort_keys=True)),
    )
    value = await _get(db, ROLLOUT_KEY)
    if isinstance(value, dict):
        return {"activated_at": int(value.get("activated_at") or now),
                "legacy_max_user_id": int(value.get("legacy_max_user_id") or 0)}
    # Compatibility with an early scalar marker: time remains a safe fallback.
    return {"activated_at": int(value or now), "legacy_max_user_id": 0}


async def ensure_activity_rollout_marker(db) -> dict[str, int]:
    """Grandfather accounts present when the active-day rule is deployed."""
    now = await db_access.get_unixepoch(db)
    cur = await db.execute(f"SELECT COALESCE(MAX({schema.USER_ID}), 0) FROM {schema.USER_TABLE_NAME}")
    marker = {"activated_at": int(now), "legacy_max_user_id": int((await cur.fetchone())[0])}
    await db.execute(
        f"INSERT OR IGNORE INTO {schema.APP_METADATA_TABLE_NAME} "
        f"({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}) VALUES (?, ?)",
        (ACTIVITY_ROLLOUT_KEY, json.dumps(marker, separators=(",", ":"), sort_keys=True)),
    )
    value = await _get(db, ACTIVITY_ROLLOUT_KEY)
    return value if isinstance(value, dict) else marker


async def record_meaningful_activity(db, *, user_id: int, now: int | None = None) -> None:
    """Record at most the first two authenticated app-session calendar days."""
    now = int(now if now is not None else await db_access.get_unixepoch(db))
    day = now // 86_400
    key = _key(ACTIVITY_PREFIX, user_id)
    async with db_access.transaction(db, immediate=True):
        state = await _get(db, key)
        days = list(state.get("days", [])) if isinstance(state, dict) else []
        if day not in days and len(days) < 2:
            days.append(day)
            updated = dict(state) if isinstance(state, dict) else {}
            updated.update({"days": sorted(days), "updated_at": now})
            await _set(db, key, updated)


async def record_first_public_claim_attempt(
    db, *, user_id: int, now: int | None = None
) -> int:
    """Atomically freeze the first signed public-claim attempt timestamp."""
    now = int(now if now is not None else await db_access.get_unixepoch(db))
    key = _key(ACTIVITY_PREFIX, user_id)
    async with db_access.transaction(db, immediate=True):
        state = await _get(db, key)
        state = dict(state) if isinstance(state, dict) else {"days": []}
        first_attempt_at = int(state.get("first_public_claim_attempt_at") or now)
        if "first_public_claim_attempt_at" not in state:
            state["first_public_claim_attempt_at"] = first_attempt_at
            await _set(db, key, state)
    return first_attempt_at


async def _account_age_trusted(db, *, user: dict[str, Any], now: int) -> bool:
    if int(now) - int(user[schema.USER_CREATED_AT]) < const.CLAIM_IDENTITY_TRUST_AGE_SECONDS:
        return False
    rollout = await _get(db, ACTIVITY_ROLLOUT_KEY)
    if not isinstance(rollout, dict):
        if db.in_transaction:
            rollout = await ensure_activity_rollout_marker(db)
        else:
            async with db_access.transaction(db, immediate=True):
                rollout = await ensure_activity_rollout_marker(db)
    if int(user[schema.USER_ID]) <= int(rollout.get("legacy_max_user_id") or 0):
        return True
    state = await _get(db, _key(ACTIVITY_PREFIX, int(user[schema.USER_ID])))
    days = state.get("days", []) if isinstance(state, dict) else []
    cutoff_at = (
        int(state.get("first_public_claim_attempt_at") or now)
        if isinstance(state, dict) else int(now)
    )
    cutoff = cutoff_at // 86_400
    return len(set(int(day) for day in days if int(day) < cutoff)) >= 2


async def _inside_active_public_spot(
    db, *, user_id: int, lat: float, long: float, now: int
) -> bool:
    """Return whether the user is inside another creator's public reward."""
    rows = await db.execute_fetchall(
        f"SELECT * FROM {schema.SPOT_VIEW_PUBLIC_LIST} "
        f"WHERE {schema.SPOT_STATUS}=? "
        f"AND {schema.SPOT_CREATED_BY}<>? "
        f"AND {schema.SPOT_USE_PASSWORD}=0 AND {schema.SPOT_LAT} IS NOT NULL "
        f"AND availability_rank=0",
        (const.SPOT_STATUS_PUBLISHED, int(user_id)),
    )
    return any(
        db_access.spot_summary_has_public_claim_capacity(dict(row))
        and db_access.distance_metres(
            lat, long, row[schema.SPOT_LAT], row[schema.SPOT_LONG]
        ) <= float(row[schema.SPOT_RADIUS])
        for row in rows
    )


async def record_gps_observation(db, *, user_id: int, ip: str | None,
                                 lat: float, long: float, now: int | None = None) -> dict[str, Any]:
    """Keep one bounded Find-Spots GPS anchor and explicit weak evidence."""
    now = int(now if now is not None else await db_access.get_unixepoch(db))
    key = _key(GPS_PREFIX, user_id)
    ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None
    async with db_access.transaction(db, immediate=True):
        state = await _get(db, key)
        state = dict(state) if isinstance(state, dict) else {}
        if "first_observed_at" not in state:
            # This local query runs only for the first observation and under the
            # same short transaction that freezes its classification. Owned
            # Spots are excluded without making overlapping third-party Spots safe.
            inside = await _inside_active_public_spot(
                db, user_id=user_id, lat=lat, long=long, now=now
            )
            state.update({"first_observed_at": now, "first_inside_public_spot": inside,
                          "ordinary_presence_before_reward": not inside})
        previous_at = int(state.get("last_observed_at") or 0)
        contradiction = False
        state_changed = False
        pending_continuation = False
        previous_distance = None
        if previous_at:
            previous_distance = db_access.distance_metres(
                float(state["last_lat"]), float(state["last_long"]), lat, long)
        pending_at = int(state.get("pending_suspicious_at") or 0)
        if pending_at and state.get("pending_suspicious_ip_hash") != ip_hash:
            for field in ("pending_suspicious_at", "pending_suspicious_lat",
                          "pending_suspicious_long", "pending_suspicious_ip_hash"):
                state.pop(field, None)
            pending_at = 0
            state_changed = True
        if (ip_hash and state.get("last_ip_hash") == ip_hash and previous_at
                and now >= previous_at):
            distance = float(previous_distance)
            speed = distance / max(1, now - previous_at)
            contradicts_stable = (
                distance >= const.CLAIM_SAME_IP_GPS_MIN_DISTANCE_METRES
                and speed > const.CLAIM_SAME_IP_GPS_MAX_SPEED_METRES_PER_SECOND
            )
            if pending_at:
                suspicious_distance = db_access.distance_metres(
                    float(state["pending_suspicious_lat"]),
                    float(state["pending_suspicious_long"]), lat, long,
                )
                contradicts_suspicious = (
                    suspicious_distance >= const.CLAIM_SAME_IP_GPS_MIN_DISTANCE_METRES
                    and suspicious_distance / max(1, now - pending_at)
                    > const.CLAIM_SAME_IP_GPS_MAX_SPEED_METRES_PER_SECOND
                )
                if not contradicts_stable:
                    # Returning to the stable anchor retires the one bounded bad
                    # excursion without manufacturing a second contradiction.
                    for field in ("pending_suspicious_at", "pending_suspicious_lat",
                                  "pending_suspicious_long", "pending_suspicious_ip_hash"):
                        state.pop(field, None)
                    pending_at = 0
                    state_changed = True
                elif contradicts_suspicious:
                    contradiction = True
                else:
                    pending_continuation = True
            elif contradicts_stable:
                contradiction = True

            if contradiction:
                state["same_ip_contradiction_count"] = int(
                    state.get("same_ip_contradiction_count") or 0
                ) + 1
                state["last_contradiction_at"] = now
                if not pending_at:
                    state.update({
                        "pending_suspicious_at": now,
                        "pending_suspicious_lat": round(float(lat), 3),
                        "pending_suspicious_long": round(float(long), 3),
                        "pending_suspicious_ip_hash": ip_hash,
                    })
                if (int(state["same_ip_contradiction_count"]) >= 2
                        or (bool(state.get("first_inside_public_spot"))
                            and not bool(state.get("ordinary_presence_before_reward")))):
                    state["restricted_until"] = max(
                        int(state.get("restricted_until") or 0),
                        now + const.CLAIM_BEHAVIOURAL_RESTRICTION_SECONDS,
                    )
                # A genuinely distinct second point becomes the new stable
                # anchor; the old stable + pending pair has served its purpose.
                if pending_at:
                    for field in ("pending_suspicious_at", "pending_suspicious_lat",
                                  "pending_suspicious_long", "pending_suspicious_ip_hash"):
                        state.pop(field, None)
                    previous_at = 0
        should_refresh = (
            not previous_at
            or state.get("last_ip_hash") != ip_hash
            or float(previous_distance or 0) >= const.CLAIM_GPS_ANCHOR_MIN_MOVEMENT_METRES
            or now - previous_at >= const.CLAIM_GPS_ANCHOR_REFRESH_SECONDS
            or contradiction
            or state_changed
        )
        if pending_continuation:
            should_refresh = False
        # Keep the stable anchor on a first contradiction. Identical retries are
        # therefore compared with both the stable and pending points and dedupe.
        first_pending_contradiction = contradiction and state.get("pending_suspicious_at") == now
        if (now >= previous_at and should_refresh and not first_pending_contradiction
                and not pending_continuation):
            state.update({"last_observed_at": now, "last_lat": round(float(lat), 3),
                          "last_long": round(float(long), 3), "last_ip_hash": ip_hash})
        if should_refresh:
            await _set(db, key, state)
    return {"contradiction": contradiction, "first_inside_public_spot": bool(state["first_inside_public_spot"])}


async def _behaviour_allows_public_claim(db, *, user_id: int, now: int) -> bool:
    state = await _get(db, _key(GPS_PREFIX, user_id))
    return not isinstance(state, dict) or int(state.get("restricted_until") or 0) <= now


async def _recent_weak_behaviour_anomaly(db, *, user_id: int, now: int) -> bool:
    """Return one independent, recent anomaly that is insufficient by itself."""
    state = await _get(db, _key(GPS_PREFIX, user_id))
    if not isinstance(state, dict) or int(state.get("same_ip_contradiction_count") or 0) < 1:
        return False
    observed_at = int(state.get("last_contradiction_at") or 0)
    return observed_at <= now < observed_at + const.CLAIM_BEHAVIOURAL_RESTRICTION_SECONDS


def _transaction_timestamp(tx: dict[str, Any]) -> int | None:
    value = trans_updater._first_chain_scalar_for_keys(tx, {"timestamp", "time"})
    millis = trans_updater._normalise_chain_timestamp_milliseconds(value)
    return None if millis is None else int(millis // 1000)


def _confirmed(tx: dict[str, Any]) -> bool:
    block = trans_updater._first_chain_scalar_for_keys(
        tx, {"blockNumber", "block_number", "blockHeight", "block_height"}
    )
    execution = trans_updater._first_chain_scalar_for_keys(tx, {"executionResult"})
    try:
        return int(block) > 0 and execution is not False
    except (TypeError, ValueError):
        return False


async def _oldest_confirmed_activity(address: str) -> int | None:
    """Walk the newest-first RPC cursor until history is exhausted."""
    cursor = None
    oldest = None
    seen: set[str] = set()
    for _ in range(max(1, const.CLAIM_SIGNER_HISTORY_MAX_PAGES)):
        result = await trans_updater.get_chain_transactions_by_address(
            address,
            max_transactions=max(1, const.CLAIM_SIGNER_HISTORY_PAGE_SIZE),
            start_at=cursor,
            timeout_seconds=const.IP_GEOLOCATION_TIMEOUT_SECONDS,
        )
        transactions = list(trans_updater._iter_candidate_transactions(result))
        if not transactions:
            return oldest
        for tx in transactions:
            if _confirmed(tx):
                timestamp = _transaction_timestamp(tx)
                if timestamp is not None:
                    oldest = timestamp if oldest is None else min(oldest, timestamp)
        last_hash = trans_updater._extract_chain_hash(transactions[-1])
        if len(transactions) < const.CLAIM_SIGNER_HISTORY_PAGE_SIZE:
            return oldest
        if not last_hash or last_hash in seen:
            raise RuntimeError("Nimiq transaction-history pagination did not advance")
        seen.add(last_hash)
        cursor = last_hash
    raise RuntimeError("Nimiq transaction history exceeded the configured page limit")


def _cached_signer_decision(cached: Any, *, address: str, now: int) -> dict[str, Any] | None:
    if not isinstance(cached, dict) or cached.get("address") != address:
        return None
    if cached.get("trusted") is True:
        return {"trusted": True, "reason": "signer_established"}
    if int(cached.get("refresh_at") or 0) <= int(now):
        return None
    reason = ("signer_history_unavailable"
              if cached.get("result") == "provider_failure"
              else "signer_not_yet_trusted")
    return {"trusted": False, "reason": reason}


async def signer_or_account_trusted(db, *, user: dict[str, Any], signer_address: str, now: int) -> dict[str, Any]:
    """Resolve signer trust without holding a SQLite write lock over RPC I/O."""
    age = max(0, int(now) - int(user[schema.USER_CREATED_AT]))
    if age >= const.CLAIM_IDENTITY_TRUST_AGE_SECONDS and await _account_age_trusted(db, user=user, now=now):
        return {"trusted": True, "reason": "account_established"}
    address = trans_updater._validate_nimiq_address(
        signer_address, field_name="claim signer address"
    )
    cache_key = _key(SIGNER_PREFIX, address)
    cached = await _get(db, cache_key)
    decision = _cached_signer_decision(cached, address=address, now=now)
    if decision is not None:
        return decision
    if db.in_transaction:
        raise RuntimeError("signer-history lookup requires a connection outside a transaction")
    lookup_started_at = int(now)
    try:
        oldest = await _oldest_confirmed_activity(address)
    except Exception:
        logger.warning("Signer history unavailable for user=%s", user[schema.USER_ID])
        lookup_result = "provider_failure"
        oldest = None
    else:
        lookup_result = "history"
    trusted = oldest is not None and oldest <= now - const.CLAIM_IDENTITY_TRUST_AGE_SECONDS
    refresh_at = (now + const.CLAIM_SIGNER_HISTORY_FAILURE_RETRY_SECONDS
                  if lookup_result == "provider_failure"
                  else now + const.CLAIM_SIGNER_HISTORY_NEGATIVE_CACHE_SECONDS)
    if oldest is not None and not trusted:
        refresh_at = min(refresh_at, oldest + const.CLAIM_IDENTITY_TRUST_AGE_SECONDS + 1)
    async with db_access.transaction(db, immediate=True):
        # Re-read after RPC I/O. Never let a stale failure/negative overwrite a
        # positive result or another request's newer completed lookup.
        current_user = await db_access.get_user_by_id(db, user_id=int(user[schema.USER_ID]))
        if current_user is None or int(current_user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:
            return {"trusted": False, "reason": "user_not_allowed"}
        current = await _get(db, cache_key)
        current_decision = _cached_signer_decision(current, address=address, now=now)
        if current_decision is not None and current.get("trusted") is True:
            return current_decision
        if (not trusted and current_decision is not None
                and int(current.get("checked_at") or 0) >= lookup_started_at):
            return current_decision
        await _set(db, cache_key, {
            "address": address, "trusted": trusted, "result": lookup_result,
            "oldest_confirmed_at": oldest, "checked_at": now,
            "refresh_at": None if trusted else refresh_at,
        })
    if lookup_result == "provider_failure":
        return {"trusted": False, "reason": "signer_history_unavailable"}
    return {"trusted": trusted, "reason": "signer_established" if trusted else "signer_not_yet_trusted"}


def genuine_client_ip(request_or_scope: Any) -> str | None:
    """Use Uvicorn's proxy-resolved peer, never a raw request header.

    Railway is the only public ingress and ``railway.json`` enables Uvicorn's
    proxy-header middleware. It validates/normalises forwarding data before it
    becomes ``request.client`` / ASGI ``scope['client']``. Application code must
    not parse an attacker-supplied X-Forwarded-For chain itself.
    """
    if isinstance(request_or_scope, dict):
        client = request_or_scope.get("client")
        value = client[0] if isinstance(client, (list, tuple)) and client else None
    else:
        client = getattr(request_or_scope, "client", None)
        value = getattr(client, "host", None)
    try:
        parsed = ipaddress.ip_address(str(value))
    except ValueError:
        return None
    if not parsed.is_global:
        return None
    return parsed.compressed


def _fetch_ip_location_sync(ip: str) -> dict[str, Any]:
    template = os.getenv("NIMHUNT_IP_GEOLOCATION_URL", "").strip()
    if not template or not template.lower().startswith("https://") or "{ip}" not in template:
        raise RuntimeError("NIMHUNT_IP_GEOLOCATION_URL is not configured as HTTPS")
    request = urllib.request.Request(template.format(ip=ip), headers={"Accept": "application/json"})
    key = os.getenv("NIMHUNT_IP_GEOLOCATION_API_KEY", "").strip()
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(request, timeout=const.IP_GEOLOCATION_TIMEOUT_SECONDS) as response:
        result = json.loads(response.read(64 * 1024).decode())
    if not isinstance(result, dict):
        raise ValueError("IP geolocation returned an invalid object")
    return result


async def lookup_ip_location(ip: str) -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_ip_location_sync, ip)


def _country(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _location_state_decision(state: Any, *, now: int) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    if int(state.get("retry_at") or 0) > now:
        return {
            "allowed": False,
            "reason": str(
                state.get("restriction_reason")
                or "first_location_mismatch_restriction"
            ),
        }
    if state.get("verified_at"):
        return {"allowed": True, "reason": "first_location_verified"}
    return None


async def first_location_decision(db, *, user: dict[str, Any], ip: str | None,
                                  gps_lat: float, gps_long: float,
                                  gps_country: str | None, now: int) -> dict[str, Any]:
    rollout = await _get(db, ROLLOUT_KEY)
    if not isinstance(rollout, dict):
        async with db_access.transaction(db, immediate=True):
            rollout = await ensure_rollout_marker(db)
    if (int(user[schema.USER_ID]) <= rollout["legacy_max_user_id"] or
            int(user[schema.USER_CREATED_AT]) < rollout["activated_at"]):
        return {"allowed": True, "reason": "legacy_account"}
    key = _key(LOCATION_PREFIX, int(user[schema.USER_ID]))
    state = await _get(db, key)
    existing = _location_state_decision(state, now=now)
    if existing is not None:
        return existing
    ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None
    unreliable_hashes = (
        list(state.get("unreliable_ip_hashes", []))
        if isinstance(state, dict) else []
    )
    if isinstance(state, dict) and state.get("unreliable_ip_hash"):
        unreliable_hashes.append(state["unreliable_ip_hash"])
    if ip_hash and ip_hash in unreliable_hashes:
        return {"allowed": True, "reason": "ip_geolocation_unreliable"}
    if db.in_transaction:
        raise RuntimeError("IP-geolocation lookup requires a connection outside a transaction")
    lookup_started_at = int(now)
    outcome: dict[str, Any]
    if ip is None:
        outcome = {"kind": "unknown"}
    else:
        try:
            location = await lookup_ip_location(ip)
            ip_lat = float(location.get("latitude", location.get("lat")))
            ip_long = float(location.get("longitude", location.get("lon")))
            if not (-90 <= ip_lat <= 90 and -180 <= ip_long <= 180):
                raise ValueError("IP geolocation coordinates are invalid")
            ip_country = _country(location.get("country_name", location.get("country")))
            uncertainty_km = max(0.0, float(location.get("accuracy_radius_km") or 0))
            distance = db_access.distance_metres(ip_lat, ip_long, gps_lat, gps_long)
            same_country = bool(ip_country and _country(gps_country) == ip_country)
            blatant = (
                not same_country
                and distance - uncertainty_km * 1000
                >= const.CLAIM_FIRST_LOCATION_MISMATCH_METRES
            )
            outcome = {
                "kind": "mismatch" if blatant else "match",
                "ip_hash": hashlib.sha256(ip.encode()).hexdigest(),
                "provider_lat": round(ip_lat, 3),
                "provider_long": round(ip_long, 3),
                "ip_country": ip_country,
                "gps_country": _country(gps_country),
                "distance_km": round(distance / 1000),
            }
        except Exception:
            # Configuration is validated at public startup. Runtime transport,
            # HTTP, decoding and unusable-response failures remain UNKNOWN.
            outcome = {"kind": "unknown"}

    async with db_access.transaction(db, immediate=True):
        # Re-read after provider I/O. A concurrent verification, ban, or newer
        # restriction always wins over this potentially stale observation.
        current_user = await db_access.get_user_by_id(db, user_id=int(user[schema.USER_ID]))
        if current_user is None or int(current_user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:
            return {"allowed": False, "reason": "user_not_allowed"}
        current = await _get(db, key)
        current_decision = _location_state_decision(current, now=now)
        if current_decision is not None:
            return current_decision
        current_unreliable = (
            list(current.get("unreliable_ip_hashes", []))
            if isinstance(current, dict) else []
        )
        if isinstance(current, dict) and current.get("unreliable_ip_hash"):
            current_unreliable.append(current["unreliable_ip_hash"])
        if outcome.get("ip_hash") in current_unreliable:
            return {"allowed": True, "reason": "ip_geolocation_unreliable"}
        if isinstance(current, dict) and int(current.get("decision_at") or 0) >= lookup_started_at:
            # Another equally/newer lookup completed while this one was in
            # flight. Its durable result is authoritative even if its retry
            # boundary is exactly the mocked/current second.
            return _location_state_decision(current, now=now) or {
                "allowed": False,
                "reason": str(current.get("restriction_reason") or "first_location_provider_unavailable"),
            }
        state = dict(current) if isinstance(current, dict) else {"mismatch_count": 0}
        if (outcome["kind"] in {"match", "mismatch"}
                and state.get("provider_ip_hash") == outcome.get("ip_hash")
                and state.get("provider_lat") is not None):
            provider_shift = db_access.distance_metres(
                float(state["provider_lat"]), float(state["provider_long"]),
                float(outcome["provider_lat"]), float(outcome["provider_long"]),
            )
            if provider_shift >= const.CLAIM_FIRST_LOCATION_MISMATCH_METRES:
                known_unreliable = list(state.get("unreliable_ip_hashes", []))
                if outcome["ip_hash"] not in known_unreliable:
                    known_unreliable.append(outcome["ip_hash"])
                state.update({"decision_at": now, "last_result": "provider_unreliable",
                              "provider_unreliable_at": now,
                              "unreliable_ip_hashes": known_unreliable[-2:]})
                state.pop("unreliable_ip_hash", None)
                if state.get("mismatch_ip_hash") == outcome["ip_hash"]:
                    state["mismatch_count"] = 0
                    state.pop("mismatch_ip_hash", None)
                    state.pop("last_mismatch_at", None)
                state.pop("retry_at", None)
                state.pop("restriction_reason", None)
                await _set(db, key, state)
                return {"allowed": True, "reason": "ip_geolocation_unreliable"}
        if outcome["kind"] in {"match", "mismatch"}:
            state.update({"provider_ip_hash": outcome["ip_hash"],
                          "provider_lat": outcome["provider_lat"],
                          "provider_long": outcome["provider_long"]})
        if outcome["kind"] == "match":
            state.update({"verified_at": now, "decision_at": now, "last_result": "match",
                          "ip_country": outcome["ip_country"],
                          "gps_country": outcome["gps_country"]})
            state.pop("retry_at", None)
            state.pop("restriction_reason", None)
            await _set(db, key, state)
            return {"allowed": True, "reason": "first_location_verified"}
        if outcome["kind"] == "unknown":
            state.update({"decision_at": now, "last_result": "unknown",
                          "retry_at": now + const.CLAIM_FIRST_LOCATION_UNKNOWN_RETRY_SECONDS,
                          "restriction_reason": "first_location_provider_unavailable"})
            await _set(db, key, state)
            return {"allowed": False, "reason": "first_location_provider_unavailable"}
        strikes = (
            int(state.get("mismatch_count") or 0) + 1
            if state.get("mismatch_ip_hash") == outcome["ip_hash"] else 1
        )
        cooldown = (const.CLAIM_FIRST_LOCATION_COOLDOWN_SECONDS if strikes == 1
                    else const.CLAIM_FIRST_LOCATION_SECOND_COOLDOWN_SECONDS)
        state.update({"mismatch_count": strikes, "decision_at": now,
                      "last_result": "blatant_mismatch", "last_mismatch_at": now,
                      "retry_at": now + cooldown,
                      "restriction_reason": "first_location_mismatch_restriction",
                      "mismatch_ip_hash": outcome["ip_hash"],
                      "ip_country": outcome["ip_country"],
                      "gps_country": outcome["gps_country"],
                      "distance_km": outcome["distance_km"]})
        await _set(db, key, state)
    # Repeated weak IP evidence can extend the strong restriction indefinitely,
    # but never sets USER_STATUS_BANNED without a separate strong signal.
    logger.warning("Restricted public claims for user=%s after location mismatch event=%s",
                   user[schema.USER_ID], strikes)
    return {"allowed": False, "reason": "first_location_mismatch_restriction"}


async def public_claim_decision(db, *, user_id: int, signer_address: str,
                                spot: dict[str, Any], ip: str | None,
                                lat: float, long: float) -> dict[str, Any]:
    if int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1:
        return {"allowed": True, "reason": "password_exempt"}
    if db.in_transaction:
        raise RuntimeError("fresh public-claim evaluation requires no active transaction")
    user = await db_access.get_user_by_id(db, user_id=user_id)
    if user is None or int(user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:
        return {"allowed": False, "reason": "user_not_allowed"}
    now = await db_access.get_unixepoch(db)
    if not await _behaviour_allows_public_claim(db, user_id=user_id, now=now):
        return {"allowed": False, "reason": "behavioural_temporary_restriction"}
    # Funding observation is one-hop and never bans. UNKNOWN retains a short
    # retry cache and contributes no evidence, but is not a claim denial.
    import wallet_cluster_guard
    cluster = await wallet_cluster_guard.observe(
        db, signer_address=signer_address, now=now)
    # UNKNOWN is deliberately neutral: this optional corroborating observer
    # must not become a second availability dependency on Nimiq history. The
    # pre-existing signer-history and location decisions below remain fail-safe.
    if (cluster["evidence"] and cluster.get("similar_pattern") is True
            and await _recent_weak_behaviour_anomaly(db, user_id=user_id, now=now)):
        return {"allowed": False, "reason": "corroborated_temporary_restriction"}
    trust = await signer_or_account_trusted(db, user=user, signer_address=signer_address, now=now)
    if not trust["trusted"]:
        return {"allowed": False, **trust}
    location = await first_location_decision(
        db, user=user, ip=ip, gps_lat=lat, gps_long=long,
        gps_country=spot.get(schema.SPOT_COUNTRY), now=now,
    )
    return {"allowed": bool(location["allowed"]), **location}
