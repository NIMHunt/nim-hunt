"""Authentication helpers for NimHunt's isolated administrator panel."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time


ADMIN_PASSWORD_HASH_ENV = "NIMHUNT_ADMIN_PASSWORD_HASH"
ADMIN_SESSION_COOKIE = "nimhunt_admin_session"
ADMIN_SESSION_SECONDS = 30 * 60

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024

# This key deliberately exists only for the lifetime of one app process.
# A deployment/restart therefore invalidates every old admin browser session.
_SESSION_SIGNING_KEY = secrets.token_bytes(32)


class AdminSession:
    """Small immutable-by-convention value object for one validated session."""

    __slots__ = ("expires_at", "csrf_token", "nonce")

    def __init__(self, *, expires_at: int, csrf_token: str, nonce: str):
        self.expires_at = int(expires_at)
        self.csrf_token = str(csrf_token)
        self.nonce = str(nonce)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    value = str(value or "").strip()
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def hash_admin_password(password: str, *, salt: bytes | None = None) -> str:
    """Return a Railway-safe scrypt hash for one administrator password."""
    password = str(password or "")
    if len(password) < 16:
        raise ValueError("admin password must contain at least 16 characters")
    salt = secrets.token_bytes(16) if salt is None else bytes(salt)
    if len(salt) < 16:
        raise ValueError("admin password salt must contain at least 16 bytes")
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        f"{_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_admin_password(password: str, encoded_hash: str | None = None) -> bool:
    """Compare a candidate against the configured Railway hash in constant time."""
    encoded = (
        os.getenv(ADMIN_PASSWORD_HASH_ENV, "")
        if encoded_hash is None
        else str(encoded_hash or "")
    ).strip()
    if not encoded:
        return False

    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        if (n, r, p) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
        if len(salt) < 16 or len(expected) != _SCRYPT_DKLEN:
            return False
        actual = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(actual, expected)


def admin_password_is_configured() -> bool:
    return bool(os.getenv(ADMIN_PASSWORD_HASH_ENV, "").strip())


def create_admin_session(*, now: int | None = None) -> tuple[str, AdminSession]:
    """Create one signed, short-lived session token."""
    issued_at = int(time.time() if now is None else now)
    session = AdminSession(
        expires_at=issued_at + ADMIN_SESSION_SECONDS,
        csrf_token=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(18),
    )
    payload = f"{session.expires_at}.{session.csrf_token}.{session.nonce}"
    signature = hmac.new(
        _SESSION_SIGNING_KEY,
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{payload}.{_b64encode(signature)}", session


def read_admin_session(token: str | None, *, now: int | None = None) -> AdminSession | None:
    """Validate one signed admin session without storing server-side session data."""
    try:
        expires_raw, csrf_token, nonce, signature_raw = str(token or "").split(".", 3)
        expires_at = int(expires_raw)
        if expires_at <= int(time.time() if now is None else now):
            return None
        if len(csrf_token) < 16 or len(nonce) < 12:
            return None
        payload = f"{expires_at}.{csrf_token}.{nonce}"
        expected = hmac.new(
            _SESSION_SIGNING_KEY,
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        supplied = _b64decode(signature_raw)
    except (ValueError, TypeError):
        return None

    if not hmac.compare_digest(supplied, expected):
        return None
    return AdminSession(expires_at=expires_at, csrf_token=csrf_token, nonce=nonce)


def verify_csrf(session: AdminSession, supplied_token: str | None) -> bool:
    return bool(
        supplied_token
        and hmac.compare_digest(session.csrf_token, str(supplied_token))
    )
