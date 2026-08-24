"""Reliable source-network extraction for claim-security signals.

NimHunt is deployed behind Railway's HTTP edge. Railway documents X-Real-IP as
the header containing the client's remote IP. Using the rightmost
X-Forwarded-For entry can instead identify an internal proxy hop and make many
unrelated users look identical.

The network address is only a secondary anti-abuse signal; it never establishes
a user's identity. We still validate it strictly and fall back to the ASGI peer
when Railway's header is absent or malformed.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import Request

import claim_security

_INSTALLED = False


def _normalise_ip(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    # X-Real-IP should contain a bare address. Bracketed IPv6 is harmless to
    # accept, but never parse arbitrary host:port strings from a header.
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return None


def _header_map(scope: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        try:
            name = bytes(key).decode("latin-1").lower()
            text = bytes(value).decode("latin-1")
        except Exception:
            continue
        headers[name] = text
    return headers


def request_ip(request: Request) -> str:
    """Return Railway's validated client IP, else the immediate ASGI peer."""
    railway_ip = _normalise_ip(request.headers.get("x-real-ip"))
    if railway_ip:
        return railway_ip

    if request.client and request.client.host:
        peer = _normalise_ip(request.client.host)
        if peer:
            return peer
    return "unknown"


def scope_ip(scope: dict[str, Any]) -> str:
    """ASGI equivalent of request_ip() for the outer claim middleware."""
    headers = _header_map(scope)
    railway_ip = _normalise_ip(headers.get("x-real-ip"))
    if railway_ip:
        return railway_ip

    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        peer = _normalise_ip(client[0])
        if peer:
            return peer
    return "unknown"


def install() -> None:
    """Replace claim_security's proxy-IP helpers with Railway-safe versions."""
    global _INSTALLED
    if _INSTALLED:
        return
    claim_security._request_ip = request_ip
    claim_security._scope_ip = scope_ip
    _INSTALLED = True


__all__ = ["install", "request_ip", "scope_ip"]
