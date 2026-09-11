"""Shared durable admission and cancellation-safe deposit derivation."""

from __future__ import annotations

import asyncio

import constants as const
import db_access
import wallet
from database import get_db


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


async def derive_admitted_deposit(*, reservation_id: int, user_id: int, key_index: int):
    """Derive and durably charge successful expensive work to its admission.

    The whole operation is shielded so cancellation cannot leave successfully
    completed worker work in the recoverable-pending state.
    """

    async def derive_and_consume():
        record = await derive_reserved_deposit(key_index)
        async with get_db() as db:
            async with db_access.transaction(db, immediate=True):
                await db_access.consume_draft_creation(
                    db, reservation_id=reservation_id, user_id=user_id
                )
        return record

    task = asyncio.create_task(derive_and_consume())
    return await asyncio.shield(task)
