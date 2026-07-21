from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return content.replace(old, new, 1)


def regex_replace_once(content: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    return updated


# ---------------------------------------------------------------------------
# Database schema: add a durable, non-exclusive pending code association.
# ---------------------------------------------------------------------------

database = read("database.py")
database = replace_once(
    database,
    "SCHEMA_VERSION = 2",
    "SCHEMA_VERSION = 3",
    label="schema version",
)

database = replace_once(
    database,
    "if has_app_tables and current_version != SCHEMA_VERSION:\n",
    "# Schema v3 is an additive migration from the deployed v2 database.\n"
    "    # Older schemas still require deliberate migration or recreation.\n"
    "    if has_app_tables and current_version not in {2, SCHEMA_VERSION}:\n",
    label="compatible schema versions",
)

attempt_schema = '''

# A CLAIM_CODE_ATTEMPT records which code a pending claim is trying to use.
# Several pending duration claims may reference the same code. The CLAIM_CODE
# itself is only marked used_by when one of those claims becomes successful.
CLAIM_CODE_ATTEMPT_TABLE_NAME = "claim_code_attempt"
CLAIM_CODE_ATTEMPT_CLAIM_ID = "claim_id"
CLAIM_CODE_ATTEMPT_CODE_ID = "claim_code_id"
CLAIM_CODE_ATTEMPT_CREATED_AT = "created_at"

CREATE_CLAIM_CODE_ATTEMPT_TABLE = f"""
CREATE TABLE IF NOT EXISTS {CLAIM_CODE_ATTEMPT_TABLE_NAME} (
    {CLAIM_CODE_ATTEMPT_CLAIM_ID} INTEGER PRIMARY KEY,
    {CLAIM_CODE_ATTEMPT_CODE_ID} INTEGER NOT NULL,
    {CLAIM_CODE_ATTEMPT_CREATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    CHECK ({CLAIM_CODE_ATTEMPT_CREATED_AT} > 0),

    FOREIGN KEY ({CLAIM_CODE_ATTEMPT_CLAIM_ID})
        REFERENCES {CLAIM_TABLE_NAME}({CLAIM_ID})
        ON DELETE CASCADE,

    FOREIGN KEY ({CLAIM_CODE_ATTEMPT_CODE_ID})
        REFERENCES {CLAIM_CODE_TABLE_NAME}({CLAIM_CODE_ID})
        ON DELETE CASCADE
);
"""

# Deliberately non-unique: multiple pending claims may race using the same code.
CLAIM_CODE_ATTEMPT_INDEX_CODE = "idx_claim_code_attempt_code"
CLAIM_CODE_ATTEMPT_INDEX_CODE_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_CODE_ATTEMPT_INDEX_CODE}
ON {CLAIM_CODE_ATTEMPT_TABLE_NAME}({CLAIM_CODE_ATTEMPT_CODE_ID});
"""

# A pending code association must join a claim and code belonging to one Spot.
CLAIM_CODE_ATTEMPT_TRIGGER_MATCH_SPOT = "trg_claim_code_attempt_match_spot"
CLAIM_CODE_ATTEMPT_TRIGGER_MATCH_SPOT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_CODE_ATTEMPT_TRIGGER_MATCH_SPOT}
BEFORE INSERT ON {CLAIM_CODE_ATTEMPT_TABLE_NAME}
FOR EACH ROW
WHEN (
    SELECT c.{CLAIM_SPOT_ID}
    FROM {CLAIM_TABLE_NAME} c
    WHERE c.{CLAIM_ID} = NEW.{CLAIM_CODE_ATTEMPT_CLAIM_ID}
) IS NOT (
    SELECT cc.{CLAIM_CODE_SPOT_ID}
    FROM {CLAIM_CODE_TABLE_NAME} cc
    WHERE cc.{CLAIM_CODE_ID} = NEW.{CLAIM_CODE_ATTEMPT_CODE_ID}
)
BEGIN
    SELECT RAISE(ABORT, 'claim_code_attempt must join a claim and code from the same spot');
END;
"""

# Once a claim reaches a terminal state, its pending association is no longer
# needed. A successful claim has already consumed the code; a failed claim leaves
# the code unused for another claimant.
CLAIM_CODE_ATTEMPT_TRIGGER_CLEANUP = "trg_claim_code_attempt_cleanup"
CLAIM_CODE_ATTEMPT_TRIGGER_CLEANUP_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_CODE_ATTEMPT_TRIGGER_CLEANUP}
AFTER UPDATE OF {CLAIM_STATUS} ON {CLAIM_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{CLAIM_STATUS} IN ({CLAIM_STATUS_SUCCESS}, {CLAIM_STATUS_FAILED})
BEGIN
    DELETE FROM {CLAIM_CODE_ATTEMPT_TABLE_NAME}
    WHERE {CLAIM_CODE_ATTEMPT_CLAIM_ID} = NEW.{CLAIM_ID};
END;
"""
'''

database = replace_once(
    database,
    "\n\n# Finds codes for a particular SPOT.\n",
    attempt_schema + "\n\n# Finds codes for a particular SPOT.\n",
    label="claim code attempt schema insertion",
)

database = replace_once(
    database,
    "        await db.executescript(CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE_QUERY)\n\n        await db.executescript(CREATE_TRANS_TABLE)\n",
    "        await db.executescript(CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE_QUERY)\n"
    "        await db.executescript(CREATE_CLAIM_CODE_ATTEMPT_TABLE)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_INDEX_CODE_QUERY)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_MATCH_SPOT_QUERY)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_CLEANUP_QUERY)\n\n"
    "        await db.executescript(CREATE_TRANS_TABLE)\n",
    label="claim code attempt initialisation",
)
write("database.py", database)


# ---------------------------------------------------------------------------
# Claim flow: record the candidate code while pending, consume only on success.
# ---------------------------------------------------------------------------

db_access = read("db_access.py")

attempt_helpers = '''

async def create_claim_code_attempt(db, *, claim_id: int, claim_code_id: int) -> None:
    """Associate one pending claim with a candidate code without consuming it."""
    await db.execute(
        f"""
        INSERT INTO {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME} (
            {schema.CLAIM_CODE_ATTEMPT_CLAIM_ID},
            {schema.CLAIM_CODE_ATTEMPT_CODE_ID}
        )
        VALUES (?, ?);
        """,
        (int(claim_id), int(claim_code_id)),
    )


async def get_claim_code_attempt(db, *, claim_id: int) -> RowDict | None:
    """Return the candidate code attached to a pending claim, if any."""
    cur = await db.execute(
        f"""
        SELECT
            a.{schema.CLAIM_CODE_ATTEMPT_CLAIM_ID},
            a.{schema.CLAIM_CODE_ATTEMPT_CODE_ID},
            a.{schema.CLAIM_CODE_ATTEMPT_CREATED_AT},
            cc.{schema.CLAIM_CODE_SPOT_ID},
            cc.{schema.CLAIM_CODE_CODE},
            cc.{schema.CLAIM_CODE_USED_BY}
        FROM {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME} a
        JOIN {schema.CLAIM_CODE_TABLE_NAME} cc
            ON cc.{schema.CLAIM_CODE_ID} = a.{schema.CLAIM_CODE_ATTEMPT_CODE_ID}
        WHERE a.{schema.CLAIM_CODE_ATTEMPT_CLAIM_ID} = ?;
        """,
        (int(claim_id),),
    )
    return _row_to_dict(await cur.fetchone())


async def release_claim_code_from_claim(db, *, claim_id: int) -> None:
    """Undo a just-consumed code when that claim cannot ultimately succeed."""
    await db.execute(
        f"""
        UPDATE {schema.CLAIM_CODE_TABLE_NAME}
        SET {schema.CLAIM_CODE_USED_BY} = NULL
        WHERE {schema.CLAIM_CODE_USED_BY} = ?;
        """,
        (int(claim_id),),
    )
'''

db_access = replace_once(
    db_access,
    "    return int(cur.lastrowid)\n\n\nasync def modify_claim_accuracy",
    "    return int(cur.lastrowid)" + attempt_helpers + "\n\nasync def modify_claim_accuracy",
    label="claim code attempt helpers",
)

new_promotion = '''async def promote_pending_claim_to_success_if_capacity_available(db, *, claim_id: int) -> RowDict | None:
    """Promote a pending claim only if both its code and capacity are available.

    Pending duration claims do not reserve a one-time code. Several people may
    therefore race using the same code. SQLite serialises the final write: the
    first claim that reaches SUCCESS consumes the code, and later contenders fail.
    """
    claim = await get_claim(db, claim_id=int(claim_id))
    if claim is None:
        return None
    if int(claim[schema.CLAIM_STATUS]) != const.CLAIM_STATUS_PENDING:
        return claim

    spot_id = int(claim[schema.CLAIM_SPOT_ID])
    spot = await get_spot(db, spot_id=spot_id)
    if spot is None:
        return claim

    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1
    consumed_code_id: int | None = None
    if use_password:
        attempt = await get_claim_code_attempt(db, claim_id=int(claim_id))
        if attempt is None:
            await set_claim_status_to_failed(db, claim_id=int(claim_id))
            claim_after = await get_claim(db, claim_id=int(claim_id))
            if claim_after is not None:
                claim_after["capacity_promotion"] = {
                    "ok": False,
                    "claim_id": int(claim_id),
                    "spot_id": spot_id,
                    "reason": "claim_code_attempt_missing",
                }
            return claim_after

        code_id = int(attempt[schema.CLAIM_CODE_ATTEMPT_CODE_ID])
        cur = await db.execute(
            f"""
            UPDATE {schema.CLAIM_CODE_TABLE_NAME}
            SET {schema.CLAIM_CODE_USED_BY} = ?
            WHERE {schema.CLAIM_CODE_ID} = ?
              AND (
                    {schema.CLAIM_CODE_USED_BY} IS NULL
                    OR {schema.CLAIM_CODE_USED_BY} = ?
              )
            RETURNING {schema.CLAIM_CODE_ID};
            """,
            (int(claim_id), code_id, int(claim_id)),
        )
        code_row = await cur.fetchone()
        if code_row is None:
            await set_claim_status_to_failed(db, claim_id=int(claim_id))
            claim_after = await get_claim(db, claim_id=int(claim_id))
            if claim_after is not None:
                claim_after["capacity_promotion"] = {
                    "ok": False,
                    "claim_id": int(claim_id),
                    "spot_id": spot_id,
                    "reason": "claim_code_already_used",
                }
            return claim_after
        consumed_code_id = int(code_row[schema.CLAIM_CODE_ID])

    if await is_prizedraw(db, spot_id=spot_id):
        await set_claim_status_to_success(db, claim_id=int(claim_id))
        claim_after = await get_claim(db, claim_id=int(claim_id))
        if claim_after is not None:
            claim_after["capacity_promotion"] = {
                "ok": True,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "prizedraw_entry_promoted",
            }
        return claim_after

    max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if max_total <= 0:
        await set_claim_status_to_success(db, claim_id=int(claim_id))
        claim_after = await get_claim(db, claim_id=int(claim_id))
        if claim_after is not None:
            claim_after["capacity_promotion"] = {
                "ok": True,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "unlimited_standard_spot",
            }
        return claim_after

    cur = await db.execute(
        f"""
        UPDATE {schema.CLAIM_TABLE_NAME}
        SET {schema.CLAIM_STATUS} = ?,
            {schema.CLAIM_UPDATED_AT} = unixepoch()
        WHERE {schema.CLAIM_ID} = ?
          AND {schema.CLAIM_STATUS} = ?
          AND (
                SELECT COUNT(*)
                FROM {schema.CLAIM_TABLE_NAME} existing
                WHERE existing.{schema.CLAIM_SPOT_ID} = ?
                  AND existing.{schema.CLAIM_STATUS} = ?
          ) < (
                SELECT s.{schema.SPOT_MAX_TOTAL_CLAIMS}
                FROM {schema.SPOT_TABLE_NAME} s
                WHERE s.{schema.SPOT_ID} = ?
          )
        RETURNING {schema.CLAIM_ID};
        """,
        (
            const.CLAIM_STATUS_SUCCESS,
            int(claim_id),
            const.CLAIM_STATUS_PENDING,
            spot_id,
            const.CLAIM_STATUS_SUCCESS,
            spot_id,
        ),
    )
    row = await cur.fetchone()
    if row is not None:
        claim_after = await get_claim(db, claim_id=int(claim_id))
        cleanup = await fail_pending_standard_duration_claims_if_capacity_full(db, spot_id=spot_id)
        if claim_after is not None:
            claim_after["capacity_promotion"] = {
                "ok": True,
                "claim_id": int(claim_id),
                "spot_id": spot_id,
                "reason": "promoted_with_capacity",
            }
            claim_after["capacity_cleanup"] = cleanup
        return claim_after

    if consumed_code_id is not None:
        await release_claim_code_from_claim(db, claim_id=int(claim_id))
    await set_claim_status_to_failed(db, claim_id=int(claim_id))
    claim_after = await get_claim(db, claim_id=int(claim_id))
    if claim_after is not None:
        claim_after["capacity_promotion"] = {
            "ok": False,
            "claim_id": int(claim_id),
            "spot_id": spot_id,
            "reason": "capacity_full_claim_failed",
            "max_total_claims": max_total,
        }
        claim_after["capacity_cleanup"] = {
            "ok": True,
            "spot_id": spot_id,
            "failed_count": 1,
            "reason": "capacity_full_current_claim_failed",
            "failed_claim_ids": [int(claim_id)],
            "failed_user_ids": [int(claim_after[schema.CLAIM_RECIPIENT])],
        }
    return claim_after
'''

db_access = regex_replace_once(
    db_access,
    r"async def promote_pending_claim_to_success_if_capacity_available\(.*?\n\nasync def modify_claim_location_score",
    new_promotion + "\n\nasync def modify_claim_location_score",
    label="success promotion replacement",
)

db_access = replace_once(
    db_access,
    "    Immediate standard claims are marked successful at once. Duration claims and\n"
    "    Prizedraw entries begin as pending because they require later completion or\n"
    "    draw settlement. Password claim codes are consumed atomically with the CLAIM.\n",
    "    Immediate standard claims are marked successful at once. Duration claims and\n"
    "    Prizedraw entries begin as pending because they require later completion or\n"
    "    draw settlement. A password code is recorded as a pending candidate here and\n"
    "    is consumed only when the claim is promoted to SUCCESS.\n",
    label="claim attempt docstring",
)

db_access = replace_once(
    db_access,
    "    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1\n"
    "    clean_code = _normalise_claim_code(claim_code, required=use_password)\n"
    "    if use_password:\n"
    "        existing_code = await get_claim_code_by_code(db, spot_id=spot_id, claim_code=clean_code)\n"
    "        if existing_code is None:\n"
    "            raise ValueError(\"That claim code is not valid for this spot.\")\n"
    "        if existing_code.get(schema.CLAIM_CODE_USED_BY) is not None:\n"
    "            raise ValueError(\"This code has already been used.\")\n",
    "    use_password = int(spot.get(schema.SPOT_USE_PASSWORD) or 0) == 1\n"
    "    clean_code = _normalise_claim_code(claim_code, required=use_password)\n"
    "    claim_code_id: int | None = None\n"
    "    if use_password:\n"
    "        existing_code = await get_claim_code_by_code(db, spot_id=spot_id, claim_code=clean_code)\n"
    "        if existing_code is None:\n"
    "            raise ValueError(\"That claim code is not valid for this spot.\")\n"
    "        if existing_code.get(schema.CLAIM_CODE_USED_BY) is not None:\n"
    "            raise ValueError(\"This code has already been used.\")\n"
    "        claim_code_id = int(existing_code[schema.CLAIM_CODE_ID])\n",
    label="candidate code validation",
)

db_access = replace_once(
    db_access,
    "    if use_password and clean_code:\n"
    "        await claim_code_for_claim(\n"
    "            db,\n"
    "            spot_id=spot_id,\n"
    "            claim_code=clean_code,\n"
    "            claim_id=claim_id,\n"
    "        )\n",
    "    if claim_code_id is not None:\n"
    "        await create_claim_code_attempt(\n"
    "            db,\n"
    "            claim_id=int(claim_id),\n"
    "            claim_code_id=int(claim_code_id),\n"
    "        )\n",
    label="defer code consumption",
)

db_access = replace_once(
    db_access,
    "        if isinstance(promotion, dict) and promotion.get(\"ok\") is False:\n"
    "            raise ValueError(\"This spot has run out of rewards.\")\n",
    "        if isinstance(promotion, dict) and promotion.get(\"ok\") is False:\n"
    "            reason = str(promotion.get(\"reason\") or \"\")\n"
    "            if reason == \"claim_code_already_used\":\n"
    "                raise ValueError(\"This code has already been used.\")\n"
    "            if reason == \"claim_code_attempt_missing\":\n"
    "                raise ValueError(\"This claim no longer has a valid code attempt.\")\n"
    "            raise ValueError(\"This spot has run out of rewards.\")\n",
    label="immediate claim failure reason",
)
write("db_access.py", db_access)


# ---------------------------------------------------------------------------
# Map zoom: zoom 11 remains the initial regional focus, while zoom 5 is the
# hard lower bound that prevents world-scale rendering and invalid map bounds.
# ---------------------------------------------------------------------------

constants = read("constants.py")
constants = replace_once(
    constants,
    "MAX_MAP_INIT_SPOTS = 10\nMAX_MAP_ZOOM_OUT = 11\n",
    "MAX_MAP_INIT_SPOTS = 10\nMAX_MAP_ZOOM_OUT = 11\n# Hard manual zoom-out limit. Zoom 5 still permits broad regional browsing,\n"
    "# but prevents world-scale bounds where metre-radius circles become unusable.\n"
    "MIN_MAP_ZOOM = 5\n",
    label="minimum map zoom constant",
)
write("constants.py", constants)

public_html = read("public_html.py")
public_html = replace_once(
    public_html,
    '_ASSET_VERSION = "transaction-integrity-v1-20260721"',
    '_ASSET_VERSION = "success-code-map-zoom-v1-20260721"',
    label="asset version",
)
public_html = replace_once(
    public_html,
    '        "max_map_zoom_out": int(const.MAX_MAP_ZOOM_OUT),\n        "create_spot_url": const.CREATE_SPOT_URL,\n',
    '        "max_map_zoom_out": int(const.MAX_MAP_ZOOM_OUT),\n        "min_map_zoom": int(const.MIN_MAP_ZOOM),\n        "create_spot_url": const.CREATE_SPOT_URL,\n',
    label="map config payload",
)
public_html = replace_once(
    public_html,
    '            "max_map_zoom_out": const.MAX_MAP_ZOOM_OUT,\n            "create_spot_url": const.CREATE_SPOT_URL,\n',
    '            "max_map_zoom_out": const.MAX_MAP_ZOOM_OUT,\n            "min_map_zoom": const.MIN_MAP_ZOOM,\n            "create_spot_url": const.CREATE_SPOT_URL,\n',
    label="find spots map context",
)
write("public_html.py", public_html)

template = read("templates/find_spots.html")
template = replace_once(
    template,
    '    data-max-map-zoom-out="{{ max_map_zoom_out }}"\n',
    '    data-max-map-zoom-out="{{ max_map_zoom_out }}"\n    data-min-map-zoom="{{ min_map_zoom | default(5) }}"\n',
    label="find spots min zoom data",
)
write("templates/find_spots.html", template)

find_spots = read("static/find_spots.js")
find_spots = replace_once(
    find_spots,
    "const MAX_MAP_ZOOM_OUT = Number.parseInt(document.body.dataset.maxMapZoomOut || '11', 10);\n",
    "const MAX_MAP_ZOOM_OUT = Number.parseInt(document.body.dataset.maxMapZoomOut || '11', 10);\n"
    "const MIN_MAP_ZOOM = Number.parseInt(document.body.dataset.minMapZoom || '5', 10);\n",
    label="find spots min zoom setting",
)
find_spots = replace_once(
    find_spots,
    "        boxZoom: !state.hasUserLocation,\n    }).setView(start, state.hasUserLocation ? 14 : 11);\n",
    "        boxZoom: !state.hasUserLocation,\n        minZoom: MIN_MAP_ZOOM,\n    }).setView(start, state.hasUserLocation ? 14 : 11);\n",
    label="find spots Leaflet min zoom",
)
find_spots = replace_once(
    find_spots,
    "            claimText.codeUsedWhenVerificationStarts\n"
    "            || 'This one-time code is used when verification begins and is not restored if the duration check later fails.'\n",
    "            claimText.codeUsedOnSuccess\n"
    "            || 'This code is used only if this claim succeeds. Other people may attempt the same code; the first successful claim uses it.'\n",
    label="claim code race disclosure",
)
write("static/find_spots.js", find_spots)

interface_text = read("static/interface_text.js")
interface_text = replace_once(
    interface_text,
    "            codeUsedWhenVerificationStarts: 'This one-time code is used when verification begins and is not restored if the duration check later fails.',\n",
    "            codeUsedOnSuccess: 'This code is used only if this claim succeeds. Other people may attempt the same code; the first successful claim uses it.',\n",
    label="claim code interface text",
)
write("static/interface_text.js", interface_text)

spot_map = read("static/spot_map.js")
spot_map = replace_once(
    spot_map,
    "const DEFAULT_MAP_ZOOM = 5;\n",
    "const DEFAULT_MAP_ZOOM = 5;\nconst DEFAULT_MAP_MIN_ZOOM = 5;\n",
    label="reusable map min zoom constant",
)
spot_map = replace_once(
    spot_map,
    "    popupBuilder = null,\n    onSpotClick = null,\n}) {\n",
    "    popupBuilder = null,\n    onSpotClick = null,\n    minZoom = DEFAULT_MAP_MIN_ZOOM,\n}) {\n",
    label="reusable map min zoom argument",
)
spot_map = replace_once(
    spot_map,
    "        zoomControl: true,\n        attributionControl: true,\n    });\n",
    "        zoomControl: true,\n        attributionControl: true,\n        minZoom: Number(minZoom),\n    });\n",
    label="reusable Leaflet min zoom",
)
write("static/spot_map.js", spot_map)


# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------

test_file = '''from __future__ import annotations

import tempfile
import time
import unittest

import aiosqlite

import constants as const
import database as schema
import db_access


CLAIM_CODE = "RACECODE01"


class ClaimCodeSuccessRaceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(db, device_id_hash="race-owner")
            self.first_user_id = await db_access.create_user(db, device_id_hash="race-first")
            self.second_user_id = await db_access.create_user(db, device_id_hash="race-second")
            now = int(time.time())
            self.spot_id = await db_access.create_spot(
                db,
                created_by=self.owner_id,
                title="Code Race",
                desc="Two duration claims may race using one code.",
                lat=51.5,
                long=-0.1,
                radius=200,
                claim_duration=600,
                max_claims_per_user=1,
                max_total_claims=2,
                total_value=2 * const.MIN_STANDARD_CLAIM_PAYOUT,
                starts_at=now - 30,
                ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                use_password=True,
                auto_reverse_geocode=False,
                city="London",
                country="United Kingdom",
            )
            await db.execute(
                f"UPDATE {schema.SPOT_TABLE_NAME} SET {schema.SPOT_STATUS} = ? WHERE {schema.SPOT_ID} = ?;",
                (const.SPOT_STATUS_PUBLISHED, self.spot_id),
            )
            self.code_id = await db_access.create_claim_code(
                db,
                spot_id=self.spot_id,
                claim_code=CLAIM_CODE,
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def _start_claim(self, user_id: int) -> dict:
        async with schema.get_db() as db:
            async with db_access.transaction(db):
                return await db_access.create_claim_attempt(
                    db,
                    spot_id=self.spot_id,
                    user_id=user_id,
                    lat=51.5,
                    long=-0.1,
                    location_accuracy_metres=5,
                    claim_code=CLAIM_CODE,
                    payout_address=None,
                )

    async def test_same_code_is_unconsumed_while_pending_and_first_success_wins(self):
        first = await self._start_claim(self.first_user_id)
        second = await self._start_claim(self.second_user_id)

        async with schema.get_db() as db:
            code = await db_access.get_claim_code_by_code(
                db,
                spot_id=self.spot_id,
                claim_code=CLAIM_CODE,
            )
            self.assertIsNone(code[schema.CLAIM_CODE_USED_BY])
            attempts = await db.execute_fetchall(
                f"SELECT * FROM {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME} ORDER BY {schema.CLAIM_CODE_ATTEMPT_CLAIM_ID};"
            )
            self.assertEqual(len(attempts), 2)

        async with schema.get_db() as db:
            async with db_access.transaction(db):
                first_result = await db_access.promote_pending_claim_to_success_if_capacity_available(
                    db,
                    claim_id=int(first[schema.CLAIM_ID]),
                )
        self.assertEqual(first_result[schema.CLAIM_STATUS], const.CLAIM_STATUS_SUCCESS)

        async with schema.get_db() as db:
            async with db_access.transaction(db):
                second_result = await db_access.promote_pending_claim_to_success_if_capacity_available(
                    db,
                    claim_id=int(second[schema.CLAIM_ID]),
                )
        self.assertEqual(second_result[schema.CLAIM_STATUS], const.CLAIM_STATUS_FAILED)
        self.assertEqual(
            second_result["capacity_promotion"]["reason"],
            "claim_code_already_used",
        )

        async with schema.get_db() as db:
            code = await db_access.get_claim_code_by_code(
                db,
                spot_id=self.spot_id,
                claim_code=CLAIM_CODE,
            )
            self.assertEqual(code[schema.CLAIM_CODE_USED_BY], int(first[schema.CLAIM_ID]))
            attempt_count = await db.execute_fetchone(
                f"SELECT COUNT(*) AS n FROM {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME};"
            )
            self.assertEqual(int(attempt_count["n"]), 0)

    async def test_failed_pending_claim_releases_its_attempt_without_using_code(self):
        claim = await self._start_claim(self.first_user_id)
        async with schema.get_db() as db:
            async with db_access.transaction(db):
                await db_access.set_claim_status_to_failed(
                    db,
                    claim_id=int(claim[schema.CLAIM_ID]),
                )

        async with schema.get_db() as db:
            code = await db_access.get_claim_code_by_code(
                db,
                spot_id=self.spot_id,
                claim_code=CLAIM_CODE,
            )
            self.assertIsNone(code[schema.CLAIM_CODE_USED_BY])
            attempt = await db_access.get_claim_code_attempt(
                db,
                claim_id=int(claim[schema.CLAIM_ID]),
            )
            self.assertIsNone(attempt)


class SchemaV2MigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_v2_database_receives_attempt_table_without_reset(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
            old_path = schema.DB_PATH
            schema.DB_PATH = tmp.name
            try:
                await schema.init_db()
                async with aiosqlite.connect(tmp.name) as db:
                    await db.execute(f"DROP TABLE {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME};")
                    await db.execute("PRAGMA user_version = 2;")
                    await db.commit()

                await schema.init_db()

                async with aiosqlite.connect(tmp.name) as db:
                    version_row = await db.execute_fetchall("PRAGMA user_version;")
                    table_row = await db.execute_fetchall(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
                        (schema.CLAIM_CODE_ATTEMPT_TABLE_NAME,),
                    )
                self.assertEqual(int(version_row[0][0]), 3)
                self.assertEqual(table_row[0][0], schema.CLAIM_CODE_ATTEMPT_TABLE_NAME)
            finally:
                schema.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_claim_code_success_race.py", test_file)

regression = read("tests/test_transaction_integrity_regressions.py")
regression = replace_once(
    regression,
    "    def test_combined_code_duration_warning_is_present(self):\n"
    "        source = (Path(__file__).resolve().parents[1] / \"static\" / \"find_spots.js\").read_text()\n"
    "        self.assertIn(\"codeUsedWhenVerificationStarts\", source)\n",
    "    def test_combined_code_duration_race_disclosure_is_present(self):\n"
    "        source = (Path(__file__).resolve().parents[1] / \"static\" / \"find_spots.js\").read_text()\n"
    "        self.assertIn(\"codeUsedOnSuccess\", source)\n"
    "        self.assertNotIn(\"codeUsedWhenVerificationStarts\", source)\n\n"
    "    def test_map_has_a_hard_manual_zoom_out_limit(self):\n"
    "        source = (Path(__file__).resolve().parents[1] / \"static\" / \"find_spots.js\").read_text()\n"
    "        shared_source = (Path(__file__).resolve().parents[1] / \"static\" / \"spot_map.js\").read_text()\n"
    "        template = (Path(__file__).resolve().parents[1] / \"templates\" / \"find_spots.html\").read_text()\n"
    "        self.assertEqual(const.MIN_MAP_ZOOM, 5)\n"
    "        self.assertIn(\"minZoom: MIN_MAP_ZOOM\", source)\n"
    "        self.assertIn(\"minZoom: Number(minZoom)\", shared_source)\n"
    "        self.assertIn(\"data-min-map-zoom\", template)\n",
    label="regression source tests",
)
write("tests/test_transaction_integrity_regressions.py", regression)

print("Applied success-only claim code and map zoom changes.")
