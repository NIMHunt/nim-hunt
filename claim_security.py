"""Claim authentication, anti-Sybil signals, and payout safety for NimHunt.

NimHunt's ordinary UI runs in a browser/webview. Browser-supplied coordinates
and device identifiers are useful signals, but they are not authentication: a
script can submit arbitrary JSON. This module adds a second, server-verifiable
boundary around money-moving claims:

* Nimiq Pay signs a one-time server challenge. The private key never leaves the
  wallet, while NimHunt verifies the signature and derives the signer address.
* A random HttpOnly session token binds that verified signer to the device row
  used by the existing application.
* Claim HTTP requests must present the signed session in public deployments.
* Successful claims receive a durable security record before any later payout
  is allowed to leave a Spot deposit address.
* Recent claim events are correlated across verified wallet, device, payout
  address and (only as a secondary signal) source IP.
* A short payout hold gives the server time to see a coordinated sweep. A very
  suspicious burst is marked for manual review instead of auto-paying later.

The existing claim_location_guard.py remains useful and is intentionally kept.
That guard protects one USER from impossible travel; this module adds signals
that survive creation of a fresh USER row.

No schema migration is required. Security/session data lives in the existing
app_metadata table so the change is additive to the production database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import secrets
import subprocess
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import constants as const
import database as schema
import db_access
import trans_updater
import wallet
from database import get_db

RowDict = dict[str, Any]
ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/security", tags=["claim-security"])

_DEVICE_ID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CLAIM_CREATE_RE = re.compile(r"^/api/spot/(?P<spot_id>[1-9][0-9]*)/claim$")
_CLAIM_PRIVATE_RE = re.compile(r"^/api/claim/(?P<claim_id>[1-9][0-9]*)/(?:detail|location)$")

SESSION_COOKIE_NAME = "nimhunt_claim_session"
METADATA_PREFIX = "claim_security:"
CHALLENGE_PREFIX = f"{METADATA_PREFIX}challenge:"
SESSION_PREFIX = f"{METADATA_PREFIX}session:"
USER_BINDING_PREFIX = f"{METADATA_PREFIX}user:"
CLAIM_RECORD_PREFIX = f"{METADATA_PREFIX}claim:"
RATE_PREFIX = f"{METADATA_PREFIX}rate:"
RECENT_EVENTS_KEY = f"{METADATA_PREFIX}recent_events"
INCIDENT_KEY = f"{METADATA_PREFIX}latest_incident"


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < int(minimum):
        raise ValueError(f"{name} must be at least {int(minimum)}")
    return value


CHALLENGE_TTL_SECONDS = _env_int("NIMHUNT_CLAIM_AUTH_CHALLENGE_TTL_SECONDS", 5 * 60)
SESSION_TTL_SECONDS = _env_int("NIMHUNT_CLAIM_AUTH_SESSION_TTL_SECONDS", 30 * 24 * 60 * 60)
PAYOUT_HOLD_SECONDS = _env_int("NIMHUNT_CLAIM_PAYOUT_SECURITY_HOLD_SECONDS", 5 * 60)
EVENT_RETENTION_SECONDS = _env_int("NIMHUNT_CLAIM_SECURITY_EVENT_RETENTION_SECONDS", 24 * 60 * 60)
MAX_RECENT_EVENTS = _env_int("NIMHUNT_CLAIM_SECURITY_MAX_RECENT_EVENTS", 200)

STRONG_TRAVEL_MIN_METRES = _env_int("NIMHUNT_CLAIM_SECURITY_TRAVEL_MIN_METRES", 1_000)
STRONG_TRAVEL_MAX_MPS = _env_int("NIMHUNT_CLAIM_SECURITY_TRAVEL_MAX_MPS", 75)
IP_TRAVEL_MIN_METRES = _env_int("NIMHUNT_CLAIM_SECURITY_IP_TRAVEL_MIN_METRES", 20_000)
IP_TRAVEL_MAX_MPS = _env_int("NIMHUNT_CLAIM_SECURITY_IP_TRAVEL_MAX_MPS", 200)
WALLET_HOURLY_CLAIM_LIMIT = _env_int("NIMHUNT_CLAIM_SECURITY_WALLET_HOURLY_LIMIT", 20)

AUTH_RATE_WINDOW_SECONDS = _env_int("NIMHUNT_CLAIM_AUTH_RATE_WINDOW_SECONDS", 10 * 60)
AUTH_RATE_LIMIT_PER_IP = _env_int("NIMHUNT_CLAIM_AUTH_RATE_LIMIT_PER_IP", 8)
AUTH_RATE_LIMIT_PER_DEVICE = _env_int("NIMHUNT_CLAIM_AUTH_RATE_LIMIT_PER_DEVICE", 5)

BURST_WINDOW_SECONDS = _env_int("NIMHUNT_CLAIM_SECURITY_BURST_WINDOW_SECONDS", 10 * 60)
BURST_MIN_IDENTITIES = _env_int("NIMHUNT_CLAIM_SECURITY_BURST_MIN_IDENTITIES", 4)
BURST_MIN_SPREAD_METRES = _env_int("NIMHUNT_CLAIM_SECURITY_BURST_MIN_SPREAD_METRES", 50_000)
BURST_CENTRE_TOLERANCE_METRES = _env_int("NIMHUNT_CLAIM_SECURITY_BURST_CENTRE_TOLERANCE_METRES", 5)
NEW_IDENTITY_MAX_AGE_SECONDS = _env_int("NIMHUNT_CLAIM_SECURITY_NEW_IDENTITY_MAX_AGE_SECONDS", 60 * 60)

_VERIFY_HELPER = Path(__file__).resolve().parent / "helpers" / "verify_nimiq_message.mjs"
_NODE_BINARY = os.getenv("NIMHUNT_NIMIQ_NODE_BINARY", "node").strip() or "node"

_ORIGINAL_SUBMIT_CLAIM_REWARD = trans_updater.submit_claim_reward_transaction
_INSTALLED = False


class SecurityDeviceRequest(BaseModel):
    device_id_hash: str = Field(min_length=64, max_length=64)


class SecurityVerifyRequest(SecurityDeviceRequest):
    challenge_id: str = Field(min_length=10, max_length=128)
    public_key: str = Field(min_length=64, max_length=66)
    signature: str = Field(min_length=128, max_length=130)


def _clean_device_id(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if not _DEVICE_ID_RE.fullmatch(clean):
        raise ValueError("A valid Nimiq Pay device identifier is required.")
    return clean


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _session_key(token: str) -> str:
    return f"{SESSION_PREFIX}{_sha256_text(token)}"


def _user_binding_key(user_id: int) -> str:
    return f"{USER_BINDING_PREFIX}{int(user_id)}"


def _claim_record_key(claim_id: int) -> str:
    return f"{CLAIM_RECORD_PREFIX}{int(claim_id)}"


def _canonical_optional_address(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return wallet.normalise_nimiq_address(
            raw,
            field_name="claim payout address",
            allow_dev_placeholder=False,
        )
    except ValueError:
        return None


async def _metadata_get(db, key: str) -> Any | None:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} AS value "
        f"FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;",
        (str(key),),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["value"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Discarding malformed claim-security metadata key=%s", key)
        await _metadata_delete(db, key)
        return None


async def _metadata_set(db, key: str, value: Any) -> None:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    await db.execute(
        f"""
        INSERT INTO {schema.APP_METADATA_TABLE_NAME} (
            {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}
        ) VALUES (?, ?)
        ON CONFLICT ({schema.APP_METADATA_KEY}) DO UPDATE SET
            {schema.APP_METADATA_VALUE} = excluded.{schema.APP_METADATA_VALUE};
        """,
        (str(key), payload),
    )


async def _metadata_delete(db, key: str) -> None:
    await db.execute(
        f"DELETE FROM {schema.APP_METADATA_TABLE_NAME} WHERE {schema.APP_METADATA_KEY} = ?;",
        (str(key),),
    )


def _request_ip(request: Request) -> str:
    # Railway/reverse proxies normally append the immediate client to the right
    # side of X-Forwarded-For. We use it only as a secondary anti-abuse signal;
    # wallet/device/payout identity remains authoritative.
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        candidate = forwarded.split(",")[-1].strip()
        if candidate:
            return candidate
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def _scope_ip(scope: dict[str, Any]) -> str:
    headers = {
        bytes(key).decode("latin-1").lower(): bytes(value).decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    forwarded = str(headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        candidate = forwarded.split(",")[-1].strip()
        if candidate:
            return candidate
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return str(client[0])
    return "unknown"


def _ip_hash(value: str) -> str:
    return _sha256_text(f"nimhunt-claim-ip-v1:{str(value).strip().lower()}")


def _cookie_token_from_scope(scope: dict[str, Any]) -> str | None:
    cookie_header = ""
    for key, value in scope.get("headers", []):
        if bytes(key).lower() == b"cookie":
            cookie_header = bytes(value).decode("latin-1")
            break
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(SESSION_COOKIE_NAME)
    return str(morsel.value) if morsel is not None else None


async def _load_session(db, *, token: str | None, device_id_hash: str, now: int) -> RowDict | None:
    if not token:
        return None
    value = await _metadata_get(db, _session_key(token))
    if not isinstance(value, dict):
        return None
    if int(value.get("expires_at") or 0) <= int(now):
        await _metadata_delete(db, _session_key(token))
        return None
    if str(value.get("device_id_hash") or "") != str(device_id_hash):
        return None
    wallet_address = _canonical_optional_address(value.get("wallet_address"))
    if wallet_address is None:
        return None
    try:
        user_id = int(value["user_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        **value,
        "user_id": user_id,
        "wallet_address": wallet_address,
    }


async def _rate_limit_bucket(
    db,
    *,
    key: str,
    now: int,
    window_seconds: int,
    limit: int,
) -> tuple[bool, int]:
    raw = await _metadata_get(db, key)
    timestamps = []
    if isinstance(raw, list):
        for value in raw:
            try:
                stamp = int(value)
            except (TypeError, ValueError):
                continue
            if stamp > int(now) - int(window_seconds):
                timestamps.append(stamp)

    if len(timestamps) >= int(limit):
        retry_at = min(timestamps) + int(window_seconds) + 1
        await _metadata_set(db, key, timestamps[-int(limit) :])
        return False, retry_at

    timestamps.append(int(now))
    await _metadata_set(db, key, timestamps[-int(limit) :])
    return True, int(now)


def _verify_signature_sync(*, message: str, public_key: str, signature: str) -> str:
    if not _VERIFY_HELPER.exists():
        raise RuntimeError("Nimiq authentication verifier is missing")
    payload = {
        "message": str(message),
        "public_key": str(public_key),
        "signature": str(signature),
    }
    try:
        completed = subprocess.run(
            [_NODE_BINARY, str(_VERIFY_HELPER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Nimiq authentication verifier is unavailable") from exc

    try:
        result = json.loads((completed.stdout or "{}").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Nimiq authentication verifier returned invalid JSON") from exc

    if completed.returncode != 0 or not isinstance(result, dict) or result.get("ok") is False:
        message_text = "Invalid Nimiq authentication signature"
        if isinstance(result, dict) and result.get("message"):
            message_text = str(result["message"])
        raise ValueError(message_text)

    return wallet.normalise_nimiq_address(
        str(result.get("address") or ""),
        field_name="authenticated wallet address",
        allow_dev_placeholder=False,
    )


async def _verify_signature(*, message: str, public_key: str, signature: str) -> str:
    return await asyncio.to_thread(
        _verify_signature_sync,
        message=message,
        public_key=public_key,
        signature=signature,
    )


@router.post("/session")
async def security_session_status(payload: SecurityDeviceRequest, request: Request) -> JSONResponse:
    try:
        device_id = _clean_device_id(payload.device_id_hash)
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "authenticated": False, "code": "invalid_device", "message": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = request.cookies.get(SESSION_COOKIE_NAME)
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        session = await _load_session(db, token=token, device_id_hash=device_id, now=now)
        if session is None:
            return JSONResponse({"ok": True, "authenticated": False})
        user = await db_access.get_user_by_id(db, user_id=int(session["user_id"]))
        if user is None or str(user[schema.USER_DEVICE_ID_HASH]).lower() != device_id:
            return JSONResponse({"ok": True, "authenticated": False})

    return JSONResponse(
        {
            "ok": True,
            "authenticated": True,
            "wallet_address": session["wallet_address"],
            "expires_at": int(session["expires_at"]),
        }
    )


@router.post("/challenge")
async def security_challenge(payload: SecurityDeviceRequest, request: Request) -> JSONResponse:
    try:
        device_id = _clean_device_id(payload.device_id_hash)
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "code": "invalid_device", "message": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    ip_fingerprint = _ip_hash(_request_ip(request))
    async with get_db() as db:
        async with db_access.transaction(db, immediate=True):
            now = await db_access.get_unixepoch(db)
            allowed_ip, retry_ip = await _rate_limit_bucket(
                db,
                key=f"{RATE_PREFIX}challenge:ip:{ip_fingerprint}",
                now=now,
                window_seconds=AUTH_RATE_WINDOW_SECONDS,
                limit=AUTH_RATE_LIMIT_PER_IP,
            )
            allowed_device, retry_device = await _rate_limit_bucket(
                db,
                key=f"{RATE_PREFIX}challenge:device:{device_id}",
                now=now,
                window_seconds=AUTH_RATE_WINDOW_SECONDS,
                limit=AUTH_RATE_LIMIT_PER_DEVICE,
            )
            if not allowed_ip or not allowed_device:
                retry_at = max(retry_ip, retry_device)
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "auth_rate_limited",
                        "message": "Too many wallet-verification attempts. Please try again later.",
                        "retry_at": retry_at,
                    },
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            challenge_id = secrets.token_urlsafe(24)
            nonce = secrets.token_hex(32)
            message = (
                "NimHunt claim authentication\n"
                f"Device: {device_id}\n"
                f"Nonce: {nonce}\n"
                f"Issued: {int(now)}"
            )
            expires_at = int(now) + CHALLENGE_TTL_SECONDS
            await _metadata_set(
                db,
                f"{CHALLENGE_PREFIX}{challenge_id}",
                {
                    "device_id_hash": device_id,
                    "message": message,
                    "created_at": int(now),
                    "expires_at": expires_at,
                    "ip_hash": ip_fingerprint,
                },
            )

    return JSONResponse(
        {
            "ok": True,
            "challenge_id": challenge_id,
            "message": message,
            "expires_at": expires_at,
        }
    )


@router.post("/verify")
async def security_verify(payload: SecurityVerifyRequest, request: Request) -> JSONResponse:
    try:
        device_id = _clean_device_id(payload.device_id_hash)
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "code": "invalid_device", "message": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    challenge_key = f"{CHALLENGE_PREFIX}{payload.challenge_id}"
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        challenge = await _metadata_get(db, challenge_key)
    if not isinstance(challenge, dict):
        return JSONResponse(
            {"ok": False, "code": "challenge_missing", "message": "This authentication challenge is no longer valid."},
            status_code=status.HTTP_409_CONFLICT,
        )
    if str(challenge.get("device_id_hash") or "") != device_id:
        return JSONResponse(
            {"ok": False, "code": "challenge_mismatch", "message": "The authentication challenge belongs to another device."},
            status_code=status.HTTP_409_CONFLICT,
        )
    if int(challenge.get("expires_at") or 0) <= int(now):
        async with get_db() as db:
            async with db_access.transaction(db):
                await _metadata_delete(db, challenge_key)
        return JSONResponse(
            {"ok": False, "code": "challenge_expired", "message": "The authentication challenge expired. Please try again."},
            status_code=status.HTTP_409_CONFLICT,
        )

    try:
        wallet_address = await _verify_signature(
            message=str(challenge["message"]),
            public_key=payload.public_key,
            signature=payload.signature,
        )
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "code": "signature_invalid", "message": str(exc)},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    except RuntimeError as exc:
        logger.exception("Nimiq claim authentication verifier failed")
        return JSONResponse(
            {"ok": False, "code": "auth_unavailable", "message": str(exc)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    token = secrets.token_urlsafe(48)
    ip_fingerprint = _ip_hash(_request_ip(request))
    async with get_db() as db:
        async with db_access.transaction(db, immediate=True):
            now = await db_access.get_unixepoch(db)
            fresh_challenge = await _metadata_get(db, challenge_key)
            if not isinstance(fresh_challenge, dict) or int(fresh_challenge.get("expires_at") or 0) <= int(now):
                return JSONResponse(
                    {"ok": False, "code": "challenge_used", "message": "This authentication challenge has already been used."},
                    status_code=status.HTTP_409_CONFLICT,
                )
            if str(fresh_challenge.get("device_id_hash") or "") != device_id:
                return JSONResponse(
                    {"ok": False, "code": "challenge_mismatch", "message": "The authentication challenge belongs to another device."},
                    status_code=status.HTTP_409_CONFLICT,
                )

            user_id, _created = await db_access.get_or_create_user(db, device_id_hash=device_id)
            binding_key = _user_binding_key(user_id)
            existing_binding = await _metadata_get(db, binding_key)
            if isinstance(existing_binding, dict):
                previous_wallet = _canonical_optional_address(existing_binding.get("wallet_address"))
                if previous_wallet is not None and previous_wallet != wallet_address:
                    return JSONResponse(
                        {
                            "ok": False,
                            "code": "device_wallet_mismatch",
                            "message": (
                                "This device is already bound to a different Nimiq account for claims. "
                                "This safeguard prevents account switching from bypassing claim limits."
                            ),
                        },
                        status_code=status.HTTP_409_CONFLICT,
                    )

            expires_at = int(now) + SESSION_TTL_SECONDS
            session = {
                "user_id": int(user_id),
                "device_id_hash": device_id,
                "wallet_address": wallet_address,
                "public_key_hash": _sha256_text(str(payload.public_key).lower()),
                "created_at": int(now),
                "expires_at": expires_at,
                "ip_hash": ip_fingerprint,
            }
            await _metadata_set(db, _session_key(token), session)
            await _metadata_set(
                db,
                binding_key,
                {
                    "user_id": int(user_id),
                    "device_id_hash": device_id,
                    "wallet_address": wallet_address,
                    "verified_at": int(now),
                },
            )
            await _metadata_delete(db, challenge_key)

    response = JSONResponse(
        {
            "ok": True,
            "authenticated": True,
            "wallet_address": wallet_address,
            "expires_at": expires_at,
        }
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=bool(getattr(const, "PUBLIC_DEPLOYMENT", False)),
        samesite="lax",
        path="/",
    )
    return response


async def _load_recent_events(db, *, now: int) -> list[RowDict]:
    raw = await _metadata_get(db, RECENT_EVENTS_KEY)
    if not isinstance(raw, list):
        return []
    cutoff = int(now) - EVENT_RETENTION_SECONDS
    out: list[RowDict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            at = int(item.get("claimed_at") or 0)
        except (TypeError, ValueError):
            continue
        if at >= cutoff:
            out.append(dict(item))
    return out[-MAX_RECENT_EVENTS:]


def _event_distance(previous: RowDict, target: RowDict) -> float:
    centre = db_access.distance_metres(
        float(previous["spot_lat"]),
        float(previous["spot_long"]),
        float(target["spot_lat"]),
        float(target["spot_long"]),
    )
    return max(
        0.0,
        float(centre)
        - max(0.0, float(previous.get("spot_radius") or 0))
        - max(0.0, float(target.get("spot_radius") or 0)),
    )


def _travel_violation(
    previous: RowDict,
    target: RowDict,
    *,
    max_mps: float,
    min_metres: float,
) -> RowDict | None:
    try:
        previous_at = int(previous["claimed_at"])
        target_at = int(target["claimed_at"])
        distance = _event_distance(previous, target)
    except (KeyError, TypeError, ValueError):
        return None
    elapsed = max(1, target_at - previous_at)
    speed = distance / elapsed
    if distance < float(min_metres) or speed <= float(max_mps):
        return None
    retry_at = previous_at + math.ceil(distance / float(max_mps)) + 1
    return {
        "distance_metres": distance,
        "elapsed_seconds": elapsed,
        "speed_metres_per_second": speed,
        "retry_at": retry_at,
    }


def _shared_strong_signal(previous: RowDict, target: RowDict) -> str | None:
    pairs = (
        ("verified_wallet", "verified wallet"),
        ("device_id_hash", "device"),
        ("payout_address", "payout address"),
    )
    for field, label in pairs:
        left = str(previous.get(field) or "").strip().upper()
        right = str(target.get(field) or "").strip().upper()
        if left and right and left == right:
            return label
    return None


def _preclaim_risk(events: list[RowDict], target: RowDict) -> RowDict:
    strongest: RowDict | None = None
    for previous in events:
        if int(previous.get("claim_id") or 0) == int(target.get("claim_id") or -1):
            continue
        signal = _shared_strong_signal(previous, target)
        if signal:
            violation = _travel_violation(
                previous,
                target,
                max_mps=STRONG_TRAVEL_MAX_MPS,
                min_metres=STRONG_TRAVEL_MIN_METRES,
            )
            if violation and (strongest is None or int(violation["retry_at"]) > int(strongest["retry_at"])):
                strongest = {
                    **violation,
                    "blocked": True,
                    "reason": "impossible_travel",
                    "signal": signal,
                    "previous_claim_id": int(previous.get("claim_id") or 0),
                }

    if strongest is not None:
        return strongest

    wallet_address = str(target.get("verified_wallet") or "").strip().upper()
    if wallet_address:
        wallet_events = [
            event
            for event in events
            if str(event.get("verified_wallet") or "").strip().upper() == wallet_address
            and int(event.get("claimed_at") or 0) > int(target["claimed_at"]) - 60 * 60
        ]
        if len(wallet_events) >= WALLET_HOURLY_CLAIM_LIMIT:
            retry_at = min(int(event["claimed_at"]) for event in wallet_events) + 60 * 60 + 1
            return {
                "blocked": True,
                "reason": "wallet_rate_limit",
                "signal": "verified wallet",
                "retry_at": retry_at,
            }

    ip_value = str(target.get("ip_hash") or "")
    if ip_value:
        same_ip = [event for event in events if str(event.get("ip_hash") or "") == ip_value]
        for previous in same_ip:
            violation = _travel_violation(
                previous,
                target,
                max_mps=IP_TRAVEL_MAX_MPS,
                min_metres=IP_TRAVEL_MIN_METRES,
            )
            if violation:
                return {
                    **violation,
                    "blocked": True,
                    "reason": "source_network_impossible_travel",
                    "signal": "source network",
                    "previous_claim_id": int(previous.get("claim_id") or 0),
                }

    return {"blocked": False, "reason": "allow"}


def _max_spread_metres(events: list[RowDict]) -> float:
    maximum = 0.0
    for index, first in enumerate(events):
        for second in events[index + 1 :]:
            try:
                distance = db_access.distance_metres(
                    float(first["spot_lat"]),
                    float(first["spot_long"]),
                    float(second["spot_lat"]),
                    float(second["spot_long"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            maximum = max(maximum, float(distance))
    return maximum


def _coordinated_burst_claim_ids(events: list[RowDict], *, now: int) -> list[int]:
    cutoff = int(now) - BURST_WINDOW_SECONDS
    candidates: list[RowDict] = []
    for event in events:
        try:
            if int(event.get("claimed_at") or 0) < cutoff:
                continue
            user_age = int(event.get("claimed_at") or 0) - int(event.get("user_created_at") or 0)
            session_age = int(event.get("claimed_at") or 0) - int(event.get("session_created_at") or 0)
            centre_offset = float(event.get("centre_offset_metres"))
        except (TypeError, ValueError):
            continue
        if user_age < 0 or session_age < 0:
            continue
        if user_age > NEW_IDENTITY_MAX_AGE_SECONDS or session_age > NEW_IDENTITY_MAX_AGE_SECONDS:
            continue
        if centre_offset > BURST_CENTRE_TOLERANCE_METRES:
            continue
        candidates.append(event)

    if len(candidates) < BURST_MIN_IDENTITIES:
        return []
    devices = {str(event.get("device_id_hash") or "") for event in candidates if event.get("device_id_hash")}
    wallets = {str(event.get("verified_wallet") or "") for event in candidates if event.get("verified_wallet")}
    spots = {int(event.get("spot_id") or 0) for event in candidates if int(event.get("spot_id") or 0) > 0}
    if min(len(devices), len(wallets), len(spots)) < BURST_MIN_IDENTITIES:
        return []
    if _max_spread_metres(candidates) < BURST_MIN_SPREAD_METRES:
        return []
    return sorted({int(event["claim_id"]) for event in candidates if int(event.get("claim_id") or 0) > 0})


async def _mark_manual_review(db, *, claim_ids: list[int], reason: str, now: int) -> None:
    if not claim_ids:
        return
    for claim_id in sorted({int(value) for value in claim_ids if int(value) > 0}):
        key = _claim_record_key(claim_id)
        record = await _metadata_get(db, key)
        if not isinstance(record, dict):
            continue
        record["manual_review"] = True
        record["manual_review_reason"] = str(reason)
        record["manual_review_marked_at"] = int(now)
        await _metadata_set(db, key, record)
    await _metadata_set(
        db,
        INCIDENT_KEY,
        {
            "reason": str(reason),
            "claim_ids": sorted({int(value) for value in claim_ids if int(value) > 0}),
            "detected_at": int(now),
        },
    )
    logger.warning("Claim-security manual review hold: reason=%s claims=%s", reason, claim_ids)


async def _record_claim_event(
    *,
    claim_id: int,
    session: RowDict,
    request_body: RowDict,
    ip_fingerprint: str,
) -> None:
    async with get_db() as db:
        async with db_access.transaction(db, immediate=True):
            now = await db_access.get_unixepoch(db)
            claim = await db_access.get_claim(db, claim_id=int(claim_id))
            if claim is None:
                raise RuntimeError(f"claim id={claim_id} disappeared before security recording")
            spot = await db_access.get_spot(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
            if spot is None:
                raise RuntimeError(f"spot for claim id={claim_id} disappeared before security recording")
            user = await db_access.get_user_by_id(db, user_id=int(claim[schema.CLAIM_RECIPIENT]))
            if user is None:
                raise RuntimeError(f"user for claim id={claim_id} disappeared before security recording")

            reported_lat = float(request_body.get("lat", claim[schema.CLAIM_LAT]))
            reported_long = float(request_body.get("long", claim[schema.CLAIM_LONG]))
            spot_lat = float(spot[schema.SPOT_LAT])
            spot_long = float(spot[schema.SPOT_LONG])
            event: RowDict = {
                "claim_id": int(claim_id),
                "spot_id": int(claim[schema.CLAIM_SPOT_ID]),
                "user_id": int(claim[schema.CLAIM_RECIPIENT]),
                "device_id_hash": str(session["device_id_hash"]),
                "verified_wallet": str(session["wallet_address"]),
                "payout_address": _canonical_optional_address(claim.get(schema.CLAIM_PAYOUT_ADDRESS)),
                "ip_hash": str(ip_fingerprint),
                "claimed_at": int(claim.get(schema.CLAIM_CLAIMED_AT) or now),
                "recorded_at": int(now),
                "session_created_at": int(session.get("created_at") or now),
                "user_created_at": int(user.get(schema.USER_CREATED_AT) or now),
                "spot_lat": spot_lat,
                "spot_long": spot_long,
                "spot_radius": int(spot.get(schema.SPOT_RADIUS) or 0),
                "reported_lat": reported_lat,
                "reported_long": reported_long,
                "centre_offset_metres": db_access.distance_metres(
                    reported_lat,
                    reported_long,
                    spot_lat,
                    spot_long,
                ),
                "manual_review": False,
            }
            await _metadata_set(db, _claim_record_key(claim_id), event)

            events = await _load_recent_events(db, now=now)
            events = [item for item in events if int(item.get("claim_id") or 0) != int(claim_id)]
            events.append(event)
            events = events[-MAX_RECENT_EVENTS:]
            await _metadata_set(db, RECENT_EVENTS_KEY, events)

            suspicious_ids = _coordinated_burst_claim_ids(events, now=now)
            if suspicious_ids:
                await _mark_manual_review(
                    db,
                    claim_ids=suspicious_ids,
                    reason="coordinated_new_identity_exact_location_burst",
                    now=now,
                )


async def get_claim_security_record(db, *, claim_id: int) -> RowDict | None:
    """Return one durable security record for tests/operator diagnostics."""
    value = await _metadata_get(db, _claim_record_key(int(claim_id)))
    return dict(value) if isinstance(value, dict) else None


async def release_claim_manual_review(db, *, claim_id: int) -> bool:
    """Operator helper: clear a manual-review hold after inspecting a claim."""
    key = _claim_record_key(int(claim_id))
    record = await _metadata_get(db, key)
    if not isinstance(record, dict):
        return False
    record["manual_review"] = False
    record.pop("manual_review_reason", None)
    record.pop("manual_review_marked_at", None)
    record["manual_review_released_at"] = await db_access.get_unixepoch(db)
    await _metadata_set(db, key, record)
    return True


async def _payout_security_decision(db, *, claim_id: int) -> RowDict:
    now = await db_access.get_unixepoch(db)
    claim = await db_access.get_claim(db, claim_id=int(claim_id))
    if claim is None:
        return {"allow": False, "reason": "claim_missing"}

    record = await get_claim_security_record(db, claim_id=int(claim_id))
    if record is None:
        if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
            return {"allow": False, "reason": "security_record_missing", "manual_review": True}
        return {"allow": True, "reason": "development_without_security_record"}

    if bool(record.get("manual_review")):
        return {
            "allow": False,
            "reason": str(record.get("manual_review_reason") or "manual_review"),
            "manual_review": True,
        }

    claimed_at = int(claim.get(schema.CLAIM_CLAIMED_AT) or now)
    release_at = claimed_at + PAYOUT_HOLD_SECONDS
    if now < release_at:
        return {
            "allow": False,
            "reason": "security_hold",
            "retry_at": release_at,
            "retry_after_seconds": max(1, release_at - now),
        }

    return {"allow": True, "reason": "security_checks_passed"}


async def submit_claim_reward_transaction_with_security(
    db,
    *,
    claim_id: int,
    amount: int,
    to_address: str | None = None,
) -> RowDict:
    """Final claim-payout gate installed in front of trans_updater."""
    decision = await _payout_security_decision(db, claim_id=int(claim_id))
    if not bool(decision.get("allow")):
        logger.warning(
            "Claim payout held by security: claim=%s reason=%s retry_at=%s",
            int(claim_id),
            decision.get("reason"),
            decision.get("retry_at"),
        )
        return {
            "ok": True,
            "claim_id": int(claim_id),
            "paid": False,
            "skipped": True,
            "deferred": True,
            "security_hold": True,
            **decision,
        }
    return await _ORIGINAL_SUBMIT_CLAIM_REWARD(
        db,
        claim_id=int(claim_id),
        amount=int(amount),
        to_address=to_address,
    )


def _security_error_response(*, code: str, message: str, http_status: int, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "code": str(code), "message": str(message), **extra},
        status_code=int(http_status),
    )


async def _read_request_body(receive: Callable[..., Awaitable[dict[str, Any]]]) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            break
        if message.get("type") != "http.request":
            continue
        chunks.append(bytes(message.get("body") or b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay_receive(body: bytes) -> Callable[..., Awaitable[dict[str, Any]]]:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def _response_json(messages: list[dict[str, Any]]) -> tuple[int, RowDict | None]:
    status_code = 500
    body_parts: list[bytes] = []
    for message in messages:
        if message.get("type") == "http.response.start":
            status_code = int(message.get("status") or 500)
        elif message.get("type") == "http.response.body":
            body_parts.append(bytes(message.get("body") or b""))
    try:
        value = json.loads(b"".join(body_parts).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status_code, None
    return status_code, value if isinstance(value, dict) else None


async def _preclaim_decision(
    *,
    spot_id: int,
    session: RowDict,
    request_body: RowDict,
    ip_fingerprint: str,
) -> RowDict:
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        spot = await db_access.get_spot(db, spot_id=int(spot_id))
        if spot is None or spot.get(schema.SPOT_LAT) is None or spot.get(schema.SPOT_LONG) is None:
            return {"blocked": False, "reason": "spot_unavailable_for_security_check"}
        events = await _load_recent_events(db, now=now)

    target: RowDict = {
        "claim_id": 0,
        "spot_id": int(spot_id),
        "device_id_hash": str(session["device_id_hash"]),
        "verified_wallet": str(session["wallet_address"]),
        "payout_address": _canonical_optional_address(request_body.get("payout_address")),
        "ip_hash": str(ip_fingerprint),
        "claimed_at": int(now),
        "spot_lat": float(spot[schema.SPOT_LAT]),
        "spot_long": float(spot[schema.SPOT_LONG]),
        "spot_radius": int(spot.get(schema.SPOT_RADIUS) or 0),
    }
    return _preclaim_risk(events, target)


async def guard_http_request(
    app: ASGIApp,
    scope: dict[str, Any],
    receive: Callable[..., Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> bool:
    """Handle protected claim API requests, returning True when consumed.

    This is called by NimHunt's existing outer HTTP middleware. Development is
    deliberately left compatible with spoof.py; public-testnet and production
    fail closed for money-moving claim paths.
    """
    if not _INSTALLED or not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        return False
    if scope.get("type") != "http" or str(scope.get("method") or "").upper() != "POST":
        return False

    path = str(scope.get("path") or "")
    claim_match = _CLAIM_CREATE_RE.fullmatch(path)
    private_match = _CLAIM_PRIVATE_RE.fullmatch(path)
    if claim_match is None and private_match is None:
        return False

    body = await _read_request_body(receive)
    try:
        request_body = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        request_body = {}
    if not isinstance(request_body, dict):
        request_body = {}

    try:
        device_id = _clean_device_id(request_body.get("device_id_hash"))
    except ValueError as exc:
        response = _security_error_response(
            code="wallet_auth_required",
            message=str(exc),
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
        await response(scope, _replay_receive(b""), send)
        return True

    token = _cookie_token_from_scope(scope)
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        session = await _load_session(db, token=token, device_id_hash=device_id, now=now)
        if session is not None:
            user = await db_access.get_user_by_id(db, user_id=int(session["user_id"]))
            if user is None or str(user[schema.USER_DEVICE_ID_HASH]).lower() != device_id:
                session = None

    if session is None:
        response = _security_error_response(
            code="wallet_auth_required",
            message="Approve the Nimiq wallet verification before claiming.",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
        await response(scope, _replay_receive(b""), send)
        return True

    ip_fingerprint = _ip_hash(_scope_ip(scope))
    if claim_match is not None:
        spot_id = int(claim_match.group("spot_id"))
        decision = await _preclaim_decision(
            spot_id=spot_id,
            session=session,
            request_body=request_body,
            ip_fingerprint=ip_fingerprint,
        )
        if bool(decision.get("blocked")):
            retry_at = int(decision.get("retry_at") or 0)
            response = _security_error_response(
                code="claim_security_cooldown",
                message=(
                    "This claim has been postponed because recent claim activity associated "
                    "with this identity could not have reached this Spot safely yet."
                ),
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                retry_at=retry_at or None,
                reason=decision.get("reason"),
            )
            await response(scope, _replay_receive(b""), send)
            return True

    captured: list[dict[str, Any]] = []

    async def capture_send(message: dict[str, Any]) -> None:
        captured.append(message)

    await app(scope, _replay_receive(body), capture_send)

    if claim_match is not None:
        response_status, response_data = _response_json(captured)
        if 200 <= response_status < 300 and isinstance(response_data, dict) and response_data.get("ok") is True:
            claim_data = response_data.get("claim")
            claim_id = claim_data.get("id") if isinstance(claim_data, dict) else None
            if claim_id is not None:
                try:
                    await _record_claim_event(
                        claim_id=int(claim_id),
                        session=session,
                        request_body=request_body,
                        ip_fingerprint=ip_fingerprint,
                    )
                except Exception:
                    # The payout boundary fails closed when this marker is absent,
                    # so never create a duplicate claim merely because audit
                    # recording failed after the claim itself committed.
                    logger.exception("Failed to persist security record for claim=%s", claim_id)

    for message in captured:
        await send(message)
    return True


def install() -> None:
    """Install the public HTTP and final payout claim-security boundaries."""
    global _INSTALLED
    if _INSTALLED:
        return

    trans_updater.submit_claim_reward_transaction = submit_claim_reward_transaction_with_security

    # public_html is imported before funding_flow.install() in main.py. Import it
    # lazily here to avoid a module cycle during application startup.
    import public_html

    public_html.router.include_router(router)
    _INSTALLED = True


__all__ = [
    "BURST_CENTRE_TOLERANCE_METRES",
    "BURST_MIN_IDENTITIES",
    "BURST_MIN_SPREAD_METRES",
    "BURST_WINDOW_SECONDS",
    "CHALLENGE_TTL_SECONDS",
    "PAYOUT_HOLD_SECONDS",
    "SESSION_TTL_SECONDS",
    "SESSION_COOKIE_NAME",
    "get_claim_security_record",
    "guard_http_request",
    "install",
    "release_claim_manual_review",
    "submit_claim_reward_transaction_with_security",
]
