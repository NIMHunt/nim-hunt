"""Low-level ASGI helpers shared by claim security boundaries.

Request-body replay is plumbing only. The outer RequestBodyLimitMiddleware must
run before callers use ``read_request_body`` so buffering is always bounded.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi.responses import JSONResponse


async def read_request_body(
    receive: Callable[..., Awaitable[dict[str, Any]]],
) -> bytes:
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


def replay_receive(body: bytes) -> Callable[..., Awaitable[dict[str, Any]]]:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def error_response(*, code: str, message: str, http_status: int, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "code": str(code), "message": str(message), **extra},
        status_code=int(http_status),
    )


def response_json(messages: list[dict[str, Any]]) -> tuple[int, dict[str, Any] | None]:
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
