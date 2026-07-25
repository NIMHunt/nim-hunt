"""Durable, disabled-by-default automatic X posting for newly active Spots.

Posting to an external service cannot be committed atomically with SQLite. NimHunt
therefore retries only outcomes that X clearly rejected before creating a Post
(such as rate limiting). Network failures and server errors are recorded as
uncertain and are not retried automatically, preferring one missed Post over a
duplicate public Post.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import constants as const
import database as schema
import db_access
import social_card_images
import social_preview
from database import get_db

logger = logging.getLogger(__name__)

X_CREATE_POST_URL = "https://api.x.com/2/tweets"
X_AUTHENTICATED_USER_URL = "https://api.x.com/2/users/me"
MODE_METADATA_KEY = "x_auto_post:mode"
CURSOR_METADATA_KEY = "x_auto_post:activation_cursor"
SPOT_METADATA_PREFIX = "x_auto_post:spot:"
MODE_ENABLED = "enabled"
MODE_DISABLED = "disabled"
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")

_X_POST_TASK: asyncio.Task | None = None
_X_POST_STOP_EVENT: asyncio.Event | None = None
_X_POST_LAST_RESULT: dict[str, Any] | None = None
_X_POST_LAST_ERROR: str | None = None
_X_VERIFIED_USERNAME: str | None = None


class XConfigurationError(RuntimeError):
    """The operator enabled posting without a complete, safe configuration."""


class XTransportError(RuntimeError):
    """No authoritative response arrived, so the posting outcome is ambiguous."""


@dataclass(frozen=True)
class XCredentials:
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str


@dataclass(frozen=True)
class XResponse:
    status: int
    data: dict[str, Any]
    headers: dict[str, str]


@dataclass(frozen=True, order=True)
class ActivationCursor:
    timestamp: int
    spot_id: int = 0


def normalise_account_handle(value: object) -> str:
    """Return an X username without @, rejecting invalid account handles."""
    handle = str(value or "").strip().lstrip("@").strip()
    if not _USERNAME_PATTERN.fullmatch(handle):
        raise XConfigurationError(
            "NIMHUNT_X_ACCOUNT_HANDLE must contain 1-15 letters, numbers, or underscores"
        )
    return handle


def _required_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise XConfigurationError(
            f"{name} must be configured when automatic X posting is enabled"
        )
    return value


def load_credentials() -> XCredentials:
    """Load user-context OAuth 1.0a credentials without ever logging them."""
    return XCredentials(
        api_key=_required_secret(const.NIMHUNT_X_API_KEY_ENV),
        api_secret=_required_secret(const.NIMHUNT_X_API_SECRET_ENV),
        access_token=_required_secret(const.NIMHUNT_X_ACCESS_TOKEN_ENV),
        access_token_secret=_required_secret(const.NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV),
    )


def validate_configuration() -> None:
    """Fail clearly when the opt-in flag is enabled without safe settings."""
    if not const.X_AUTO_POST_ENABLED:
        return
    normalise_account_handle(const.X_ACCOUNT_HANDLE)
    load_credentials()
    for name, value in (
        ("NIMHUNT_X_POST_INTERVAL_SECONDS", const.X_POST_INTERVAL_SECONDS),
        ("NIMHUNT_X_HTTP_TIMEOUT_SECONDS", const.X_HTTP_TIMEOUT_SECONDS),
        ("NIMHUNT_X_RETRY_AFTER_SECONDS", const.X_RETRY_AFTER_SECONDS),
        ("NIMHUNT_X_MAX_SPOTS_PER_RUN", const.X_MAX_SPOTS_PER_RUN),
    ):
        if int(value) <= 0:
            raise XConfigurationError(f"{name} must be greater than zero")


def _oauth_encode(value: object) -> str:
    return urllib.parse.quote(str(value), safe="~-._")


def oauth_authorization_header(
    method: str,
    url: str,
    credentials: XCredentials,
    *,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Create an OAuth 1.0a HMAC-SHA1 Authorization header for one request."""
    parsed = urllib.parse.urlsplit(url)
    base_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )
    oauth_params = {
        "oauth_consumer_key": credentials.api_key,
        "oauth_nonce": nonce or secrets.token_urlsafe(24),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(
            int(timestamp if timestamp is not None else time.time())
        ),
        "oauth_token": credentials.access_token,
        "oauth_version": "1.0",
    }
    signature_params = list(
        urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    )
    signature_params.extend(oauth_params.items())
    sorted_params = sorted(
        signature_params,
        key=lambda item: (_oauth_encode(item[0]), _oauth_encode(item[1])),
    )
    normalised = "&".join(
        f"{_oauth_encode(key)}={_oauth_encode(value)}"
        for key, value in sorted_params
    )
    signature_base = "&".join(
        (method.upper(), _oauth_encode(base_url), _oauth_encode(normalised))
    )
    signing_key = (
        f"{_oauth_encode(credentials.api_secret)}&"
        f"{_oauth_encode(credentials.access_token_secret)}"
    )
    digest = hmac.new(
        signing_key.encode("utf-8"),
        signature_base.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("ascii")
    values = ", ".join(
        f'{_oauth_encode(key)}="{_oauth_encode(value)}"'
        for key, value in sorted(oauth_params.items())
    )
    return f"OAuth {values}"


def _request_json_sync(
    method: str,
    url: str,
    credentials: XCredentials,
    *,
    payload: dict[str, Any] | None = None,
) -> XResponse:
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": oauth_authorization_header(method, url, credentials),
        "User-Agent": "NimHuntXAutoPoster/1.0 (+https://nimhunt.app)",
    }
    if payload is not None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(1, int(const.X_HTTP_TIMEOUT_SECONDS)),
        ) as response:
            raw = response.read()
            status = int(response.status)
            response_headers = {
                str(key).lower(): str(value)
                for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
        response_headers = {
            str(key).lower(): str(value)
            for key, value in (exc.headers.items() if exc.headers else ())
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise XTransportError(
            f"X request did not return an authoritative response: {type(exc).__name__}"
        ) from exc

    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"raw": raw.decode("utf-8", errors="replace")[:500]}
    if not isinstance(parsed, dict):
        parsed = {"data": parsed}
    return XResponse(status=status, data=parsed, headers=response_headers)


async def request_json(
    method: str,
    url: str,
    credentials: XCredentials,
    *,
    payload: dict[str, Any] | None = None,
) -> XResponse:
    return await asyncio.to_thread(
        _request_json_sync,
        method,
        url,
        credentials,
        payload=payload,
    )


async def verify_posting_account(credentials: XCredentials) -> str:
    """Prove the credentials belong to the configured account handle."""
    response = await request_json("GET", X_AUTHENTICATED_USER_URL, credentials)
    if response.status != 200:
        raise XConfigurationError(
            "X account verification failed with HTTP "
            f"{response.status}; check App permissions and tokens"
        )
    data = response.data.get("data")
    username = str(
        data.get("username") if isinstance(data, dict) else ""
    ).strip()
    if not username:
        raise XConfigurationError(
            "X account verification response did not include a username"
        )
    expected = normalise_account_handle(const.X_ACCOUNT_HANDLE)
    if username.lower() != expected.lower():
        raise XConfigurationError(
            f"X credentials belong to @{username}, not configured account @{expected}"
        )
    return username


def build_spot_post_text(spot: dict[str, Any]) -> str:
    """Build the public Post whose URL supplies NimHunt's existing social card."""
    title = " ".join(
        str(spot.get(schema.SPOT_TITLE) or "NimHunt Spot").split()
    )
    ref = str(spot.get(schema.SPOT_LINK) or spot[schema.SPOT_ID])
    url = social_preview.public_url(f"{const.SPOT_PAGE_URL_PREFIX}/{ref}")
    kind = (
        "Prizedraw"
        if spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
        else "Spot"
    )
    return f"A new NimHunt {kind} is now active!\n\n{title}\n\n{url}"


def _spot_state_key(spot_id: int) -> str:
    return f"{SPOT_METADATA_PREFIX}{int(spot_id)}"


async def _get_metadata(db, key: str) -> str | None:
    cur = await db.execute(
        f"SELECT {schema.APP_METADATA_VALUE} "
        f"FROM {schema.APP_METADATA_TABLE_NAME} "
        f"WHERE {schema.APP_METADATA_KEY} = ?;",
        (key,),
    )
    row = await cur.fetchone()
    return str(row[0]) if row is not None else None


async def _set_metadata(db, key: str, value: str) -> None:
    await db.execute(
        f"""
        INSERT INTO {schema.APP_METADATA_TABLE_NAME}
            ({schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE})
        VALUES (?, ?)
        ON CONFLICT({schema.APP_METADATA_KEY}) DO UPDATE
        SET {schema.APP_METADATA_VALUE} = excluded.{schema.APP_METADATA_VALUE};
        """,
        (key, value),
    )


def _decode_cursor(raw: str | None, *, fallback: int) -> ActivationCursor:
    if raw is None:
        return ActivationCursor(max(0, int(fallback)), 0)
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            return ActivationCursor(
                max(0, int(decoded.get("timestamp") or 0)),
                max(0, int(decoded.get("spot_id") or 0)),
            )
        return ActivationCursor(max(0, int(decoded)), 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ActivationCursor(max(0, int(fallback)), 0)


async def _get_cursor(db, *, now: int) -> ActivationCursor:
    return _decode_cursor(
        await _get_metadata(db, CURSOR_METADATA_KEY),
        fallback=now,
    )


async def _set_cursor(db, cursor: ActivationCursor) -> None:
    await _set_metadata(
        db,
        CURSOR_METADATA_KEY,
        json.dumps(
            {
                "timestamp": max(0, int(cursor.timestamp)),
                "spot_id": max(0, int(cursor.spot_id)),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _decode_state(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"state": "uncertain", "reason": "invalid_persisted_state"}
    if isinstance(value, dict):
        return value
    return {"state": "uncertain", "reason": "invalid_persisted_state"}


async def _set_spot_state(
    db,
    spot_id: int,
    state: dict[str, Any],
) -> None:
    await _set_metadata(
        db,
        _spot_state_key(spot_id),
        json.dumps(state, sort_keys=True, separators=(",", ":")),
    )


async def _prepare_mode(
    db,
    *,
    enabled: bool,
    now: int,
) -> tuple[bool, ActivationCursor]:
    """Persist opt-in transitions and prevent an old disabled-period backlog."""
    desired_mode = MODE_ENABLED if enabled else MODE_DISABLED
    stored_mode = await _get_metadata(db, MODE_METADATA_KEY)
    raw_cursor = await _get_metadata(db, CURSOR_METADATA_KEY)
    transitioned = stored_mode != desired_mode or raw_cursor is None
    if transitioned:
        cursor = ActivationCursor(int(now), 0)
        await _set_metadata(db, MODE_METADATA_KEY, desired_mode)
        await _set_cursor(db, cursor)
        return True, cursor
    return False, _decode_cursor(raw_cursor, fallback=now)


async def prepare_disabled_mode() -> ActivationCursor:
    """Record that no activations from the disabled period should be backfilled."""
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        _transitioned, cursor = await _prepare_mode(
            db,
            enabled=False,
            now=now,
        )
        # Refresh the cursor at every disabled startup. If the flag is later
        # enabled, the mode transition resets it again to that enable-time.
        cursor = ActivationCursor(now, 0)
        await _set_cursor(db, cursor)
        await db.commit()
    return cursor


async def _candidate_spots(
    db,
    *,
    cursor: ActivationCursor,
    now: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        f"""
        WITH eligible AS (
            SELECT
                s.*,
                pd.{schema.PRIZEDRAW_PRIZE_COUNT},
                MAX(s.{schema.SPOT_STARTS_AT}, s.{schema.SPOT_UPDATED_AT})
                    AS activation_at
            FROM {schema.SPOT_TABLE_NAME} s
            LEFT JOIN {schema.PRIZEDRAW_TABLE_NAME} pd
                ON pd.{schema.PRIZEDRAW_SPOT_ID} = s.{schema.SPOT_ID}
            WHERE s.{schema.SPOT_STATUS} = ?
              AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
              AND s.{schema.SPOT_LAT} IS NOT NULL
              AND s.{schema.SPOT_LONG} IS NOT NULL
              AND s.{schema.SPOT_STARTS_AT} IS NOT NULL
              AND s.{schema.SPOT_STARTS_AT} <= ?
              AND (s.{schema.SPOT_STARTS_AT} + s.{schema.SPOT_ENDS_AT}) > ?
        )
        SELECT *
        FROM eligible
        WHERE activation_at > ?
           OR (activation_at = ? AND {schema.SPOT_ID} > ?)
        ORDER BY activation_at ASC, {schema.SPOT_ID} ASC
        LIMIT ?;
        """,
        (
            const.SPOT_STATUS_PUBLISHED,
            int(now),
            int(now),
            int(cursor.timestamp),
            int(cursor.timestamp),
            int(cursor.spot_id),
            max(1, int(limit)),
        ),
    )
    return [dict(row) for row in rows]


async def _due_retry_spots(
    db,
    *,
    now: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        f"""
        SELECT {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE}
        FROM {schema.APP_METADATA_TABLE_NAME}
        WHERE {schema.APP_METADATA_KEY} LIKE ?;
        """,
        (f"{SPOT_METADATA_PREFIX}%",),
    )
    spot_ids: list[int] = []
    for row in rows:
        state = _decode_state(str(row[schema.APP_METADATA_VALUE]))
        if not state or state.get("state") != "retry":
            continue
        if int(state.get("retry_at") or 0) > int(now):
            continue
        try:
            spot_ids.append(
                int(
                    str(row[schema.APP_METADATA_KEY]).removeprefix(
                        SPOT_METADATA_PREFIX
                    )
                )
            )
        except ValueError:
            continue
    if not spot_ids:
        return []

    spot_ids = spot_ids[: max(1, int(limit))]
    placeholders = ",".join("?" for _ in spot_ids)
    result = await db.execute_fetchall(
        f"""
        SELECT s.*, pd.{schema.PRIZEDRAW_PRIZE_COUNT}
        FROM {schema.SPOT_TABLE_NAME} s
        LEFT JOIN {schema.PRIZEDRAW_TABLE_NAME} pd
            ON pd.{schema.PRIZEDRAW_SPOT_ID} = s.{schema.SPOT_ID}
        WHERE s.{schema.SPOT_ID} IN ({placeholders})
          AND s.{schema.SPOT_STATUS} = ?
          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
          AND s.{schema.SPOT_LAT} IS NOT NULL
          AND s.{schema.SPOT_LONG} IS NOT NULL
          AND s.{schema.SPOT_STARTS_AT} IS NOT NULL
          AND s.{schema.SPOT_STARTS_AT} <= ?
          AND (s.{schema.SPOT_STARTS_AT} + s.{schema.SPOT_ENDS_AT}) > ?
        ORDER BY s.{schema.SPOT_ID} ASC;
        """,
        (*spot_ids, const.SPOT_STATUS_PUBLISHED, int(now), int(now)),
    )
    return [dict(row) for row in result]


async def prewarm_spot_card(spot: dict[str, Any]) -> None:
    """Generate/cache the social image before X crawls the shared Spot URL."""
    ref = str(spot.get(schema.SPOT_LINK) or spot[schema.SPOT_ID])
    is_prizedraw = spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    await asyncio.to_thread(
        social_card_images.cached_card,
        f"spot:{ref}",
        lambda: social_card_images.render_spot_card(spot, is_prizedraw),
    )


def _retry_at(response: XResponse, *, now: int) -> int:
    candidates = [int(now) + int(const.X_RETRY_AFTER_SECONDS)]
    try:
        candidates.append(int(response.headers.get("x-rate-limit-reset", "0")) + 1)
    except ValueError:
        pass
    try:
        retry_after = int(response.headers.get("retry-after", "0"))
        candidates.append(int(now) + retry_after)
    except ValueError:
        pass
    return max(candidates)


async def post_spot_once(
    spot: dict[str, Any],
    credentials: XCredentials,
    *,
    now: int,
) -> dict[str, Any]:
    """Attempt one durable public Post without retrying ambiguous outcomes."""
    spot_id = int(spot[schema.SPOT_ID])
    state_key = _spot_state_key(spot_id)
    async with get_db() as db:
        existing = _decode_state(await _get_metadata(db, state_key))
        if existing:
            state_name = str(existing.get("state") or "recorded")
            if state_name != "retry":
                return {
                    "spot_id": spot_id,
                    "posted": False,
                    "reason": f"already_{state_name}",
                }
            if int(existing.get("retry_at") or 0) > int(now):
                return {
                    "spot_id": spot_id,
                    "posted": False,
                    "waiting": True,
                    "reason": "retry_not_due",
                }

    try:
        await prewarm_spot_card(spot)
    except Exception as exc:
        async with get_db() as db:
            await _set_spot_state(
                db,
                spot_id,
                {
                    "state": "retry",
                    "retry_at": int(now) + int(const.X_RETRY_AFTER_SECONDS),
                    "reason": "card_prewarm_failed",
                    "detail": type(exc).__name__,
                },
            )
            await db.commit()
        return {
            "spot_id": spot_id,
            "posted": False,
            "retry": True,
            "reason": "card_prewarm_failed",
        }

    async with get_db() as db:
        await _set_spot_state(
            db,
            spot_id,
            {
                "state": "sending",
                "sending_at": int(now),
                "account": normalise_account_handle(const.X_ACCOUNT_HANDLE),
            },
        )
        await db.commit()

    text = build_spot_post_text(spot)
    try:
        response = await request_json(
            "POST",
            X_CREATE_POST_URL,
            credentials,
            payload={"text": text},
        )
    except XTransportError as exc:
        final_state = {
            "state": "uncertain",
            "uncertain_at": int(now),
            "reason": "ambiguous_transport_failure",
            "detail": type(exc).__name__,
        }
        result = {
            "spot_id": spot_id,
            "posted": False,
            "uncertain": True,
            "reason": "ambiguous_transport_failure",
        }
    else:
        final_state, result = _classify_post_response(
            response,
            spot_id=spot_id,
            now=now,
        )

    async with get_db() as db:
        await _set_spot_state(db, spot_id, final_state)
        await db.commit()
    return result


def _classify_post_response(
    response: XResponse,
    *,
    spot_id: int,
    now: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if response.status == 201:
        data = response.data.get("data")
        post_id = str(
            data.get("id") if isinstance(data, dict) else ""
        ).strip()
        if post_id:
            return (
                {
                    "state": "posted",
                    "post_id": post_id,
                    "posted_at": int(now),
                    "account": normalise_account_handle(const.X_ACCOUNT_HANDLE),
                },
                {"spot_id": spot_id, "posted": True, "post_id": post_id},
            )
        return (
            {
                "state": "uncertain",
                "uncertain_at": int(now),
                "reason": "success_response_missing_post_id",
            },
            {
                "spot_id": spot_id,
                "posted": False,
                "uncertain": True,
                "reason": "missing_post_id",
            },
        )

    # These responses authoritatively reject the request before a Post exists,
    # so retrying later cannot create a duplicate.
    if response.status in {401, 403, 429}:
        return (
            {
                "state": "retry",
                "retry_at": _retry_at(response, now=now),
                "reason": f"x_http_{response.status}",
            },
            {
                "spot_id": spot_id,
                "posted": False,
                "retry": True,
                "reason": f"x_http_{response.status}",
            },
        )

    # A server error may occur after X accepted the request. Do not guess.
    if 500 <= response.status <= 599:
        return (
            {
                "state": "uncertain",
                "uncertain_at": int(now),
                "reason": f"x_http_{response.status}",
            },
            {
                "spot_id": spot_id,
                "posted": False,
                "uncertain": True,
                "reason": f"x_http_{response.status}",
            },
        )

    return (
        {
            "state": "failed",
            "failed_at": int(now),
            "reason": f"x_http_{response.status}",
        },
        {
            "spot_id": spot_id,
            "posted": False,
            "failed": True,
            "reason": f"x_http_{response.status}",
        },
    )


async def run_x_auto_post_pass() -> dict[str, Any]:
    """Post each newly-active Spot once and advance the durable cursor safely."""
    global _X_VERIFIED_USERNAME

    if not const.X_AUTO_POST_ENABLED:
        cursor = await prepare_disabled_mode()
        return {
            "ok": True,
            "enabled": False,
            "cursor": cursor.__dict__,
            "checked_count": 0,
            "posted_count": 0,
        }

    validate_configuration()
    credentials = load_credentials()
    if _X_VERIFIED_USERNAME is None:
        _X_VERIFIED_USERNAME = await verify_posting_account(credentials)

    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
        transitioned, cursor = await _prepare_mode(
            db,
            enabled=True,
            now=now,
        )
        await db.commit()
        if transitioned:
            return {
                "ok": True,
                "enabled": True,
                "account": _X_VERIFIED_USERNAME,
                "initialised_cursor": cursor.__dict__,
                "checked_count": 0,
                "posted_count": 0,
            }

        maximum = max(1, int(const.X_MAX_SPOTS_PER_RUN))
        retries = await _due_retry_spots(
            db,
            now=now,
            limit=maximum,
        )
        retry_ids = {int(spot[schema.SPOT_ID]) for spot in retries}
        remaining = max(0, maximum - len(retries))
        candidates = (
            await _candidate_spots(
                db,
                cursor=cursor,
                now=now,
                limit=remaining,
            )
            if remaining
            else []
        )

    candidate_results: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for spot in retries:
        results.append(await post_spot_once(spot, credentials, now=now))
    for spot in candidates:
        spot_id = int(spot[schema.SPOT_ID])
        if spot_id in retry_ids:
            continue
        result = await post_spot_once(spot, credentials, now=now)
        candidate_results.append(result)
        results.append(result)

    if remaining == 0:
        # Retry work consumed this pass. Preserve the activation cursor so new
        # Spots are not skipped merely because older safe retries were due.
        next_cursor = cursor
    elif candidates and len(candidates) >= remaining:
        last = candidates[-1]
        next_cursor = ActivationCursor(
            int(last.get("activation_at") or cursor.timestamp),
            int(last[schema.SPOT_ID]),
        )
    else:
        next_cursor = ActivationCursor(int(now), 0)

    async with get_db() as db:
        await _set_cursor(db, next_cursor)
        await db.commit()

    return {
        "ok": not any(
            result.get("failed") or result.get("uncertain")
            for result in results
        ),
        "enabled": True,
        "account": _X_VERIFIED_USERNAME,
        "cursor_before": cursor.__dict__,
        "cursor_after": next_cursor.__dict__,
        "checked_count": len(results),
        "candidate_count": len(candidate_results),
        "posted_count": sum(1 for result in results if result.get("posted")),
        "retry_count": sum(1 for result in results if result.get("retry")),
        "uncertain_count": sum(
            1 for result in results if result.get("uncertain")
        ),
        "failed_count": sum(1 for result in results if result.get("failed")),
        "results": results,
    }


async def _x_auto_post_loop(interval_seconds: int) -> None:
    global _X_POST_LAST_RESULT, _X_POST_LAST_ERROR

    stop_event = _X_POST_STOP_EVENT
    if stop_event is None:
        return
    while not stop_event.is_set():
        try:
            _X_POST_LAST_RESULT = await run_x_auto_post_pass()
            _X_POST_LAST_ERROR = (
                None
                if _X_POST_LAST_RESULT.get("ok", True)
                else repr(_X_POST_LAST_RESULT)
            )
            if _X_POST_LAST_ERROR:
                logger.error(
                    "Automatic X posting pass reported failure: %s",
                    _X_POST_LAST_RESULT,
                )
        except Exception as exc:  # pragma: no cover - defensive loop guard
            _X_POST_LAST_ERROR = repr(exc)
            logger.exception("Automatic X posting pass failed")
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=max(1, int(interval_seconds)),
            )


async def start_x_auto_poster(
    *,
    run_immediately: bool = False,
    interval_seconds: int | None = None,
) -> None:
    """Start the opt-in worker, or record disabled mode without making requests."""
    global _X_POST_TASK, _X_POST_STOP_EVENT
    global _X_POST_LAST_RESULT, _X_POST_LAST_ERROR

    if _X_POST_TASK is not None and not _X_POST_TASK.done():
        return
    if not const.X_AUTO_POST_ENABLED:
        cursor = await prepare_disabled_mode()
        _X_POST_LAST_RESULT = {
            "ok": True,
            "enabled": False,
            "cursor": cursor.__dict__,
            "checked_count": 0,
            "posted_count": 0,
        }
        _X_POST_LAST_ERROR = None
        return

    validate_configuration()
    _X_POST_STOP_EVENT = asyncio.Event()
    if run_immediately:
        try:
            _X_POST_LAST_RESULT = await run_x_auto_post_pass()
            _X_POST_LAST_ERROR = (
                None
                if _X_POST_LAST_RESULT.get("ok", True)
                else repr(_X_POST_LAST_RESULT)
            )
        except Exception as exc:
            _X_POST_LAST_ERROR = repr(exc)
            logger.exception("Initial automatic X posting pass failed")
    _X_POST_TASK = asyncio.create_task(
        _x_auto_post_loop(
            int(interval_seconds or const.X_POST_INTERVAL_SECONDS)
        )
    )


def x_auto_poster_status() -> dict[str, Any]:
    """Return a secret-free worker snapshot suitable for health diagnostics."""
    account = None
    if const.X_ACCOUNT_HANDLE:
        try:
            account = normalise_account_handle(const.X_ACCOUNT_HANDLE)
        except XConfigurationError:
            account = "invalid"
    return {
        "enabled": bool(const.X_AUTO_POST_ENABLED),
        "account": account,
        "running": _X_POST_TASK is not None and not _X_POST_TASK.done(),
        "last_error": _X_POST_LAST_ERROR,
        "last_result": _X_POST_LAST_RESULT,
        "interval_seconds": int(const.X_POST_INTERVAL_SECONDS),
    }


async def stop_x_auto_poster() -> None:
    global _X_POST_TASK, _X_POST_STOP_EVENT, _X_VERIFIED_USERNAME

    if _X_POST_STOP_EVENT is not None:
        _X_POST_STOP_EVENT.set()
    if _X_POST_TASK is not None:
        _X_POST_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await _X_POST_TASK
    _X_POST_TASK = None
    _X_POST_STOP_EVENT = None
    _X_VERIFIED_USERNAME = None
