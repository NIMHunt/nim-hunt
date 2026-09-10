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

import constants as const
import database as schema
import db_access
import trans_updater

logger = logging.getLogger(__name__)
ROLLOUT_KEY = "fresh_claim_guard:activated_at"
SIGNER_PREFIX = "fresh_claim_guard:signer:"
LOCATION_PREFIX = "fresh_claim_guard:location:"
GENERIC_MESSAGE = "Public Spots are temporarily unavailable for this account. Please try again later."


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


async def signer_or_account_trusted(db, *, user: dict[str, Any], signer_address: str, now: int) -> dict[str, Any]:
    age = max(0, int(now) - int(user[schema.USER_CREATED_AT]))
    if age >= const.CLAIM_IDENTITY_TRUST_AGE_SECONDS:
        return {"trusted": True, "reason": "account_established"}
    address = trans_updater._validate_nimiq_address(
        signer_address, field_name="claim signer address"
    )
    cache_key = _key(SIGNER_PREFIX, address)
    cached = await _get(db, cache_key)
    if isinstance(cached, dict) and cached.get("address") == address:
        if cached.get("trusted") is True:
            return {"trusted": True, "reason": "signer_established"}
        if int(cached.get("refresh_at") or 0) > int(now):
            return {"trusted": False, "reason": "signer_not_yet_trusted"}
    try:
        oldest = await _oldest_confirmed_activity(address)
    except Exception:
        logger.warning("Signer history unavailable for user=%s", user[schema.USER_ID])
        return {"trusted": False, "reason": "signer_history_unavailable"}
    trusted = oldest is not None and oldest <= now - const.CLAIM_IDENTITY_TRUST_AGE_SECONDS
    refresh_at = now + const.CLAIM_SIGNER_HISTORY_NEGATIVE_CACHE_SECONDS
    if oldest is not None and not trusted:
        refresh_at = min(refresh_at, oldest + const.CLAIM_IDENTITY_TRUST_AGE_SECONDS + 1)
    await _set(db, cache_key, {
        "address": address, "trusted": trusted, "oldest_confirmed_at": oldest,
        "checked_at": now, "refresh_at": None if trusted else refresh_at,
    })
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


async def first_location_decision(db, *, user: dict[str, Any], ip: str | None,
                                  gps_lat: float, gps_long: float,
                                  gps_country: str | None, now: int) -> dict[str, Any]:
    rollout = await ensure_rollout_marker(db)
    if (int(user[schema.USER_ID]) <= rollout["legacy_max_user_id"] or
            int(user[schema.USER_CREATED_AT]) < rollout["activated_at"]):
        return {"allowed": True, "reason": "legacy_account"}
    key = _key(LOCATION_PREFIX, int(user[schema.USER_ID]))
    state = await _get(db, key)
    state = dict(state) if isinstance(state, dict) else {"mismatch_count": 0}
    if state.get("verified_at"):
        return {"allowed": True, "reason": "first_location_verified"}
    if int(state.get("retry_at") or 0) > now:
        return {"allowed": False, "reason": str(state.get("restriction_reason") or "first_location_mismatch_restriction")}
    if ip is None:
        state.update({"last_result": "unknown", "retry_at": now + const.CLAIM_FIRST_LOCATION_UNKNOWN_RETRY_SECONDS,
                      "restriction_reason": "first_location_provider_unavailable"})
        await _set(db, key, state)
        return {"allowed": False, "reason": "first_location_provider_unavailable"}
    try:
        location = await lookup_ip_location(ip)
        ip_lat = float(location.get("latitude", location.get("lat")))
        ip_long = float(location.get("longitude", location.get("lon")))
        ip_country = _country(location.get("country_name", location.get("country")))
        uncertainty_km = max(0.0, float(location.get("accuracy_radius_km") or 0))
        distance = db_access.distance_metres(ip_lat, ip_long, gps_lat, gps_long)
    except (OSError, ValueError, TypeError, urllib.error.URLError, json.JSONDecodeError):
        state.update({"last_result": "unknown", "retry_at": now + const.CLAIM_FIRST_LOCATION_UNKNOWN_RETRY_SECONDS,
                      "restriction_reason": "first_location_provider_unavailable"})
        await _set(db, key, state)
        return {"allowed": False, "reason": "first_location_provider_unavailable"}
    same_country = bool(ip_country and _country(gps_country) == ip_country)
    blatant = (not same_country and
               distance - uncertainty_km * 1000 >= const.CLAIM_FIRST_LOCATION_MISMATCH_METRES)
    if not blatant:
        state.update({"verified_at": now, "last_result": "match",
                      "ip_country": ip_country, "gps_country": _country(gps_country)})
        state.pop("retry_at", None)
        await _set(db, key, state)
        return {"allowed": True, "reason": "first_location_verified"}
    strikes = int(state.get("mismatch_count") or 0) + 1
    # A strike is independent only because this branch cannot run before the
    # previous retry_at. Refreshes and retries inside one cooldown return above.
    cooldown = (const.CLAIM_FIRST_LOCATION_COOLDOWN_SECONDS if strikes == 1
                else const.CLAIM_FIRST_LOCATION_SECOND_COOLDOWN_SECONDS)
    state.update({"mismatch_count": strikes, "last_result": "blatant_mismatch",
                  "last_mismatch_at": now, "retry_at": now + cooldown,
                  "restriction_reason": "first_location_mismatch_restriction",
                  "ip_country": ip_country, "gps_country": _country(gps_country),
                  "distance_km": round(distance / 1000)})
    await _set(db, key, state)
    if strikes >= const.CLAIM_FIRST_LOCATION_BAN_STRIKES:
        await db_access.set_user_status_to_banned(db, user_id=int(user[schema.USER_ID]))
        logger.warning("Banned user=%s after %s independent first-location mismatches",
                       user[schema.USER_ID], strikes)
        return {"allowed": False, "reason": "repeated_location_mismatch", "banned": True}
    logger.warning("Restricted public claims for user=%s after location mismatch event=%s",
                   user[schema.USER_ID], strikes)
    return {"allowed": False, "reason": "first_location_mismatch_restriction"}


async def public_claim_decision(db, *, user_id: int, signer_address: str,
                                spot: dict[str, Any], ip: str | None,
                                lat: float, long: float) -> dict[str, Any]:
    if int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1:
        return {"allowed": True, "reason": "password_exempt"}
    user = await db_access.get_user_by_id(db, user_id=user_id)
    if user is None or int(user[schema.USER_STATUS]) == const.USER_STATUS_BANNED:
        return {"allowed": False, "reason": "user_not_allowed"}
    now = await db_access.get_unixepoch(db)
    trust = await signer_or_account_trusted(db, user=user, signer_address=signer_address, now=now)
    if not trust["trusted"]:
        return {"allowed": False, **trust}
    location = await first_location_decision(
        db, user=user, ip=ip, gps_lat=lat, gps_long=long,
        gps_country=spot.get(schema.SPOT_COUNTRY), now=now,
    )
    return {"allowed": bool(location["allowed"]), **location}
