"""
spoof.py

Development-only mock data generator for NimHunt.

Run from the project folder:

    python spoof.py

This script removes the local SQLite database, creates the current schema from
scratch, and adds a presentation-friendly but rule-valid mock dataset. Stop the
FastAPI server before running it, then restart the server so its cache uses the
new database.
"""

from __future__ import annotations

import asyncio
import sqlite3
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
MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR


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
    active_for_seconds: int
    radius: int = 100
    claim_duration: int = 0
    max_claims_per_user: int = 1
    max_total_claims: int = 1
    total_value_nim: int = const.MIN_SPOT_TOTAL_VALUE_NIM
    use_password: bool = False
    is_prizedraw: bool = False
    prize_count: int = 1

    @property
    def total_value(self) -> int:
        return int(self.total_value_nim) * int(const.LUNA_PER_NIM)


PRESENTATION_SPOTS: tuple[MockSpot, ...] = (
    # Central London: the first ten active Spots finish soonest, so the initial
    # map view presents a dense, overlapping cluster around Westminster.
    MockSpot(
        1,
        "Trafalgar Square Welcome",
        "Visit Trafalgar Square and collect a quick reward while you explore.",
        51.5080,
        -0.1281,
        "London",
        "demo-trafalgar-welcome",
        -(30 * MINUTE),
        3 * HOUR,
        radius=220,
        max_total_claims=8,
        total_value_nim=800,
    ),
    MockSpot(
        2,
        "National Gallery Challenge",
        "Remain by the National Gallery for ten minutes to complete this art stop.",
        51.5089,
        -0.1283,
        "London",
        "demo-national-gallery",
        -HOUR,
        4 * HOUR,
        radius=140,
        claim_duration=10 * MINUTE,
        max_total_claims=6,
        total_value_nim=900,
    ),
    MockSpot(
        3,
        "St James's Park Wander",
        "Take a short wander through St James's Park and enjoy a small reward.",
        51.5025,
        -0.1349,
        "London",
        "demo-st-jamess-wander",
        -(2 * HOUR),
        6 * HOUR,
        radius=500,
        max_claims_per_user=2,
        max_total_claims=10,
        total_value_nim=1000,
    ),
    MockSpot(
        4,
        "Westminster Bridge View",
        "Find the bridge view, then enter the code shared on today's event card.",
        51.5009,
        -0.1221,
        "London",
        "demo-westminster-code",
        -(45 * MINUTE),
        6 * HOUR,
        radius=220,
        max_total_claims=5,
        total_value_nim=750,
        use_password=True,
    ),
    MockSpot(
        1,
        "South Bank Street Art",
        "Spend twenty minutes exploring the murals and performers along the South Bank.",
        51.5055,
        -0.1160,
        "London",
        "demo-south-bank-art",
        -(3 * HOUR),
        10 * HOUR,
        radius=450,
        claim_duration=20 * MINUTE,
        max_total_claims=5,
        total_value_nim=1000,
    ),
    MockSpot(
        2,
        "Borough Market Taster",
        "Drop into Borough Market and claim a reward for trying somewhere new.",
        51.5055,
        -0.0910,
        "London",
        "demo-borough-taster",
        -(4 * HOUR),
        12 * HOUR,
        radius=160,
        max_total_claims=12,
        total_value_nim=1200,
    ),
    MockSpot(
        3,
        "Tate Modern Discovery",
        "Visit Tate Modern and discover one artwork you have never seen before.",
        51.5076,
        -0.0994,
        "London",
        "demo-tate-discovery",
        -(6 * HOUR),
        DAY,
        radius=240,
        max_total_claims=10,
        total_value_nim=1500,
    ),
    MockSpot(
        4,
        "St Paul's Steps Reward",
        "Reach the cathedral steps and take in the view before collecting your reward.",
        51.5138,
        -0.0984,
        "London",
        "demo-st-pauls-steps",
        -(8 * HOUR),
        2 * DAY,
        radius=170,
        max_total_claims=7,
        total_value_nim=1050,
    ),
    MockSpot(
        1,
        "Barbican Hidden Corners",
        "Explore the Barbican's walkways and find a corner you have not noticed before.",
        51.5202,
        -0.0950,
        "London",
        "demo-barbican-corners",
        -(12 * HOUR),
        3 * DAY,
        radius=360,
        max_total_claims=9,
        total_value_nim=1350,
    ),
    MockSpot(
        2,
        "British Museum Explorer",
        "Step inside the British Museum and choose one gallery to explore.",
        51.5194,
        -0.1270,
        "London",
        "demo-british-museum",
        -DAY,
        5 * DAY,
        radius=260,
        max_total_claims=15,
        total_value_nim=1500,
    ),
    MockSpot(
        3,
        "Covent Garden Performer",
        "Watch a Covent Garden street performance and reward your curiosity.",
        51.5117,
        -0.1230,
        "London",
        "demo-covent-performer",
        -(2 * HOUR),
        DAY,
        radius=190,
        max_claims_per_user=0,
        max_total_claims=8,
        total_value_nim=800,
    ),
    MockSpot(
        4,
        "A Book from Cecil Court",
        "Ask a participating bookseller for today's code after browsing the shelves.",
        51.5100,
        -0.1290,
        "London",
        "demo-cecil-court-code",
        -(5 * HOUR),
        2 * DAY,
        radius=90,
        max_total_claims=6,
        total_value_nim=900,
        use_password=True,
    ),
    MockSpot(
        1,
        "Serpentine Slow Walk",
        "Stay beside the Serpentine for fifteen minutes and enjoy a slower route.",
        51.5052,
        -0.1640,
        "London",
        "demo-serpentine-walk",
        -HOUR,
        8 * HOUR,
        radius=500,
        claim_duration=15 * MINUTE,
        max_total_claims=6,
        total_value_nim=1200,
    ),
    MockSpot(
        2,
        "Golden Hour Primrose Hill",
        "Remain on Primrose Hill for twenty minutes and watch the skyline change.",
        51.5390,
        -0.1607,
        "London",
        "demo-primrose-golden-hour",
        -(30 * MINUTE),
        6 * HOUR,
        radius=420,
        claim_duration=20 * MINUTE,
        max_total_claims=5,
        total_value_nim=1000,
    ),
    MockSpot(
        3,
        "Greenwich Meridian Pause",
        "Stand near the Prime Meridian and pause for a moment between east and west.",
        51.4769,
        0.0005,
        "London",
        "demo-greenwich-meridian",
        -DAY,
        7 * DAY,
        radius=240,
        max_total_claims=10,
        total_value_nim=1000,
    ),
    MockSpot(
        4,
        "Camden Market Secret Code",
        "Find the featured stall and ask for the NimHunt code before entering it.",
        51.5416,
        -0.1430,
        "London",
        "demo-camden-secret-code",
        -(2 * HOUR),
        2 * DAY,
        radius=180,
        max_total_claims=8,
        total_value_nim=1200,
        use_password=True,
    ),
    MockSpot(
        1,
        "Leadenhall Lucky Draw",
        "Enter from Leadenhall Market; two visitors will each win 1,500 NIM.",
        51.5128,
        -0.0836,
        "London",
        "demo-leadenhall-draw",
        -(6 * HOUR),
        3 * DAY,
        radius=260,
        max_total_claims=60,
        total_value_nim=3000,
        is_prizedraw=True,
        prize_count=2,
    ),
    MockSpot(
        2,
        "Kew Gardens Prize Trail",
        "Enter while visiting Kew; three explorers will each win 2,000 NIM.",
        51.4787,
        -0.2956,
        "London",
        "demo-kew-prize-trail",
        -DAY,
        7 * DAY,
        radius=900,
        max_total_claims=100,
        total_value_nim=6000,
        is_prizedraw=True,
        prize_count=3,
    ),
    MockSpot(
        3,
        "Alexandra Palace Sunset",
        "Return this evening for a reward while watching sunset over London.",
        51.5941,
        -0.1298,
        "London",
        "demo-ally-pally-sunset",
        2 * HOUR,
        2 * DAY,
        radius=600,
        max_total_claims=10,
        total_value_nim=1500,
    ),
    MockSpot(
        4,
        "Richmond Park Deer Watch",
        "Tomorrow's challenge: remain in the marked area for thirty minutes and observe quietly.",
        51.4421,
        -0.2731,
        "London",
        "demo-richmond-deer-watch",
        DAY,
        5 * DAY,
        radius=1000,
        claim_duration=30 * MINUTE,
        max_total_claims=8,
        total_value_nim=1600,
    ),

    # The remaining five demonstrate that NimHunt can support trails and events
    # beyond London without making the initial presentation map too dispersed.
    MockSpot(
        1,
        "Oxford Literary Landmark",
        "Visit the Radcliffe Camera and celebrate Oxford's literary history.",
        51.7535,
        -1.2540,
        "Oxford",
        "demo-oxford-literary",
        -(3 * HOUR),
        4 * DAY,
        radius=260,
        max_total_claims=10,
        total_value_nim=1000,
    ),
    MockSpot(
        2,
        "Brighton Pier Adventure",
        "Enter from the pier; two seaside visitors will each win 1,250 NIM.",
        50.8158,
        -0.1367,
        "Brighton",
        "demo-brighton-pier-draw",
        4 * HOUR,
        2 * DAY,
        radius=350,
        max_total_claims=50,
        total_value_nim=2500,
        is_prizedraw=True,
        prize_count=2,
    ),
    MockSpot(
        3,
        "Cambridge Bridge Puzzle",
        "Solve the clue by the Mathematical Bridge and enter the answer as your code.",
        52.2030,
        0.1147,
        "Cambridge",
        "demo-cambridge-puzzle",
        -DAY,
        7 * DAY,
        radius=150,
        max_total_claims=7,
        total_value_nim=1050,
        use_password=True,
    ),
    MockSpot(
        4,
        "Bath Crescent Stroll",
        "A future weekend reward for completing a gentle Royal Crescent stroll.",
        51.3869,
        -2.3658,
        "Bath",
        "demo-bath-crescent",
        2 * DAY,
        4 * DAY,
        radius=420,
        max_total_claims=12,
        total_value_nim=1200,
    ),
    MockSpot(
        1,
        "York Minster Bell Hunt",
        "Enter near York Minster; three visitors will each win 1,500 NIM.",
        53.9623,
        -1.0819,
        "York",
        "demo-york-minster-draw",
        6 * HOUR,
        3 * DAY,
        radius=300,
        max_total_claims=75,
        total_value_nim=4500,
        is_prizedraw=True,
        prize_count=3,
    ),
)


def _device_hash_for_user(user_id: int) -> str:
    """Return a stable 64-character fake device hash for a mock USER."""
    return f"{int(user_id):064x}"[-64:]


def _refuse_public_database_reset() -> None:
    """Refuse to delete a database already marked for a public deployment."""
    db_path = Path(schema.DB_PATH)
    if not db_path.exists():
        return

    try:
        connection = sqlite3.connect(db_path)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (schema.APP_METADATA_TABLE_NAME,),
            ).fetchone()
            if table is None:
                return
            rows = dict(
                connection.execute(
                    f"SELECT {schema.APP_METADATA_KEY}, {schema.APP_METADATA_VALUE} "
                    f"FROM {schema.APP_METADATA_TABLE_NAME} "
                    f"WHERE {schema.APP_METADATA_KEY} IN (?, ?, ?)",
                    (
                        schema.METADATA_NIMIQ_NETWORK,
                        schema.METADATA_NIMIQ_NETWORK_ID,
                        schema.METADATA_DEPLOYMENT_MODE,
                    ),
                ).fetchall()
            )
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(
            "Refusing to reset a database whose deployment metadata could not be read"
        ) from exc

    required_keys = {
        schema.METADATA_NIMIQ_NETWORK,
        schema.METADATA_NIMIQ_NETWORK_ID,
        schema.METADATA_DEPLOYMENT_MODE,
    }
    if set(rows) != required_keys:
        raise RuntimeError(
            "Refusing to reset a database whose deployment metadata is incomplete"
        )

    stored_mode = str(rows[schema.METADATA_DEPLOYMENT_MODE]).strip()
    if stored_mode != "development":
        if stored_mode in {"public-testnet", "production"}:
            raise RuntimeError(
                f"Refusing to reset a database bound to public deployment mode {stored_mode}"
            )
        raise RuntimeError(
            "Refusing to reset a database with an unknown deployment-mode marker"
        )


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
    starts_at = (
        None
        if spot.starts_offset_seconds is None
        else int(now + spot.starts_offset_seconds)
    )
    create = db_access.create_prizedraw if spot.is_prizedraw else db_access.create_spot
    kwargs = {
        "created_by": spot.user_id,
        "lat": spot.lat,
        "long": spot.long,
        "radius": spot.radius,
        "claim_duration": spot.claim_duration,
        "max_claims_per_user": spot.max_claims_per_user,
        "max_total_claims": spot.max_total_claims,
        "total_value": spot.total_value,
        "starts_at": starts_at,
        "ends_at": spot.active_for_seconds,
        "title": spot.title,
        "desc": spot.desc,
        "link": spot.link,
        "city": spot.city,
        "country": TEST_COUNTRY,
    }
    if spot.is_prizedraw:
        kwargs["prize_count"] = spot.prize_count
    else:
        kwargs["use_password"] = spot.use_password
    return int(await create(db, **kwargs))


async def _create_title_only_draft(db, *, user_id: int, title: str) -> int:
    """Create the first-step Create Spot draft: only owner + title."""
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


async def _create_confirmed_creation_fee_for_spot(
    db,
    *,
    user_id: int,
    spot_id: int,
) -> int | None:
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise RuntimeError(f"Mock Spot {spot_id} disappeared")
    amount = int(spot.get(schema.SPOT_CREATION_FEE) or 0)
    if amount <= 0:
        return None

    trans_id = await db_access.create_spot_creation_fee_transaction(
        db,
        user_id=int(user_id),
        spot_id=int(spot_id),
        amount=amount,
        from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
        to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
        tx_hash="cfee" + f"{spot_id:060x}"[-60:],
    )
    await db_access.set_transaction_status_to_confirmed(
        db,
        trans_id=int(trans_id),
        block_number=1_234_568,
    )
    return int(trans_id)


async def _fund_and_publish_mock_spot(
    db,
    *,
    spot: MockSpot,
    spot_id: int,
) -> int:
    stored = await db_access.get_spot(db, spot_id=int(spot_id))
    if stored is None:
        raise RuntimeError(f"Mock Spot {spot_id} disappeared before funding")

    deposit_id = await _create_confirmed_deposit_for_spot(
        db,
        user_id=spot.user_id,
        spot_id=spot_id,
        amount=db_access.spot_required_deposit_amount(stored),
    )
    await _create_confirmed_creation_fee_for_spot(
        db,
        user_id=spot.user_id,
        spot_id=spot_id,
    )
    # Use the real publish helper so coded Spots receive their claim codes and
    # every presentation row follows the same completeness/funding checks as
    # Spots created through the UI.
    await db_access.publish_spot(db, spot_id=spot_id)
    return deposit_id


async def seed_mock_data() -> dict[str, Any]:
    """Recreate the database, seed the dynamic presentation dataset, and summarise it."""
    if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        raise RuntimeError("Refusing to reset or seed mock data in a public deployment mode")

    _refuse_public_database_reset()
    _remove_existing_database_files()
    await init_db()
    now = int(time.time())

    test_user_id = int(const.TEST_USER_ID)
    users = [
        MockUser(test_user_id, "Desktop Test User"),
        MockUser(1, "City Trails"),
        MockUser(2, "Museum Friends"),
        MockUser(3, "Local Adventures"),
        MockUser(4, "Weekend Wanderer"),
    ]

    draft_unpaid = MockSpot(
        user_id=test_user_id,
        title="Unpaid Presentation Draft",
        desc="Draft test Spot with no deposit transaction. It should not be publishable yet.",
        lat=51.5007,
        long=-0.1246,
        city="London",
        link="mock-unpaid-for-spot",
        starts_offset_seconds=DAY,
        active_for_seconds=7 * DAY,
        radius=200,
        max_total_claims=1,
        total_value_nim=const.MIN_SPOT_TOTAL_VALUE_NIM,
    )
    draft_paid = MockSpot(
        user_id=test_user_id,
        title="Funded Presentation Draft",
        desc="Draft test Spot with a confirmed deposit. It should be ready to publish.",
        lat=51.5155,
        long=-0.1410,
        city="London",
        link="mock-paid-for-spot",
        starts_offset_seconds=DAY,
        active_for_seconds=7 * DAY,
        radius=200,
        max_total_claims=1,
        total_value_nim=const.MIN_SPOT_TOTAL_VALUE_NIM,
    )

    async with get_db() as db:
        async with db_access.transaction(db):
            for user in users:
                await _insert_mock_user(db, user, now=now)

            published_spot_ids: list[int] = []
            presentation_deposit_ids: list[int] = []
            for spot in PRESENTATION_SPOTS:
                spot_id = await _create_mock_spot(db, spot, now=now)
                presentation_deposit_ids.append(
                    await _fund_and_publish_mock_spot(
                        db,
                        spot=spot,
                        spot_id=spot_id,
                    )
                )
                published_spot_ids.append(spot_id)

            title_only_draft_id = await _create_title_only_draft(
                db,
                user_id=test_user_id,
                title="Title Only Draft",
            )

            unpaid_spot_id = await _create_mock_spot(db, draft_unpaid, now=now)
            paid_spot_id = await _create_mock_spot(db, draft_paid, now=now)
            paid_spot = await db_access.get_spot(db, spot_id=paid_spot_id)
            if paid_spot is None:
                raise RuntimeError("Funded presentation draft disappeared")
            trans_id = await _create_confirmed_deposit_for_spot(
                db,
                user_id=test_user_id,
                spot_id=paid_spot_id,
                amount=db_access.spot_required_deposit_amount(paid_spot),
            )
            await _create_confirmed_creation_fee_for_spot(
                db,
                user_id=test_user_id,
                spot_id=paid_spot_id,
            )

    return {
        "ok": True,
        "users": [user.user_id for user in users],
        "published_spot_count": len(published_spot_ids),
        "published_spot_ids": published_spot_ids,
        "presentation_deposit_ids": presentation_deposit_ids,
        "title_only_draft_id": title_only_draft_id,
        "draft_unpaid_spot_id": unpaid_spot_id,
        "draft_paid_spot_id": paid_spot_id,
        "confirmed_deposit_transaction_id": trans_id,
        "now": now,
    }


async def main() -> None:
    summary = await seed_mock_data()
    print("Mock NimHunt presentation dataset created.")
    print(f"Users: {summary['users']}")
    print(f"Published Spots: {summary['published_spot_count']}")
    print(f"Title-only draft Spot id: {summary['title_only_draft_id']}")
    print(f"Unpaid draft Spot id: {summary['draft_unpaid_spot_id']}")
    print(f"Funded draft Spot id: {summary['draft_paid_spot_id']}")
    print(
        "Confirmed draft deposit transaction id: "
        f"{summary['confirmed_deposit_transaction_id']}"
    )
    print("Restart FastAPI if it was already running so cache.py reloads this data.")


if __name__ == "__main__":
    asyncio.run(main())
