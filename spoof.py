"""
spoof.py

Development-only mock data generator for NimHunt.

Run from the project folder:

    python spoof.py

This script removes the local SQLite database, creates the current schema from
scratch, and adds a small, predictable mock dataset. Stop the FastAPI server
before running it, then restart the server so its cache uses the new database.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import constants as const
import database as schema
import db_access
from database import get_db, init_db

TEST_COUNTRY = "United Kingdom"


@dataclass(frozen=True, slots=True)
class MockUser:
    user_id: int
    display_name: str


@dataclass(frozen=True, slots=True)
class MockSpot:
    user_id: int
    title: str
    desc: str
    lat: float
    long: float
    city: str
    link: str
    starts_offset_seconds: int | None
    ends_offset_seconds: int | None
    status: int
    total_value: int = const.MIN_SPOT_TOTAL_VALUE


def _device_hash_for_user(user_id: int) -> str:
    """Return a stable 64-character fake device hash for a mock USER."""
    return f"{int(user_id):064x}"[-64:]


def _remove_existing_database_files() -> None:
    """Delete the local development database and its SQLite sidecar files."""
    db_path = Path(schema.DB_PATH)
    for candidate in (
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
    ):
        with suppress(FileNotFoundError):
            candidate.unlink()


async def _insert_mock_user(db, user: MockUser, *, now: int) -> None:
    await db.execute(
        f"""
        INSERT INTO {schema.USER_TABLE_NAME} (
            {schema.USER_ID},
            {schema.USER_DEVICE_ID_HASH},
            {schema.USER_DISPLAY_NAME},
            {schema.USER_STATUS},
            {schema.USER_CREATED_AT},
            {schema.USER_LAST_SEEN_AT}
        )
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            int(user.user_id),
            _device_hash_for_user(user.user_id),
            user.display_name,
            const.USER_STATUS_ACTIVE,
            int(now),
            int(now),
        ),
    )


async def _create_mock_spot(db, spot: MockSpot, *, now: int) -> int:
    starts_at = None if spot.starts_offset_seconds is None else int(now + spot.starts_offset_seconds)
    # SPOT.ends_at stores seconds after starts_at, not an absolute unix timestamp.
    if spot.ends_offset_seconds is None:
        ends_after = const.DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS
    else:
        start_offset = int(spot.starts_offset_seconds or 0)
        ends_after = max(const.MIN_SPOT_ENDS_AFTER_SECONDS, int(spot.ends_offset_seconds - start_offset))

    spot_id = await db_access.create_spot(
        db,
        created_by=spot.user_id,
        lat=spot.lat,
        long=spot.long,
        radius=100,
        claim_duration=0,
        max_claims_per_user=1,
        max_total_claims=1,
        total_value=spot.total_value,
        starts_at=starts_at,
        ends_at=ends_after,
        title=spot.title,
        desc=spot.desc,
        link=spot.link,
        city=spot.city,
        country=TEST_COUNTRY,
    )

    if spot.status != const.SPOT_STATUS_DRAFT:
        await db_access.modify_spot_status(db, spot_id=spot_id, status=spot.status)

    return int(spot_id)



async def _create_title_only_draft(db, *, user_id: int, title: str) -> int:
    """Create the new first-step Create Spot draft: only owner + title."""
    return await db_access.create_spot(
        db,
        created_by=int(user_id),
        title=title,
        link="mock-title-only-draft",
    )


async def _create_confirmed_deposit_for_spot(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
) -> int:
    tx_hash = "feed" + f"{spot_id:060x}"[-60:]
    trans_id = await db_access.create_spot_deposit_transaction(
        db,
        user_id=user_id,
        spot_id=spot_id,
        amount=amount,
        from_address="NQ00 MOCK CREATOR ADDRESS",
        tx_hash=tx_hash,
    )
    await db_access.set_transaction_status_to_confirmed(
        db,
        trans_id=trans_id,
        block_number=1_234_567,
    )
    return int(trans_id)


async def seed_mock_data() -> dict[str, Any]:
    """Recreate the database, seed a dynamic mock dataset, and return a summary."""
    if bool(getattr(const, "PRODUCTION_MODE", False)):
        raise RuntimeError("Refusing to reset or seed mock data in production mode")

    _remove_existing_database_files()
    await init_db()
    now = int(time.time())

    test_user_id = int(const.TEST_USER_ID)
    users = [
        MockUser(test_user_id, "Desktop Test User"),
        MockUser(1, "London Tester"),
        MockUser(2, "Northern Tester"),
    ]

    normal_spots = [
        # TEST_USER_ID: two ordinary published spots, plus two special drafts below.
        MockSpot(
            user_id=test_user_id,
            title="London Nimiq",
            desc="Active mock spot for checking the London area on the map.",
            lat=51.5074,
            long=-0.1278,
            city="London",
            link="mock-london-nimiq",
            starts_offset_seconds=-(2 * 60 * 60),
            ends_offset_seconds=7 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),
        MockSpot(
            user_id=test_user_id,
            title="Edinburgh Nimiq",
            desc="Upcoming mock spot for checking future spot display.",
            lat=55.9533,
            long=-3.1883,
            city="Edinburgh",
            link="mock-edinburgh-nimiq",
            starts_offset_seconds=6 * 60 * 60,
            ends_offset_seconds=8 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),

        # User 1.
        MockSpot(
            user_id=1,
            title="Glasgow Nimiq",
            desc="Active mock spot near Glasgow.",
            lat=55.8642,
            long=-4.2518,
            city="Glasgow",
            link="mock-glasgow-nimiq",
            starts_offset_seconds=-(45 * 60),
            ends_offset_seconds=5 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),
        MockSpot(
            user_id=1,
            title="Manchester Nimiq",
            desc="Upcoming mock spot near Manchester.",
            lat=53.4808,
            long=-2.2426,
            city="Manchester",
            link="mock-manchester-nimiq",
            starts_offset_seconds=2 * 24 * 60 * 60,
            ends_offset_seconds=9 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),
        MockSpot(
            user_id=1,
            title="Cardiff Nimiq",
            desc="Active mock spot near Cardiff.",
            lat=51.4816,
            long=-3.1791,
            city="Cardiff",
            link="mock-cardiff-nimiq",
            starts_offset_seconds=-(24 * 60 * 60),
            ends_offset_seconds=3 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),

        # User 2.
        MockSpot(
            user_id=2,
            title="Birmingham Nimiq",
            desc="Upcoming mock spot near Birmingham.",
            lat=52.4862,
            long=-1.8904,
            city="Birmingham",
            link="mock-birmingham-nimiq",
            starts_offset_seconds=12 * 60 * 60,
            ends_offset_seconds=6 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),
        MockSpot(
            user_id=2,
            title="Belfast Nimiq",
            desc="Active mock spot near Belfast.",
            lat=54.5973,
            long=-5.9301,
            city="Belfast",
            link="mock-belfast-nimiq",
            starts_offset_seconds=-(3 * 60 * 60),
            ends_offset_seconds=4 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),
        MockSpot(
            user_id=2,
            title="Newcastle Nimiq",
            desc="Upcoming mock spot near Newcastle.",
            lat=54.9783,
            long=-1.6178,
            city="Newcastle upon Tyne",
            link="mock-newcastle-nimiq",
            starts_offset_seconds=3 * 24 * 60 * 60,
            ends_offset_seconds=10 * 24 * 60 * 60,
            status=const.SPOT_STATUS_PUBLISHED,
        ),
    ]

    draft_unpaid = MockSpot(
        user_id=test_user_id,
        title="Unpaid For Spot",
        desc="Draft test spot with no deposit transaction. It should not be publishable yet.",
        lat=51.5007,
        long=-0.1246,
        city="London",
        link="mock-unpaid-for-spot",
        starts_offset_seconds=24 * 60 * 60,
        ends_offset_seconds=7 * 24 * 60 * 60,
        status=const.SPOT_STATUS_DRAFT,
        total_value=const.MIN_SPOT_TOTAL_VALUE,
    )
    draft_paid = MockSpot(
        user_id=test_user_id,
        title="Paid For Spot",
        desc="Draft test spot with a confirmed deposit transaction. It should be publishable.",
        lat=55.8609,
        long=-4.2514,
        city="Glasgow",
        link="mock-paid-for-spot",
        starts_offset_seconds=24 * 60 * 60,
        ends_offset_seconds=7 * 24 * 60 * 60,
        status=const.SPOT_STATUS_DRAFT,
        total_value=const.MIN_SPOT_TOTAL_VALUE,
    )

    async with get_db() as db:
        async with db_access.transaction(db):
            for user in users:
                await _insert_mock_user(db, user, now=now)

            spot_ids: list[int] = []
            for spot in normal_spots:
                spot_id = await _create_mock_spot(db, spot, now=now)
                spot_ids.append(spot_id)
                await _create_confirmed_deposit_for_spot(
                    db,
                    user_id=spot.user_id,
                    spot_id=spot_id,
                    amount=spot.total_value,
                )

            title_only_draft_id = await _create_title_only_draft(
                db,
                user_id=test_user_id,
                title="Title Only Draft",
            )

            unpaid_spot_id = await _create_mock_spot(db, draft_unpaid, now=now)
            paid_spot_id = await _create_mock_spot(db, draft_paid, now=now)
            trans_id = await _create_confirmed_deposit_for_spot(
                db,
                user_id=test_user_id,
                spot_id=paid_spot_id,
                amount=draft_paid.total_value,
            )

    return {
        "ok": True,
        "users": [user.user_id for user in users],
        "published_spot_count": len(spot_ids),
        "title_only_draft_id": title_only_draft_id,
        "draft_unpaid_spot_id": unpaid_spot_id,
        "draft_paid_spot_id": paid_spot_id,
        "confirmed_deposit_transaction_id": trans_id,
        "now": now,
    }


async def main() -> None:
    summary = await seed_mock_data()
    print("Mock NimHunt dataset created.")
    print(f"Users: {summary['users']}")
    print(f"Published spots: {summary['published_spot_count']}")
    print(f"Title-only draft spot id: {summary['title_only_draft_id']}")
    print(f"Unpaid draft spot id: {summary['draft_unpaid_spot_id']}")
    print(f"Paid draft spot id: {summary['draft_paid_spot_id']}")
    print(f"Confirmed deposit transaction id: {summary['confirmed_deposit_transaction_id']}")
    print("Restart the FastAPI server if it was already running, so cache.py reloads this data.")


if __name__ == "__main__":
    asyncio.run(main())
