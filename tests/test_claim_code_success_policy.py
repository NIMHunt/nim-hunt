from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import claim_code_policy
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
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash=f"race-owner-{time.time_ns()}",
            )
            self.first_user_id = await db_access.create_user(
                db,
                device_id_hash=f"race-first-{time.time_ns()}",
            )
            self.second_user_id = await db_access.create_user(
                db,
                device_id_hash=f"race-second-{time.time_ns()}",
            )
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
                total_value=max(
                    const.MIN_SPOT_TOTAL_VALUE,
                    2 * const.MIN_STANDARD_CLAIM_PAYOUT,
                ),
                starts_at=now - 30,
                ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                use_password=True,
                auto_reverse_geocode=False,
                city="London",
                country="United Kingdom",
            )
            await db.execute(
                f"""
                UPDATE {schema.SPOT_TABLE_NAME}
                SET {schema.SPOT_STATUS} = ?
                WHERE {schema.SPOT_ID} = ?;
                """,
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
                return await claim_code_policy.create_claim_attempt_success_only(
                    db,
                    spot_id=self.spot_id,
                    user_id=user_id,
                    lat=51.5,
                    long=-0.1,
                    location_accuracy_metres=5,
                    claim_code=CLAIM_CODE,
                    payout_address=None,
                )

    async def _attempt_count(self) -> int:
        async with schema.get_db() as db:
            cur = await db.execute(
                f"SELECT COUNT(*) AS n FROM {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME};"
            )
            row = await cur.fetchone()
            return int(row["n"])

    async def test_same_code_remains_free_while_pending_and_first_success_wins(self):
        first = await self._start_claim(self.first_user_id)
        second = await self._start_claim(self.second_user_id)

        async with schema.get_db() as db:
            code = await db_access.get_claim_code_by_code(
                db,
                spot_id=self.spot_id,
                claim_code=CLAIM_CODE,
            )
            self.assertIsNone(code[schema.CLAIM_CODE_USED_BY])
        self.assertEqual(await self._attempt_count(), 2)

        async with schema.get_db() as db:
            async with db_access.transaction(db):
                first_result = (
                    await claim_code_policy.promote_pending_claim_to_success_if_code_available(
                        db,
                        claim_id=int(first[schema.CLAIM_ID]),
                    )
                )
        self.assertEqual(
            first_result[schema.CLAIM_STATUS],
            const.CLAIM_STATUS_SUCCESS,
        )

        async with schema.get_db() as db:
            async with db_access.transaction(db):
                second_result = (
                    await claim_code_policy.promote_pending_claim_to_success_if_code_available(
                        db,
                        claim_id=int(second[schema.CLAIM_ID]),
                    )
                )
        self.assertEqual(
            second_result[schema.CLAIM_STATUS],
            const.CLAIM_STATUS_FAILED,
        )
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
            self.assertEqual(
                int(code[schema.CLAIM_CODE_USED_BY]),
                int(first[schema.CLAIM_ID]),
            )
        self.assertEqual(await self._attempt_count(), 0)

    async def test_failed_pending_claim_does_not_consume_code(self):
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
        self.assertEqual(await self._attempt_count(), 0)


    async def test_attempt_table_is_created_by_the_fresh_schema(self):
        async with schema.get_db() as db:
            cur = await db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = ?;
                """,
                (schema.CLAIM_CODE_ATTEMPT_TABLE_NAME,),
            )
            table = await cur.fetchone()
            version_cur = await db.execute("PRAGMA user_version;")
            version = await version_cur.fetchone()

        self.assertIsNotNone(table)
        self.assertEqual(int(version[0]), schema.SCHEMA_VERSION)
        self.assertEqual(schema.SCHEMA_VERSION, 3)


class PolicyWiringSourceTest(unittest.TestCase):
    def test_runtime_hook_installs_claim_code_policy(self):
        source = (
            Path(__file__).resolve().parents[1] / "funding_flow.py"
        ).read_text(encoding="utf-8")
        self.assertIn("install_claim_code_policy()", source)

    def test_find_and_owner_maps_load_zoom_guard(self):
        root = Path(__file__).resolve().parents[1]
        guard = (root / "static" / "map_zoom_guard.js").read_text(encoding="utf-8")
        find_template = (root / "templates" / "find_spots.html").read_text(
            encoding="utf-8"
        )
        owner_template = (root / "templates" / "my_spots.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("MIN_SPOT_MAP_ZOOM = 5", guard)
        self.assertIn("Map.mergeOptions", guard)
        self.assertIn("map_zoom_guard.js", find_template)
        self.assertIn("map_zoom_guard.js", owner_template)

    def test_claim_card_explains_first_successful_claim_wins(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "static" / "claim_code_policy_ui.js").read_text(
            encoding="utf-8"
        )
        template = (root / "templates" / "find_spots.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("first successful claim uses it", source)
        self.assertIn("claim_code_policy_ui.js", template)


    def test_policy_contains_no_runtime_schema_or_compatibility_migration(self):
        root = Path(__file__).resolve().parents[1]
        policy_source = (root / "claim_code_policy.py").read_text(encoding="utf-8")
        database_source = (root / "database.py").read_text(encoding="utf-8")

        self.assertNotIn("ALTER TABLE", policy_source.upper())
        self.assertNotIn("CREATE TABLE", policy_source.upper())
        self.assertNotIn("INSERT OR IGNORE INTO", policy_source.upper())
        self.assertIn("CREATE_CLAIM_CODE_ATTEMPT_TABLE", database_source)
        self.assertIn("SCHEMA_VERSION = 3", database_source)


if __name__ == "__main__":
    unittest.main()
