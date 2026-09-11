"""Shared durable admission and cancellation-safe deposit derivation."""

from __future__ import annotations

import asyncio

import constants as const
import wallet


class DraftCreationRateLimited(Exception):
    """The durable per-user or global rolling allowance is exhausted."""


_derivation_semaphore = asyncio.Semaphore(
    int(getattr(const, "DRAFT_DERIVATION_MAX_CONCURRENCY", 4))
)


async def derive_reserved_deposit(key_index: int):
    """Derive off the event loop while retaining capacity through cancellation.

    ``to_thread`` cannot cancel work already running in its worker. The worker
    task, rather than its awaiting request, therefore owns the semaphore slot.
    """
    await _derivation_semaphore.acquire()

    async def run():
        try:
            return await asyncio.to_thread(wallet.derive_spot_deposit_address, int(key_index))
        finally:
            _derivation_semaphore.release()

    task = asyncio.create_task(run())
    return await asyncio.shield(task)
