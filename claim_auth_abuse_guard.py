"""Bound repeated claim-auth signature verification attempts.

Challenge creation is already rate-limited, but without this layer one valid
challenge could be replayed repeatedly against `/api/security/verify` until it
expires. Every attempt launches the local Node verifier, so that is a cheap
request-amplification / CPU-abuse path even though forged signatures still fail.

This guard consumes only the public verify endpoint, applies rolling per-source
and per-device limits, then replays the original request to FastAPI. Other claim
security traffic continues through the existing guard unchanged.
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

from fastapi import status

import claim_network_security
import claim_security
import constants as const
import db_access
from database import get_db

ASGIApp = Callable[..., Awaitable[None]]

VERIFY_RATE_LIMIT_PER_IP = int(
    os.getenv("NIMHUNT_CLAIM_AUTH_VERIFY_RATE_LIMIT_PER_IP", "12")
)
VERIFY_RATE_LIMIT_PER_DEVICE = int(
    os.getenv("NIMHUNT_CLAIM_AUTH_VERIFY_RATE_LIMIT_PER_DEVICE", "8")
)

_DELEGATE = None
_INSTALLED = False


def _verify_path(scope: dict[str, Any]) -> bool:
    return (
        scope.get("type") == "http"
        and str(scope.get("method") or "").upper() == "POST"
        and str(scope.get("path") or "") == "/api/security/verify"
    )


def _device_from_body(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return claim_security._clean_device_id(payload.get("device_id_hash"))
    except ValueError:
        return None


async def guard_http_request_with_verify_rate_limit(
    app: ASGIApp,
    scope: dict[str, Any],
    receive: Callable[..., Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> bool:
    """Rate-limit public signature verification, then delegate other guards."""
    delegate = _DELEGATE
    if delegate is None:  # pragma: no cover - runtime requires install().
        raise RuntimeError("claim auth abuse guard is not installed")

    if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)) or not _verify_path(scope):
        return await delegate(app, scope, receive, send)

    body = await claim_security._read_request_body(receive)
    device_id = _device_from_body(body)
    source_ip = claim_network_security.scope_ip(scope)
    ip_fingerprint = claim_security._ip_hash(source_ip)

    async with get_db() as db:
        async with db_access.transaction(db, immediate=True):
            now = await db_access.get_unixepoch(db)
            allowed_ip, retry_ip = await claim_security._rate_limit_bucket(
                db,
                key=f"{claim_security.RATE_PREFIX}verify:ip:{ip_fingerprint}",
                now=now,
                window_seconds=claim_security.AUTH_RATE_WINDOW_SECONDS,
                limit=max(1, VERIFY_RATE_LIMIT_PER_IP),
            )
            allowed_device = True
            retry_device = int(now)
            if device_id is not None:
                allowed_device, retry_device = await claim_security._rate_limit_bucket(
                    db,
                    key=f"{claim_security.RATE_PREFIX}verify:device:{device_id}",
                    now=now,
                    window_seconds=claim_security.AUTH_RATE_WINDOW_SECONDS,
                    limit=max(1, VERIFY_RATE_LIMIT_PER_DEVICE),
                )

    if not allowed_ip or not allowed_device:
        retry_at = max(int(retry_ip), int(retry_device))
        response = claim_security._security_error_response(
            code="auth_verify_rate_limited",
            message="Too many wallet-verification attempts. Please try again later.",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            retry_at=retry_at,
        )
        await response(scope, claim_security._replay_receive(b""), send)
        return True

    # We consumed the ASGI body to rate-limit it. Replay it exactly once to the
    # application and mark the request consumed so outer middleware does not
    # attempt to read the exhausted receive channel again.
    await app(scope, claim_security._replay_receive(body), send)
    return True


def install() -> None:
    """Wrap the existing claim HTTP guard without replacing its protections."""
    global _DELEGATE, _INSTALLED
    if _INSTALLED:
        return
    _DELEGATE = claim_security.guard_http_request
    claim_security.guard_http_request = guard_http_request_with_verify_rate_limit
    _INSTALLED = True


__all__ = [
    "VERIFY_RATE_LIMIT_PER_DEVICE",
    "VERIFY_RATE_LIMIT_PER_IP",
    "guard_http_request_with_verify_rate_limit",
    "install",
]
