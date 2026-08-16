"""Preserve after-response settlement semantics for protected claim routes.

``claim_security.guard_http_request`` has to inspect a successful claim response
before it can persist the durable security record. Its original implementation
therefore captures the ASGI response while the FastAPI endpoint runs. Starlette
runs ``BackgroundTasks`` only after its final response-body ``send`` returns, so
capturing that send until the whole app call finishes accidentally also waits
for settlement before the browser receives anything.

This adapter creates a narrow response boundary around the existing guard:

* the FastAPI request runs in a task;
* the task is paused immediately after emitting its final response body, before
  Starlette can begin BackgroundTasks;
* the unchanged claim-security guard records any successful claim and forwards
  the captured response to the real client;
* only then is the FastAPI task released to run settlement work.

No authentication, risk, audit, or payout decision is reimplemented here. The
existing guard remains authoritative; this module changes only response timing.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Awaitable, Callable

import claim_security

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict[str, Any]]],
        Callable[[dict[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]

logger = logging.getLogger(__name__)

_ORIGINAL_GUARD = claim_security.guard_http_request
_INSTALLED = False


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def guard_http_request_with_response_delivery(
    app: ASGIApp,
    scope: dict[str, Any],
    receive: Callable[..., Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> bool:
    """Run the existing guard while pausing BackgroundTasks after the response.

    Unprotected requests never invoke ``app`` through this adapter because the
    underlying guard returns ``False`` first, so their ordinary middleware path
    is unchanged.
    """

    release_background = asyncio.Event()
    app_task: asyncio.Task[Any] | None = None

    async def app_until_response_complete(
        inner_scope: dict[str, Any],
        inner_receive: Callable[..., Awaitable[dict[str, Any]]],
        inner_send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        nonlocal app_task
        response_complete = asyncio.Event()

        async def gated_send(message: dict[str, Any]) -> None:
            await inner_send(message)
            if (
                message.get("type") == "http.response.body"
                and not bool(message.get("more_body", False))
            ):
                # Starlette awaits this send before it starts BackgroundTasks.
                # Let the guard observe/record/forward the response, but do not
                # let settlement begin until the real client has received it.
                response_complete.set()
                await release_background.wait()

        app_task = asyncio.create_task(
            app(inner_scope, inner_receive, gated_send),
            name="nimhunt-protected-claim-request",
        )
        response_waiter = asyncio.create_task(response_complete.wait())
        done, _pending = await asyncio.wait(
            {app_task, response_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if response_waiter in done:
            # ``app_task`` is now deliberately blocked inside ``gated_send``.
            # Returning lets the existing claim guard inspect the completed
            # response, persist its security record, and forward it to the user.
            return

        # The application ended (or failed) before a complete response was
        # produced. Do not turn that into an apparently successful guard pass.
        response_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await response_waiter
        await app_task
        raise RuntimeError("Protected claim request ended without a complete HTTP response")

    try:
        consumed = await _ORIGINAL_GUARD(
            app_until_response_complete,
            scope,
            receive,
            send,
        )
    except Exception:
        # If the security guard itself fails before forwarding the response,
        # settlement must not be allowed to race ahead behind its back.
        await _cancel_task(app_task)
        raise

    if app_task is None:
        return consumed

    # The existing guard has now persisted the security record (for successful
    # claim creation) and sent the response to the real ASGI ``send`` callable.
    # Release Starlette's BackgroundTasks only after that boundary is complete.
    release_background.set()
    try:
        await app_task
    except Exception:
        # The response is already complete. Background settlement is deliberately
        # best-effort here: the normal settlement loop will retry durable work.
        logger.exception("Protected claim background task failed after response delivery")

    return consumed


def install() -> None:
    """Install the response-ordering adapter around the primary claim guard."""
    global _INSTALLED
    if _INSTALLED:
        return
    claim_security.guard_http_request = guard_http_request_with_response_delivery
    _INSTALLED = True


__all__ = ["guard_http_request_with_response_delivery", "install"]
