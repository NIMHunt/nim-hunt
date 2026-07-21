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
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return updated


# ---------------------------------------------------------------------------
# Put claim-code attempts into the normal fresh schema.
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
    "# Claim-Code\n# Transaction",
    "# Claim-Code\n# Claim-Code-Attempt\n# Transaction",
    label="database table list",
)

attempt_schema = r'''

# CLAIM_CODE_ATTEMPT #
# A CLAIM_CODE_ATTEMPT records the code a pending claim is trying to use.
#
# The relationship is intentionally non-exclusive on claim_code_id: several
# pending duration claims may race using the same code. A claim can reference
# only one code, and CLAIM_CODE.used_by is set only by the successful winner.
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


# Finds all pending contenders for a particular code.
# This is deliberately not unique: sharing a code is permitted until success.
CLAIM_CODE_ATTEMPT_INDEX_CODE = "idx_claim_code_attempt_code"
CLAIM_CODE_ATTEMPT_INDEX_CODE_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_CODE_ATTEMPT_INDEX_CODE}
ON {CLAIM_CODE_ATTEMPT_TABLE_NAME}({CLAIM_CODE_ATTEMPT_CODE_ID});
"""


# A claim and its candidate code must belong to the same Spot.
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


# Only a pending claim may retain a candidate-code association.
CLAIM_CODE_ATTEMPT_TRIGGER_PENDING = "trg_claim_code_attempt_pending"
CLAIM_CODE_ATTEMPT_TRIGGER_PENDING_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_CODE_ATTEMPT_TRIGGER_PENDING}
BEFORE INSERT ON {CLAIM_CODE_ATTEMPT_TABLE_NAME}
FOR EACH ROW
WHEN (
    SELECT c.{CLAIM_STATUS}
    FROM {CLAIM_TABLE_NAME} c
    WHERE c.{CLAIM_ID} = NEW.{CLAIM_CODE_ATTEMPT_CLAIM_ID}
) IS NOT {CLAIM_STATUS_PENDING}
BEGIN
    SELECT RAISE(ABORT, 'claim_code_attempt requires a pending claim');
END;
"""


# A terminal claim no longer needs a pending candidate-code association.
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
    "\n\n\n# TRANSACTION #",
    attempt_schema + "\n\n\n# TRANSACTION #",
    label="claim-code-attempt schema insertion",
)

database = replace_once(
    database,
    "        await db.executescript(CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE_QUERY)\n\n        await db.executescript(CREATE_TRANS_TABLE)",
    "        await db.executescript(CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE_QUERY)\n\n"
    "        await db.executescript(CREATE_CLAIM_CODE_ATTEMPT_TABLE)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_INDEX_CODE_QUERY)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_MATCH_SPOT_QUERY)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_PENDING_QUERY)\n"
    "        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_CLEANUP_QUERY)\n\n"
    "        await db.executescript(CREATE_TRANS_TABLE)",
    label="claim-code-attempt schema initialisation",
)
write("database.py", database)


# ---------------------------------------------------------------------------
# Remove runtime table creation and compatibility conversion.
# ---------------------------------------------------------------------------

policy = read("claim_code_policy.py")
policy = regex_replace_once(
    policy,
    r"ClaimCreator = Callable\[\.\.\., Awaitable\[RowDict\]\].*?_INSTALLED = False\n",
    "ClaimPromoter = Callable[..., Awaitable[RowDict | None]]\n\n"
    "_ORIGINAL_PROMOTE_CLAIM: ClaimPromoter = (\n"
    "    db_access.promote_pending_claim_to_success_if_capacity_available\n"
    ")\n"
    "_INSTALLED = False\n",
    label="policy constants and original functions",
)
policy = regex_replace_once(
    policy,
    r"\nasync def _ensure_attempt_table\(db\) -> None:.*?(?=\nasync def _create_attempt)",
    "\n",
    label="runtime schema and conversion removal",
)
policy = policy.replace("_ATTEMPT_TABLE", "schema.CLAIM_CODE_ATTEMPT_TABLE_NAME")
policy = policy.replace("_ATTEMPT_CLAIM_ID", "schema.CLAIM_CODE_ATTEMPT_CLAIM_ID")
policy = policy.replace("_ATTEMPT_CODE_ID", "schema.CLAIM_CODE_ATTEMPT_CODE_ID")
policy = policy.replace("_ATTEMPT_CREATED_AT", "schema.CLAIM_CODE_ATTEMPT_CREATED_AT")
policy = policy.replace("    await _ensure_attempt_table(db)\n\n", "")

for forbidden in (
    "_ensure_attempt_table",
    "CREATE TABLE",
    "INSERT OR IGNORE INTO",
    "ALTER TABLE",
    "_ATTEMPT_",
):
    if forbidden in policy:
        raise RuntimeError(f"claim_code_policy.py still contains forbidden migration text: {forbidden}")
write("claim_code_policy.py", policy)


# ---------------------------------------------------------------------------
# Replace compatibility tests with fresh-schema assertions.
# ---------------------------------------------------------------------------

tests = read("tests/test_claim_code_success_policy.py")
tests = tests.replace(
    '"SELECT COUNT(*) AS n FROM claim_code_attempt;"',
    'f"SELECT COUNT(*) AS n FROM {schema.CLAIM_CODE_ATTEMPT_TABLE_NAME};"',
)
tests = regex_replace_once(
    tests,
    r"\n    async def test_old_pending_claim_is_converted_and_its_code_released\(self\):.*?(?=\n\nclass PolicyWiringSourceTest)",
    "",
    label="old compatibility conversion test removal",
)

fresh_schema_test = '''

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
'''
tests = replace_once(
    tests,
    "\n\nclass PolicyWiringSourceTest",
    fresh_schema_test + "\n\nclass PolicyWiringSourceTest",
    label="fresh schema test insertion",
)

source_guard_test = '''

    def test_policy_contains_no_runtime_schema_or_compatibility_migration(self):
        root = Path(__file__).resolve().parents[1]
        policy_source = (root / "claim_code_policy.py").read_text(encoding="utf-8")
        database_source = (root / "database.py").read_text(encoding="utf-8")

        self.assertNotIn("ALTER TABLE", policy_source.upper())
        self.assertNotIn("CREATE TABLE", policy_source.upper())
        self.assertNotIn("INSERT OR IGNORE INTO", policy_source.upper())
        self.assertIn("CREATE_CLAIM_CODE_ATTEMPT_TABLE", database_source)
        self.assertIn("SCHEMA_VERSION = 3", database_source)
'''
tests = replace_once(
    tests,
    "\n\nif __name__ == \"__main__\":",
    source_guard_test + "\n\nif __name__ == \"__main__\":",
    label="no migration source test insertion",
)
write("tests/test_claim_code_success_policy.py", tests)
