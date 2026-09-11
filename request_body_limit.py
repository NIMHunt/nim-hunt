"""Single application-wide ASGI request-body size boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict[str, Any]]],
        Callable[[dict[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]


class RequestBodyLimitMiddleware:
    """Buffer and replay bodies up to a limit, rejecting excess incrementally.

    Buffering here ensures no parser, authentication guard, database operation,
    or signature/RPC verifier can run until the complete body is known to fit.
    The inner claim-security middleware can therefore retain its existing replay
    behaviour without becoming a second, subtly different size boundary.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max(1, int(max_body_bytes))

    @staticmethod
    def _content_length(scope: dict[str, Any]) -> int | None:
        for name, value in scope.get("headers") or ():
            if bytes(name).lower() != b"content-length":
                continue
            try:
                length = int(bytes(value).strip())
            except ValueError:
                return None
            return length if length >= 0 else None
        return None

    async def _reject(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        response = JSONResponse(
            {
                "ok": False,
                "code": "request_body_too_large",
                "message": "Request body is too large.",
            },
            status_code=413,
        )
        await response(scope, receive, send)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await self._reject(scope, receive, send)
            return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                disconnected = True
                break
            if message_type != "http.request":
                continue
            chunk = bytes(message.get("body") or b"")
            if len(body) + len(chunk) > self.max_body_bytes:
                await self._reject(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        sent = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.disconnect" if disconnected else "http.request",
                    "body": b"" if disconnected else bytes(body),
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


__all__ = ["RequestBodyLimitMiddleware"]
