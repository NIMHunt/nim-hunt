"""
─────────────────────────────────────────────

database.py

The back-end of the system. Handles the database.

─────────────────────────────────────────────
"""

import os
from contextlib import asynccontextmanager, suppress

import aiosqlite

import constants as const
from constants import (
    CLAIM_STATUS_FAILED,
    CLAIM_STATUS_PENDING,
    CLAIM_STATUS_SUCCESS,
    DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS,
    MAX_PRIZEDRAW_PRIZE_COUNT,
    MAX_SPOT_CLAIM_DURATION_SECONDS,
    MAX_SPOT_ENDS_AFTER_SECONDS,
    MAX_SPOT_MAX_CLAIMS_PER_USER,
    MAX_SPOT_MAX_TOTAL_CLAIMS,
    MAX_SPOT_RADIUS_METRES,
    MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS,
    MIN_PRIZEDRAW_PRIZE_COUNT,
    MIN_SPOT_CLAIM_DURATION_SECONDS,
    MIN_SPOT_ENDS_AFTER_SECONDS,
    MIN_SPOT_MAX_CLAIMS_PER_USER,
    MIN_SPOT_MAX_TOTAL_CLAIMS,
    MIN_SPOT_RADIUS_METRES,
    MIN_SPOT_TOTAL_VALUE,
    REPORT_STATUS_APPROVED,
    REPORT_STATUS_DISMISSED,
    REPORT_STATUS_PENDING,
    SPOT_STATUS_DRAFT,
    SPOT_STATUS_PUBLISHED,
    TRANS_STATUS_CONFIRMED,
    TRANS_STATUS_FAILED,
    TRANS_STATUS_PENDING,
    TRANS_TYPE_CANCEL_SPOT,
    TRANS_TYPE_CLAIM,
    TRANS_TYPE_CREATION_FEE,
    TRANS_TYPE_PLAT_FEE,
    TRANS_TYPE_REMAINDER_REFUND,
    USER_STATUS_ACTIVE,
)
from constants import (
    SPOT_DEPOSIT_KEY_VERSION as DEFAULT_SPOT_DEPOSIT_KEY_VERSION,
)

# Where the SQLite database is stored. Production should point this at a
# persistent volume; local development keeps the convenient repository file.
DB_PATH = os.getenv("NIMHUNT_DB_PATH", "records.db").strip() or "records.db"

# NimHunt is still in development and intentionally uses fresh databases
# instead of carrying schema migrations. Increment this whenever the schema
# changes. Existing non-empty databases with another version are rejected
# with a clear instruction to recreate them.
SCHEMA_VERSION = 3

# Durable chain and deployment identity prevents a TestAlbatross database from
# being silently reinterpreted as MainAlbatross, and prevents a local/mock
# development database from being exposed as a public service. This small
# additive table is compatible with the current schema.
APP_METADATA_TABLE_NAME = "app_metadata"
APP_METADATA_KEY = "key"
APP_METADATA_VALUE = "value"
METADATA_NIMIQ_NETWORK = "nimiq_network"
METADATA_NIMIQ_NETWORK_ID = "nimiq_network_id"
METADATA_DEPLOYMENT_MODE = "deployment_mode"
CREATE_APP_METADATA_TABLE = f"""
CREATE TABLE IF NOT EXISTS {APP_METADATA_TABLE_NAME} (
    {APP_METADATA_KEY} TEXT PRIMARY KEY,
    {APP_METADATA_VALUE} TEXT NOT NULL
);
"""


# --------------------------------------
# Attribute constants for easy access
# Use these variables instead of literal strings in code.
# --------------------------------------

#################
# DATABASE TABLES
#################
# User
# Spot
# Prizedraw
# Claim
# Claim-Code
# Claim-Code-Attempt
# Transaction
# Report
# Views
#################


# USER #
# A USER represents a single device-based user.
#
# Important:
# This does NOT necessarily mean one human being.
# It means one recognised device identifier.
USER_TABLE_NAME = "user"

USER_ID = "id"                                # Unique ID / PK
USER_DEVICE_ID_HASH = "device_id_hash"        # Hashed Nimiq device identifier
USER_DISPLAY_NAME = "display_name"            # Optional public/display name
USER_STATUS = "u_status"                      # Enum-ish. ACTIVE, LIMITED, BANNED
USER_CREATED_AT = "created_at"                # Unix timestamp of creation
USER_LAST_SEEN_AT = "last_seen_at"            # Unix timestamp of last activity

# Fields that ordinary update helpers are allowed to modify.
USER_MOD_FIELDS = {
    USER_DISPLAY_NAME,
    USER_LAST_SEEN_AT,
}


CREATE_USER_TABLE = f"""
CREATE TABLE IF NOT EXISTS {USER_TABLE_NAME} (
    {USER_ID} INTEGER PRIMARY KEY AUTOINCREMENT,

    {USER_DEVICE_ID_HASH} TEXT NOT NULL UNIQUE,

    {USER_DISPLAY_NAME} TEXT,

    {USER_STATUS} INTEGER NOT NULL
        DEFAULT {USER_STATUS_ACTIVE},

    {USER_CREATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    {USER_LAST_SEEN_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    CHECK ({USER_CREATED_AT} > 0),
    CHECK ({USER_LAST_SEEN_AT} > 0)
);
"""


# Useful later when you want to list banned/limited users in an admin page.
USER_INDEX_STATUS = "idx_user_status"
USER_INDEX_STATUS_QUERY = f"""
CREATE INDEX IF NOT EXISTS {USER_INDEX_STATUS}
ON {USER_TABLE_NAME}({USER_STATUS}, {USER_ID});
"""


# Useful later for cleanup/statistics: "which users have been inactive longest?"
USER_INDEX_LAST_SEEN = "idx_user_last_seen"
USER_INDEX_LAST_SEEN_QUERY = f"""
CREATE INDEX IF NOT EXISTS {USER_INDEX_LAST_SEEN}
ON {USER_TABLE_NAME}({USER_LAST_SEEN_AT});
"""



# SPOT #
# A SPOT represents a visible geofaucet location.
#
# A USER creates a SPOT. The SPOT describes:
#   - where the drop is
#   - how close a claimer must be
#   - how long they must remain there
#   - how many claims are allowed
#   - how much total NIM/Luna is attached to the SPOT
#
# Individual claimable portions will later live in a CACHE table.
SPOT_TABLE_NAME = "spot"

SPOT_ID = "id"                                      # Unique ID / PK
SPOT_CREATED_BY = "created_by"                      # FK from USER.id

SPOT_LINK = "link"                                  # Unique shareable link/slug
SPOT_DEPOSIT_ADDRESS = "deposit_address"              # Immutable funding/deposit address
SPOT_DEPOSIT_KEY_INDEX = "deposit_key_index"          # Immutable HD/deposit-key index
SPOT_DEPOSIT_KEY_PATH = "deposit_key_path"            # Immutable derivation path used for the address
SPOT_DEPOSIT_KEY_VERSION = "deposit_key_version"      # Address-derivation/key format version
SPOT_TITLE = "title"                                # Public title
SPOT_DESC = "description"                           # Optional public description

SPOT_LAT = "lat"                                    # Latitude
SPOT_LONG = "long"                                  # Longitude
SPOT_GEOHASH = "geohash"                            # Optional geohash for searching
SPOT_CITY = "city"                                  # Optional city label
SPOT_COUNTRY = "country"                            # Optional country label

SPOT_RADIUS = "radius"                              # Claim radius in metres
SPOT_CLAIM_DURATION = "claim_duration"              # Required stay duration in seconds
SPOT_MAX_CLAIMS_PER_USER = "max_claims_per_user"    # Per-USER claim limit
SPOT_MAX_TOTAL_CLAIMS = "max_total_claims"          # Total claim/entry limit. 0 means unlimited only for Prizedraws.
SPOT_USE_PASSWORD = "use_password"                    # Boolean-ish. 1 means CLAIM_CODE rows are required.

SPOT_TOTAL_VALUE = "total_value"                    # Total value in Luna
SPOT_CREATION_FEE = "creation_fee"                  # Snapshotted one-time creation fee in Luna
SPOT_CREATION_FEE_ADDRESS = "creation_fee_address"  # Snapshotted fee recipient

SPOT_STARTS_AT = "starts_at"                        # Optional Unix timestamp
SPOT_ENDS_AT = "ends_at"                            # Seconds after starts_at when the SPOT ends

SPOT_STATUS = "s_status"                            # Enum-ish. DRAFT, PUBLISHED, etc.

SPOT_CREATED_AT = "created_at"                      # Unix timestamp of creation
SPOT_UPDATED_AT = "updated_at"                      # Unix timestamp of last update
SPOT_CANCELLATION_STARTED_AT = "cancellation_started_at"  # Unix timestamp when cancellation began


# Fields that ordinary update helpers are allowed to modify.
#
# SPOT_DRAFT_MOD_FIELDS can only be changed while the SPOT is still DRAFT.
# SPOT_STATUS is deliberately separate: status changes are allowed after draft
# for lifecycle actions such as publish, complete, cancel, or moderation ban.
SPOT_DRAFT_MOD_FIELDS = {
    SPOT_TITLE,
    SPOT_DESC,
    SPOT_LAT,
    SPOT_LONG,
    SPOT_GEOHASH,
    SPOT_CITY,
    SPOT_COUNTRY,
    SPOT_RADIUS,
    SPOT_CLAIM_DURATION,
    SPOT_MAX_CLAIMS_PER_USER,
    SPOT_MAX_TOTAL_CLAIMS,
    SPOT_USE_PASSWORD,
    SPOT_TOTAL_VALUE,
    SPOT_STARTS_AT,
    SPOT_ENDS_AT,
    SPOT_UPDATED_AT,
}

SPOT_MOD_FIELDS = SPOT_DRAFT_MOD_FIELDS | {
    SPOT_STATUS,
    SPOT_CANCELLATION_STARTED_AT,
}

SPOT_IMMUTABLE_FIELDS = {
    SPOT_ID,
    SPOT_CREATED_BY,
    SPOT_LINK,
    SPOT_DEPOSIT_ADDRESS,
    SPOT_DEPOSIT_KEY_INDEX,
    SPOT_DEPOSIT_KEY_PATH,
    SPOT_DEPOSIT_KEY_VERSION,
    SPOT_CREATION_FEE,
    SPOT_CREATION_FEE_ADDRESS,
    SPOT_CREATED_AT,
}


CREATE_SPOT_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SPOT_TABLE_NAME} (
    {SPOT_ID} INTEGER PRIMARY KEY AUTOINCREMENT,

    {SPOT_CREATED_BY} INTEGER NOT NULL,

    {SPOT_LINK} TEXT NOT NULL UNIQUE,

    {SPOT_DEPOSIT_ADDRESS} TEXT NOT NULL UNIQUE,

    {SPOT_DEPOSIT_KEY_INDEX} INTEGER UNIQUE,

    {SPOT_DEPOSIT_KEY_PATH} TEXT,

    {SPOT_DEPOSIT_KEY_VERSION} INTEGER NOT NULL
        DEFAULT {DEFAULT_SPOT_DEPOSIT_KEY_VERSION},

    {SPOT_TITLE} TEXT NOT NULL,

    {SPOT_DESC} TEXT,

    -- DRAFT spots may not have a location yet. Publishing checks in
    -- db_access.can_publish_spot() require both coordinates to be present.
    {SPOT_LAT} REAL,
    {SPOT_LONG} REAL,

    {SPOT_GEOHASH} TEXT,

    {SPOT_CITY} TEXT,
    {SPOT_COUNTRY} TEXT,

    {SPOT_RADIUS} INTEGER NOT NULL
        DEFAULT {MIN_SPOT_RADIUS_METRES},

    {SPOT_CLAIM_DURATION} INTEGER NOT NULL
        DEFAULT 0,

    {SPOT_MAX_CLAIMS_PER_USER} INTEGER NOT NULL
        DEFAULT 1,

    {SPOT_MAX_TOTAL_CLAIMS} INTEGER NOT NULL
        DEFAULT {MIN_SPOT_MAX_TOTAL_CLAIMS},

    {SPOT_USE_PASSWORD} INTEGER NOT NULL
        DEFAULT 0,

    {SPOT_TOTAL_VALUE} INTEGER NOT NULL
        DEFAULT {MIN_SPOT_TOTAL_VALUE},

    {SPOT_CREATION_FEE} INTEGER NOT NULL
        DEFAULT 0,

    {SPOT_CREATION_FEE_ADDRESS} TEXT NOT NULL,

    {SPOT_STARTS_AT} INTEGER,

    -- Seconds after starts_at when the SPOT ends. This is intentionally
    -- relative, not an absolute unix timestamp.
    {SPOT_ENDS_AT} INTEGER NOT NULL
        DEFAULT {DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS},

    {SPOT_STATUS} INTEGER NOT NULL
        DEFAULT {SPOT_STATUS_DRAFT},

    {SPOT_CREATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    {SPOT_UPDATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    {SPOT_CANCELLATION_STARTED_AT} INTEGER,

    CHECK ({SPOT_LAT} IS NULL OR {SPOT_LAT} BETWEEN -90 AND 90),
    CHECK ({SPOT_LONG} IS NULL OR {SPOT_LONG} BETWEEN -180 AND 180),

    CHECK ({SPOT_RADIUS} BETWEEN {MIN_SPOT_RADIUS_METRES} AND {MAX_SPOT_RADIUS_METRES}),
    CHECK ({SPOT_CLAIM_DURATION} BETWEEN {MIN_SPOT_CLAIM_DURATION_SECONDS} AND {MAX_SPOT_CLAIM_DURATION_SECONDS}),
    CHECK ({SPOT_MAX_CLAIMS_PER_USER} BETWEEN {MIN_SPOT_MAX_CLAIMS_PER_USER} AND {MAX_SPOT_MAX_CLAIMS_PER_USER}),
    CHECK ({SPOT_MAX_TOTAL_CLAIMS} BETWEEN {MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS} AND {MAX_SPOT_MAX_TOTAL_CLAIMS}),
    CHECK ({SPOT_USE_PASSWORD} IN (0, 1)),
    CHECK ({SPOT_TOTAL_VALUE} >= {MIN_SPOT_TOTAL_VALUE}),
    CHECK ({SPOT_CREATION_FEE} >= 0),
    CHECK (TRIM({SPOT_CREATION_FEE_ADDRESS}) != ''),

    CHECK ({SPOT_STARTS_AT} IS NULL OR {SPOT_STARTS_AT} > 0),
    CHECK ({SPOT_ENDS_AT} BETWEEN {MIN_SPOT_ENDS_AFTER_SECONDS} AND {MAX_SPOT_ENDS_AFTER_SECONDS}),

    CHECK ({SPOT_CREATED_AT} > 0),
    CHECK ({SPOT_UPDATED_AT} > 0),
    CHECK ({SPOT_CANCELLATION_STARTED_AT} IS NULL OR {SPOT_CANCELLATION_STARTED_AT} > 0),
    CHECK ({SPOT_DEPOSIT_KEY_INDEX} IS NULL OR {SPOT_DEPOSIT_KEY_INDEX} >= 0),
    CHECK ({SPOT_DEPOSIT_KEY_VERSION} > 0),

    FOREIGN KEY ({SPOT_CREATED_BY})
        REFERENCES {USER_TABLE_NAME}({USER_ID})
        ON DELETE RESTRICT
);
"""

# Finds all SPOTs created by a particular USER.
SPOT_INDEX_CREATED_BY = "idx_spot_created_by"
SPOT_INDEX_CREATED_BY_QUERY = f"""
CREATE INDEX IF NOT EXISTS {SPOT_INDEX_CREATED_BY}
ON {SPOT_TABLE_NAME}({SPOT_CREATED_BY}, {SPOT_CREATED_AT});
"""


# Helps list public/published SPOTs.
SPOT_INDEX_STATUS_CREATED = "idx_spot_status_created"
SPOT_INDEX_STATUS_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {SPOT_INDEX_STATUS_CREATED}
ON {SPOT_TABLE_NAME}({SPOT_STATUS}, {SPOT_CREATED_AT});
"""


# Helps future map/location searches.
SPOT_INDEX_GEOHASH = "idx_spot_geohash"
SPOT_INDEX_GEOHASH_QUERY = f"""
CREATE INDEX IF NOT EXISTS {SPOT_INDEX_GEOHASH}
ON {SPOT_TABLE_NAME}({SPOT_GEOHASH})
WHERE {SPOT_GEOHASH} IS NOT NULL;
"""


# Helps the main map query: published SPOTs filtered by geohash prefix,
# then ordered by start/end timing.
SPOT_INDEX_PUBLIC_GEOHASH_SOON = "idx_spot_public_geohash_soon"
SPOT_INDEX_PUBLIC_GEOHASH_SOON_QUERY = f"""
CREATE INDEX IF NOT EXISTS {SPOT_INDEX_PUBLIC_GEOHASH_SOON}
ON {SPOT_TABLE_NAME}(
    {SPOT_STATUS},
    {SPOT_GEOHASH},
    {SPOT_STARTS_AT},
    {SPOT_ENDS_AT},
    {SPOT_ID}
)
WHERE {SPOT_GEOHASH} IS NOT NULL;
"""


# Fallback location index for city/country browsing.
SPOT_INDEX_PUBLIC_CITY_COUNTRY_SOON = "idx_spot_public_city_country_soon"
SPOT_INDEX_PUBLIC_CITY_COUNTRY_SOON_QUERY = f"""
CREATE INDEX IF NOT EXISTS {SPOT_INDEX_PUBLIC_CITY_COUNTRY_SOON}
ON {SPOT_TABLE_NAME}(
    {SPOT_STATUS},
    {SPOT_COUNTRY},
    {SPOT_CITY},
    {SPOT_STARTS_AT},
    {SPOT_ENDS_AT},
    {SPOT_ID}
);
"""


# General public listing index when no location filter is applied.
SPOT_INDEX_PUBLIC_SOON = "idx_spot_public_soon"
SPOT_INDEX_PUBLIC_SOON_QUERY = f"""
CREATE INDEX IF NOT EXISTS {SPOT_INDEX_PUBLIC_SOON}
ON {SPOT_TABLE_NAME}(
    {SPOT_STATUS},
    {SPOT_STARTS_AT},
    {SPOT_ENDS_AT},
    {SPOT_ID}
);
"""


# Automatically updates SPOT.updated_at when editable SPOT fields change.
SPOT_TRIGGER_STAMP_UPDATED_AT = "trg_spot_stamp_updated_at"
SPOT_TRIGGER_STAMP_UPDATED_AT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {SPOT_TRIGGER_STAMP_UPDATED_AT}
AFTER UPDATE OF
    {SPOT_TITLE},
    {SPOT_DESC},
    {SPOT_LAT},
    {SPOT_LONG},
    {SPOT_GEOHASH},
    {SPOT_CITY},
    {SPOT_COUNTRY},
    {SPOT_RADIUS},
    {SPOT_CLAIM_DURATION},
    {SPOT_MAX_CLAIMS_PER_USER},
    {SPOT_MAX_TOTAL_CLAIMS},
    {SPOT_USE_PASSWORD},
    {SPOT_TOTAL_VALUE},
    {SPOT_STARTS_AT},
    {SPOT_ENDS_AT},
    {SPOT_STATUS}
ON {SPOT_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{SPOT_UPDATED_AT} IS OLD.{SPOT_UPDATED_AT}
BEGIN
    UPDATE {SPOT_TABLE_NAME}
    SET {SPOT_UPDATED_AT} = unixepoch()
    WHERE {SPOT_ID} = NEW.{SPOT_ID};
END;
"""




# PRIZEDRAW #
# A PRIZEDRAW is an optional extension of a SPOT.
#
# If a SPOT has a matching PRIZEDRAW row, the SPOT works as a Prizedraw:
#   - eligible USERS become participants
#   - one or more winners are selected later
#   - the SPOT total_value is split between those winners
PRIZEDRAW_TABLE_NAME = "prizedraw"

PRIZEDRAW_SPOT_ID = "spot_id"                        # PK/FK from SPOT.id
PRIZEDRAW_PRIZE_COUNT = "prize_count"                # Number of winners


# Prize count is editable while the paired SPOT is still a draft.
PRIZEDRAW_MOD_FIELDS = {
    PRIZEDRAW_PRIZE_COUNT,
}


CREATE_PRIZEDRAW_TABLE = f"""
CREATE TABLE IF NOT EXISTS {PRIZEDRAW_TABLE_NAME} (
    {PRIZEDRAW_SPOT_ID} INTEGER PRIMARY KEY,

    {PRIZEDRAW_PRIZE_COUNT} INTEGER NOT NULL
        DEFAULT 1,

    CHECK ({PRIZEDRAW_PRIZE_COUNT} BETWEEN {MIN_PRIZEDRAW_PRIZE_COUNT} AND {MAX_PRIZEDRAW_PRIZE_COUNT}),

    FOREIGN KEY ({PRIZEDRAW_SPOT_ID})
        REFERENCES {SPOT_TABLE_NAME}({SPOT_ID})
        ON DELETE CASCADE
);
"""


# A Prizedraw cannot have more winners than possible participants/claims.
PRIZEDRAW_TRIGGER_PRIZE_COUNT_INSERT = "trg_prizedraw_prize_count_insert"
PRIZEDRAW_TRIGGER_PRIZE_COUNT_INSERT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {PRIZEDRAW_TRIGGER_PRIZE_COUNT_INSERT}
BEFORE INSERT ON {PRIZEDRAW_TABLE_NAME}
FOR EACH ROW
WHEN (
    SELECT s.{SPOT_MAX_TOTAL_CLAIMS}
    FROM {SPOT_TABLE_NAME} s
    WHERE s.{SPOT_ID} = NEW.{PRIZEDRAW_SPOT_ID}
) > 0
AND NEW.{PRIZEDRAW_PRIZE_COUNT} > (
    SELECT s.{SPOT_MAX_TOTAL_CLAIMS}
    FROM {SPOT_TABLE_NAME} s
    WHERE s.{SPOT_ID} = NEW.{PRIZEDRAW_SPOT_ID}
)
BEGIN
    SELECT RAISE(ABORT, 'prizedraw.prize_count cannot exceed spot.max_total_claims unless max_total_claims is 0/unlimited');
END;
"""


PRIZEDRAW_TRIGGER_PRIZE_COUNT_UPDATE = "trg_prizedraw_prize_count_update"
PRIZEDRAW_TRIGGER_PRIZE_COUNT_UPDATE_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {PRIZEDRAW_TRIGGER_PRIZE_COUNT_UPDATE}
BEFORE UPDATE OF {PRIZEDRAW_PRIZE_COUNT}, {PRIZEDRAW_SPOT_ID} ON {PRIZEDRAW_TABLE_NAME}
FOR EACH ROW
WHEN (
    SELECT s.{SPOT_MAX_TOTAL_CLAIMS}
    FROM {SPOT_TABLE_NAME} s
    WHERE s.{SPOT_ID} = NEW.{PRIZEDRAW_SPOT_ID}
) > 0
AND NEW.{PRIZEDRAW_PRIZE_COUNT} > (
    SELECT s.{SPOT_MAX_TOTAL_CLAIMS}
    FROM {SPOT_TABLE_NAME} s
    WHERE s.{SPOT_ID} = NEW.{PRIZEDRAW_SPOT_ID}
)
BEGIN
    SELECT RAISE(ABORT, 'prizedraw.prize_count cannot exceed spot.max_total_claims unless max_total_claims is 0/unlimited');
END;
"""


SPOT_TRIGGER_MAX_TOTAL_CLAIMS_PRIZEDRAW_UPDATE = "trg_spot_max_total_claims_prizedraw_update"
SPOT_TRIGGER_MAX_TOTAL_CLAIMS_PRIZEDRAW_UPDATE_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {SPOT_TRIGGER_MAX_TOTAL_CLAIMS_PRIZEDRAW_UPDATE}
BEFORE UPDATE OF {SPOT_MAX_TOTAL_CLAIMS} ON {SPOT_TABLE_NAME}
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM {PRIZEDRAW_TABLE_NAME} pd
    WHERE pd.{PRIZEDRAW_SPOT_ID} = NEW.{SPOT_ID}
      AND NEW.{SPOT_MAX_TOTAL_CLAIMS} > 0
      AND pd.{PRIZEDRAW_PRIZE_COUNT} > NEW.{SPOT_MAX_TOTAL_CLAIMS}
)
BEGIN
    SELECT RAISE(ABORT, 'spot.max_total_claims cannot be lower than prizedraw.prize_count unless it is 0/unlimited');
END;
"""



# CLAIM #
# A CLAIM records a USER's attempt to claim from a SPOT.
#
# This table stores:
#   - which SPOT was claimed
#   - which USER is receiving the claim
#   - the location reading used for the claim
#   - whether the claim is pending, successful, or failed
CLAIM_TABLE_NAME = "claim"

CLAIM_ID = "id"                              # Unique ID / PK
CLAIM_SPOT_ID = "spot_id"                    # FK from SPOT.id
CLAIM_RECIPIENT = "recipient"                # FK from USER.id
CLAIM_PAYOUT_ADDRESS = "payout_address"        # Nimiq address to receive any eventual payout

CLAIM_LAT = "lat"                            # Latest recorded claim latitude
CLAIM_LONG = "long"                          # Latest recorded claim longitude
CLAIM_ACCURACY = "accuracy"                  # Duration-claim health score: 0 to 1

CLAIM_STATUS = "c_status"                    # Enum-ish. PENDING, SUCCESS, FAILED

CLAIM_CLAIMED_AT = "claimed_at"              # Unix timestamp of claim creation
CLAIM_UPDATED_AT = "updated_at"              # Unix timestamp of last update


# Fields that ordinary update helpers are allowed to modify.
CLAIM_MOD_FIELDS = {
    CLAIM_LAT,
    CLAIM_LONG,
    CLAIM_ACCURACY,
    CLAIM_STATUS,
    CLAIM_UPDATED_AT,
}


CREATE_CLAIM_TABLE = f"""
CREATE TABLE IF NOT EXISTS {CLAIM_TABLE_NAME} (
    {CLAIM_ID} INTEGER PRIMARY KEY AUTOINCREMENT,

    {CLAIM_SPOT_ID} INTEGER NOT NULL,

    {CLAIM_RECIPIENT} INTEGER NOT NULL,

    {CLAIM_PAYOUT_ADDRESS} TEXT,

    {CLAIM_LAT} REAL NOT NULL,
    {CLAIM_LONG} REAL NOT NULL,

    {CLAIM_ACCURACY} REAL NOT NULL
        DEFAULT 1,

    {CLAIM_STATUS} INTEGER NOT NULL
        DEFAULT {CLAIM_STATUS_PENDING},

    {CLAIM_CLAIMED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    {CLAIM_UPDATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    CHECK ({CLAIM_LAT} BETWEEN -90 AND 90),
    CHECK ({CLAIM_LONG} BETWEEN -180 AND 180),

    CHECK ({CLAIM_ACCURACY} >= 0),
    CHECK ({CLAIM_ACCURACY} <= 1),

    CHECK ({CLAIM_CLAIMED_AT} > 0),
    CHECK ({CLAIM_UPDATED_AT} > 0),

    FOREIGN KEY ({CLAIM_SPOT_ID})
        REFERENCES {SPOT_TABLE_NAME}({SPOT_ID})
        ON DELETE CASCADE,

    FOREIGN KEY ({CLAIM_RECIPIENT})
        REFERENCES {USER_TABLE_NAME}({USER_ID})
        ON DELETE RESTRICT
);
"""


# Finds all CLAIMs for a particular SPOT.
CLAIM_INDEX_SPOT = "idx_claim_spot"
CLAIM_INDEX_SPOT_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_INDEX_SPOT}
ON {CLAIM_TABLE_NAME}({CLAIM_SPOT_ID}, {CLAIM_CLAIMED_AT});
"""


# Finds all CLAIMs made by a particular USER.
CLAIM_INDEX_RECIPIENT = "idx_claim_recipient"
CLAIM_INDEX_RECIPIENT_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_INDEX_RECIPIENT}
ON {CLAIM_TABLE_NAME}({CLAIM_RECIPIENT}, {CLAIM_CLAIMED_AT});
"""


# Helps count successful CLAIMs per USER per SPOT.
# Useful for enforcing SPOT.max_claims_per_user.
CLAIM_INDEX_SPOT_RECIPIENT_STATUS = "idx_claim_spot_recipient_status"
CLAIM_INDEX_SPOT_RECIPIENT_STATUS_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_INDEX_SPOT_RECIPIENT_STATUS}
ON {CLAIM_TABLE_NAME}({CLAIM_SPOT_ID}, {CLAIM_RECIPIENT}, {CLAIM_STATUS});
"""


# Helps count successful CLAIMs per SPOT.
# Useful for enforcing SPOT.max_total_claims.
CLAIM_INDEX_SPOT_STATUS = "idx_claim_spot_status"
CLAIM_INDEX_SPOT_STATUS_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_INDEX_SPOT_STATUS}
ON {CLAIM_TABLE_NAME}({CLAIM_SPOT_ID}, {CLAIM_STATUS});
"""


# Automatically updates CLAIM.updated_at when editable CLAIM fields change.
CLAIM_TRIGGER_STAMP_UPDATED_AT = "trg_claim_stamp_updated_at"
CLAIM_TRIGGER_STAMP_UPDATED_AT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_TRIGGER_STAMP_UPDATED_AT}
AFTER UPDATE OF
    {CLAIM_ACCURACY},
    {CLAIM_STATUS}
ON {CLAIM_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{CLAIM_UPDATED_AT} IS OLD.{CLAIM_UPDATED_AT}
BEGIN
    UPDATE {CLAIM_TABLE_NAME}
    SET {CLAIM_UPDATED_AT} = unixepoch()
    WHERE {CLAIM_ID} = NEW.{CLAIM_ID};
END;
"""


# Prevents a claim insert after cancellation has been durably marked.
# This closes the final race even if application checks ran just before the
# cancellation transaction acquired SQLite's write lock.
CLAIM_TRIGGER_BLOCK_CANCELLING_SPOT_INSERT = "trg_claim_block_cancelling_spot_insert"
CLAIM_TRIGGER_BLOCK_CANCELLING_SPOT_INSERT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_TRIGGER_BLOCK_CANCELLING_SPOT_INSERT}
BEFORE INSERT ON {CLAIM_TABLE_NAME}
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM {SPOT_TABLE_NAME}
    WHERE {SPOT_ID} = NEW.{CLAIM_SPOT_ID}
      AND {SPOT_CANCELLATION_STARTED_AT} IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'spot cancellation has started');
END;
"""



# CLAIM_CODE #
# A CLAIM_CODE stores a code/password that can be used for a particular SPOT.
#
# For now this stores the code as text, as requested.
# Later, it would be safer to store a hash rather than the raw code.
CLAIM_CODE_TABLE_NAME = "claim_code"

CLAIM_CODE_ID = "id"                    # Unique ID / PK
CLAIM_CODE_SPOT_ID = "spot_id"          # FK from SPOT.id
CLAIM_CODE_CODE = "code"                # Code/password text
CLAIM_CODE_USED_BY = "used_by"          # FK from CLAIM.id


# The only ordinary modifiable field is used_by.
CLAIM_CODE_MOD_FIELDS = {
    CLAIM_CODE_USED_BY,
}


CREATE_CLAIM_CODE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {CLAIM_CODE_TABLE_NAME} (
    {CLAIM_CODE_ID} INTEGER PRIMARY KEY AUTOINCREMENT,

    {CLAIM_CODE_SPOT_ID} INTEGER NOT NULL,

    {CLAIM_CODE_CODE} TEXT NOT NULL,

    {CLAIM_CODE_USED_BY} INTEGER,

    FOREIGN KEY ({CLAIM_CODE_SPOT_ID})
        REFERENCES {SPOT_TABLE_NAME}({SPOT_ID})
        ON DELETE CASCADE,

    FOREIGN KEY ({CLAIM_CODE_USED_BY})
        REFERENCES {CLAIM_TABLE_NAME}({CLAIM_ID})
        ON DELETE SET NULL,

    UNIQUE ({CLAIM_CODE_SPOT_ID}, {CLAIM_CODE_CODE})
);
"""


# Finds codes for a particular SPOT.
CLAIM_CODE_INDEX_SPOT = "idx_claim_code_spot"
CLAIM_CODE_INDEX_SPOT_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_CODE_INDEX_SPOT}
ON {CLAIM_CODE_TABLE_NAME}({CLAIM_CODE_SPOT_ID});
"""


# Helps find unused codes for a SPOT.
CLAIM_CODE_INDEX_UNUSED_BY_SPOT = "idx_claim_code_unused_by_spot"
CLAIM_CODE_INDEX_UNUSED_BY_SPOT_QUERY = f"""
CREATE INDEX IF NOT EXISTS {CLAIM_CODE_INDEX_UNUSED_BY_SPOT}
ON {CLAIM_CODE_TABLE_NAME}({CLAIM_CODE_SPOT_ID}, {CLAIM_CODE_CODE})
WHERE {CLAIM_CODE_USED_BY} IS NULL;
"""


# Prevents one CLAIM from using multiple CLAIM_CODE rows.
CLAIM_CODE_INDEX_USED_BY_UNIQUE = "idx_claim_code_used_by_unique"
CLAIM_CODE_INDEX_USED_BY_UNIQUE_QUERY = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {CLAIM_CODE_INDEX_USED_BY_UNIQUE}
ON {CLAIM_CODE_TABLE_NAME}({CLAIM_CODE_USED_BY})
WHERE {CLAIM_CODE_USED_BY} IS NOT NULL;
"""


# Ensures a CLAIM_CODE can only be used by a CLAIM from the same SPOT.
CLAIM_CODE_TRIGGER_MATCH_SPOT_INSERT = "trg_claim_code_match_spot_insert"
CLAIM_CODE_TRIGGER_MATCH_SPOT_INSERT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_CODE_TRIGGER_MATCH_SPOT_INSERT}
BEFORE INSERT ON {CLAIM_CODE_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{CLAIM_CODE_USED_BY} IS NOT NULL
BEGIN
    SELECT
        CASE
            WHEN (
                SELECT c.{CLAIM_SPOT_ID}
                FROM {CLAIM_TABLE_NAME} c
                WHERE c.{CLAIM_ID} = NEW.{CLAIM_CODE_USED_BY}
            ) IS NOT NEW.{CLAIM_CODE_SPOT_ID}
            THEN RAISE(ABORT, 'claim_code.used_by must reference a claim from the same spot')
        END;
END;
"""


# Ensures a CLAIM_CODE can only be updated to use a CLAIM from the same SPOT.
CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE = "trg_claim_code_match_spot_update"
CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE}
BEFORE UPDATE OF {CLAIM_CODE_USED_BY} ON {CLAIM_CODE_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{CLAIM_CODE_USED_BY} IS NOT NULL
BEGIN
    SELECT
        CASE
            WHEN (
                SELECT c.{CLAIM_SPOT_ID}
                FROM {CLAIM_TABLE_NAME} c
                WHERE c.{CLAIM_ID} = NEW.{CLAIM_CODE_USED_BY}
            ) IS NOT NEW.{CLAIM_CODE_SPOT_ID}
            THEN RAISE(ABORT, 'claim_code.used_by must reference a claim from the same spot')
        END;
END;
"""


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



# TRANSACTION #
# A TRANSACTION records movement of NIM/Luna relating to USERS, SPOTS, and CLAIMS.
#
# Note:
# The actual SQL table name is "trans" rather than "transaction" because
# TRANSACTION is SQL syntax and makes queries needlessly awkward.
TRANS_TABLE_NAME = "trans"

TRANS_ID = "id"                              # Unique ID / PK
TRANS_USER_ID = "user_id"                    # FK from USER.id
TRANS_SPOT_ID = "spot_id"                    # Optional FK from SPOT.id
TRANS_CLAIM_ID = "claim_id"                  # Optional FK from CLAIM.id

TRANS_TYPE = "type"                          # Enum-ish. FILL_SPOT, CANCEL_SPOT, REMAINDER_REFUND, CLAIM, PLAT_FEE, CREATION_FEE
TRANS_AMOUNT = "amount"                      # Amount in Luna

TRANS_FROM_ADDRESS = "from_address"          # Sender Nimiq address
TRANS_TO_ADDRESS = "to_address"              # Recipient Nimiq address
TRANS_TX_HASH = "tx_hash"                    # Blockchain transaction hash
TRANS_BLOCK_NUMBER = "block_number"          # Optional confirmed block number

TRANS_STATUS = "t_status"                    # Enum-ish. PENDING, CONFIRMED, FAILED

TRANS_CREATED_AT = "created_at"              # Unix timestamp of creation
TRANS_COMPLETED_AT = "completed_at"          # Unix timestamp when finalised


# Only the status, block number, and completion timestamp should be modified normally.
TRANS_MOD_FIELDS = {
    TRANS_BLOCK_NUMBER,
    TRANS_STATUS,
    TRANS_COMPLETED_AT,
}


CREATE_TRANS_TABLE = f"""
CREATE TABLE IF NOT EXISTS {TRANS_TABLE_NAME} (
    {TRANS_ID} INTEGER PRIMARY KEY AUTOINCREMENT,

    {TRANS_USER_ID} INTEGER NOT NULL,

    {TRANS_SPOT_ID} INTEGER,

    {TRANS_CLAIM_ID} INTEGER,

    {TRANS_TYPE} INTEGER NOT NULL,

    {TRANS_AMOUNT} INTEGER NOT NULL,

    {TRANS_FROM_ADDRESS} TEXT NOT NULL,

    {TRANS_TO_ADDRESS} TEXT NOT NULL,

    {TRANS_TX_HASH} TEXT NOT NULL UNIQUE,

    {TRANS_BLOCK_NUMBER} INTEGER,

    {TRANS_STATUS} INTEGER NOT NULL
        DEFAULT {TRANS_STATUS_PENDING},

    {TRANS_CREATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    {TRANS_COMPLETED_AT} INTEGER,

    CHECK ({TRANS_AMOUNT} >= 0),
    CHECK ({TRANS_BLOCK_NUMBER} IS NULL OR {TRANS_BLOCK_NUMBER} >= 0),
    CHECK ({TRANS_STATUS} IN ({TRANS_STATUS_PENDING}, {TRANS_STATUS_CONFIRMED}, {TRANS_STATUS_FAILED})),
    CHECK ({TRANS_CREATED_AT} > 0),
    CHECK ({TRANS_COMPLETED_AT} IS NULL OR {TRANS_COMPLETED_AT} >= {TRANS_CREATED_AT}),

    FOREIGN KEY ({TRANS_USER_ID})
        REFERENCES {USER_TABLE_NAME}({USER_ID})
        ON DELETE RESTRICT,

    FOREIGN KEY ({TRANS_SPOT_ID})
        REFERENCES {SPOT_TABLE_NAME}({SPOT_ID})
        ON DELETE SET NULL,

    FOREIGN KEY ({TRANS_CLAIM_ID})
        REFERENCES {CLAIM_TABLE_NAME}({CLAIM_ID})
        ON DELETE SET NULL
);
"""


# Finds all transactions connected to a USER.
TRANS_INDEX_USER_CREATED = "idx_trans_user_created"
TRANS_INDEX_USER_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {TRANS_INDEX_USER_CREATED}
ON {TRANS_TABLE_NAME}({TRANS_USER_ID}, {TRANS_CREATED_AT});
"""


# Finds all transactions connected to a SPOT.
TRANS_INDEX_SPOT_CREATED = "idx_trans_spot_created"
TRANS_INDEX_SPOT_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {TRANS_INDEX_SPOT_CREATED}
ON {TRANS_TABLE_NAME}({TRANS_SPOT_ID}, {TRANS_CREATED_AT})
WHERE {TRANS_SPOT_ID} IS NOT NULL;
"""


# Finds transactions connected to a CLAIM.
TRANS_INDEX_CLAIM = "idx_trans_claim"
TRANS_INDEX_CLAIM_QUERY = f"""
CREATE INDEX IF NOT EXISTS {TRANS_INDEX_CLAIM}
ON {TRANS_TABLE_NAME}({TRANS_CLAIM_ID})
WHERE {TRANS_CLAIM_ID} IS NOT NULL;
"""


# Prevents duplicate active claim payouts while keeping failed payouts retryable.
TRANS_INDEX_CLAIM_ACTIVE_PAYOUT_UNIQUE = "idx_trans_claim_active_payout_unique"
TRANS_INDEX_CLAIM_ACTIVE_PAYOUT_UNIQUE_QUERY = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {TRANS_INDEX_CLAIM_ACTIVE_PAYOUT_UNIQUE}
ON {TRANS_TABLE_NAME}({TRANS_CLAIM_ID})
WHERE {TRANS_CLAIM_ID} IS NOT NULL
  AND {TRANS_TYPE} = {TRANS_TYPE_CLAIM}
  AND {TRANS_STATUS} != {TRANS_STATUS_FAILED};
"""


# Prevents two requests from creating the same active cancellation leg.
# A failed leg remains retryable because failed rows are outside the index.
TRANS_INDEX_SPOT_ACTIVE_CANCELLATION_UNIQUE = "idx_trans_spot_active_cancellation_unique"
TRANS_INDEX_SPOT_ACTIVE_CANCELLATION_UNIQUE_QUERY = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {TRANS_INDEX_SPOT_ACTIVE_CANCELLATION_UNIQUE}
ON {TRANS_TABLE_NAME}({TRANS_SPOT_ID}, {TRANS_TYPE})
WHERE {TRANS_SPOT_ID} IS NOT NULL
  AND {TRANS_TYPE} IN ({TRANS_TYPE_CANCEL_SPOT}, {TRANS_TYPE_PLAT_FEE})
  AND {TRANS_STATUS} != {TRANS_STATUS_FAILED};
"""


# Prevents duplicate active creation-fee sends while keeping a definitively
# failed on-chain attempt retryable. Ambiguous local intents remain pending and
# therefore continue to block automatic retries.
TRANS_INDEX_SPOT_ACTIVE_CREATION_FEE_UNIQUE = "idx_trans_spot_active_creation_fee_unique"
TRANS_INDEX_SPOT_ACTIVE_CREATION_FEE_UNIQUE_QUERY = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {TRANS_INDEX_SPOT_ACTIVE_CREATION_FEE_UNIQUE}
ON {TRANS_TABLE_NAME}({TRANS_SPOT_ID}, {TRANS_TYPE})
WHERE {TRANS_SPOT_ID} IS NOT NULL
  AND {TRANS_TYPE} = {TRANS_TYPE_CREATION_FEE}
  AND {TRANS_STATUS} != {TRANS_STATUS_FAILED};
"""


# Prevents duplicate active end-of-Spot remainder refunds while allowing a
# definitively failed attempt to be retried. Ambiguous local intents remain
# pending and continue to block automatic retries.
TRANS_INDEX_SPOT_ACTIVE_REMAINDER_REFUND_UNIQUE = "idx_trans_spot_active_remainder_refund_unique"
TRANS_INDEX_SPOT_ACTIVE_REMAINDER_REFUND_UNIQUE_QUERY = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {TRANS_INDEX_SPOT_ACTIVE_REMAINDER_REFUND_UNIQUE}
ON {TRANS_TABLE_NAME}({TRANS_SPOT_ID}, {TRANS_TYPE})
WHERE {TRANS_SPOT_ID} IS NOT NULL
  AND {TRANS_TYPE} = {TRANS_TYPE_REMAINDER_REFUND}
  AND {TRANS_STATUS} != {TRANS_STATUS_FAILED};
"""


# Helps queue/review pending, confirmed, and failed transactions.
TRANS_INDEX_STATUS_CREATED = "idx_trans_status_created"
TRANS_INDEX_STATUS_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {TRANS_INDEX_STATUS_CREATED}
ON {TRANS_TABLE_NAME}({TRANS_STATUS}, {TRANS_CREATED_AT});
"""


# Helps filter by transaction type and status.
TRANS_INDEX_TYPE_STATUS_CREATED = "idx_trans_type_status_created"
TRANS_INDEX_TYPE_STATUS_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {TRANS_INDEX_TYPE_STATUS_CREATED}
ON {TRANS_TABLE_NAME}({TRANS_TYPE}, {TRANS_STATUS}, {TRANS_CREATED_AT});
"""


# Automatically sets completed_at when a TRANSACTION stops being pending.
TRANS_TRIGGER_SET_COMPLETED_AT_UPDATE = "trg_trans_set_completed_at_update"
TRANS_TRIGGER_SET_COMPLETED_AT_UPDATE_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {TRANS_TRIGGER_SET_COMPLETED_AT_UPDATE}
AFTER UPDATE OF {TRANS_STATUS} ON {TRANS_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{TRANS_STATUS} != {TRANS_STATUS_PENDING}
 AND OLD.{TRANS_STATUS} = {TRANS_STATUS_PENDING}
 AND NEW.{TRANS_COMPLETED_AT} IS NULL
BEGIN
    UPDATE {TRANS_TABLE_NAME}
    SET {TRANS_COMPLETED_AT} = unixepoch()
    WHERE {TRANS_ID} = NEW.{TRANS_ID};
END;
"""


# Also handles the rare case that a non-pending TRANSACTION is inserted directly.
TRANS_TRIGGER_SET_COMPLETED_AT_INSERT = "trg_trans_set_completed_at_insert"
TRANS_TRIGGER_SET_COMPLETED_AT_INSERT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {TRANS_TRIGGER_SET_COMPLETED_AT_INSERT}
AFTER INSERT ON {TRANS_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{TRANS_STATUS} != {TRANS_STATUS_PENDING}
 AND NEW.{TRANS_COMPLETED_AT} IS NULL
BEGIN
    UPDATE {TRANS_TABLE_NAME}
    SET {TRANS_COMPLETED_AT} = unixepoch()
    WHERE {TRANS_ID} = NEW.{TRANS_ID};
END;
"""


# If both spot_id and claim_id are supplied, they must refer to the same SPOT.
TRANS_TRIGGER_MATCH_CLAIM_SPOT_INSERT = "trg_trans_match_claim_spot_insert"
TRANS_TRIGGER_MATCH_CLAIM_SPOT_INSERT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {TRANS_TRIGGER_MATCH_CLAIM_SPOT_INSERT}
BEFORE INSERT ON {TRANS_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{TRANS_SPOT_ID} IS NOT NULL
 AND NEW.{TRANS_CLAIM_ID} IS NOT NULL
BEGIN
    SELECT
        CASE
            WHEN (
                SELECT c.{CLAIM_SPOT_ID}
                FROM {CLAIM_TABLE_NAME} c
                WHERE c.{CLAIM_ID} = NEW.{TRANS_CLAIM_ID}
            ) IS NOT NEW.{TRANS_SPOT_ID}
            THEN RAISE(ABORT, 'trans.claim_id must reference a claim from the same spot')
        END;
END;
"""


# Same safety rule for updates.
TRANS_TRIGGER_MATCH_CLAIM_SPOT_UPDATE = "trg_trans_match_claim_spot_update"
TRANS_TRIGGER_MATCH_CLAIM_SPOT_UPDATE_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {TRANS_TRIGGER_MATCH_CLAIM_SPOT_UPDATE}
BEFORE UPDATE OF {TRANS_SPOT_ID}, {TRANS_CLAIM_ID} ON {TRANS_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{TRANS_SPOT_ID} IS NOT NULL
 AND NEW.{TRANS_CLAIM_ID} IS NOT NULL
BEGIN
    SELECT
        CASE
            WHEN (
                SELECT c.{CLAIM_SPOT_ID}
                FROM {CLAIM_TABLE_NAME} c
                WHERE c.{CLAIM_ID} = NEW.{TRANS_CLAIM_ID}
            ) IS NOT NEW.{TRANS_SPOT_ID}
            THEN RAISE(ABORT, 'trans.claim_id must reference a claim from the same spot')
        END;
END;
"""



# REPORT #
# A REPORT records a USER flagging a SPOT for moderation.
REPORT_TABLE_NAME = "report"

REPORT_ID = "id"                              # Unique ID / PK
REPORT_SPOT_ID = "spot_id"                    # FK from SPOT.id
REPORT_REPORTED_BY = "reported_by"            # FK from USER.id

REPORT_REASON = "reason"                      # Enum-ish reason code
REPORT_DETAILS = "details"                    # Optional user-written details

REPORT_STATUS = "r_status"                    # Enum-ish. PENDING, APPROVED, DISMISSED
REPORT_MODERATOR_NOTE = "moderator_note"      # Optional internal moderation note

REPORT_CREATED_AT = "created_at"              # Unix timestamp of creation
REPORT_REVIEWED_AT = "reviewed_at"            # Unix timestamp of review


# Only moderation fields should be modified normally.
REPORT_MOD_FIELDS = {
    REPORT_STATUS,
    REPORT_MODERATOR_NOTE,
    REPORT_REVIEWED_AT,
}


CREATE_REPORT_TABLE = f"""
CREATE TABLE IF NOT EXISTS {REPORT_TABLE_NAME} (
    {REPORT_ID} INTEGER PRIMARY KEY AUTOINCREMENT,

    {REPORT_SPOT_ID} INTEGER NOT NULL,

    {REPORT_REPORTED_BY} INTEGER NOT NULL,

    {REPORT_REASON} INTEGER NOT NULL,

    {REPORT_DETAILS} TEXT,

    {REPORT_STATUS} INTEGER NOT NULL
        DEFAULT {REPORT_STATUS_PENDING},

    {REPORT_MODERATOR_NOTE} TEXT,

    {REPORT_CREATED_AT} INTEGER NOT NULL
        DEFAULT (unixepoch()),

    {REPORT_REVIEWED_AT} INTEGER,

    CHECK ({REPORT_CREATED_AT} > 0),
    CHECK ({REPORT_REVIEWED_AT} IS NULL OR {REPORT_REVIEWED_AT} >= {REPORT_CREATED_AT}),

    FOREIGN KEY ({REPORT_SPOT_ID})
        REFERENCES {SPOT_TABLE_NAME}({SPOT_ID})
        ON DELETE CASCADE,

    FOREIGN KEY ({REPORT_REPORTED_BY})
        REFERENCES {USER_TABLE_NAME}({USER_ID})
        ON DELETE RESTRICT
);
"""


# Main moderation queue: pending/approved/dismissed reports ordered by age.
REPORT_INDEX_STATUS_CREATED = "idx_report_status_created"
REPORT_INDEX_STATUS_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {REPORT_INDEX_STATUS_CREATED}
ON {REPORT_TABLE_NAME}({REPORT_STATUS}, {REPORT_CREATED_AT});
"""


# Helps analyse/report abuse from particular USERS.
REPORT_INDEX_REPORTED_BY_CREATED = "idx_report_reported_by_created"
REPORT_INDEX_REPORTED_BY_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {REPORT_INDEX_REPORTED_BY_CREATED}
ON {REPORT_TABLE_NAME}({REPORT_REPORTED_BY}, {REPORT_CREATED_AT});
"""


# Helps collate all reports for a SPOT and filter by moderation status.
REPORT_INDEX_SPOT_STATUS_CREATED = "idx_report_spot_status_created"
REPORT_INDEX_SPOT_STATUS_CREATED_QUERY = f"""
CREATE INDEX IF NOT EXISTS {REPORT_INDEX_SPOT_STATUS_CREATED}
ON {REPORT_TABLE_NAME}({REPORT_SPOT_ID}, {REPORT_STATUS}, {REPORT_CREATED_AT});
"""


# Helps detect repeated reports from the same USER about the same SPOT.
REPORT_INDEX_SPOT_REPORTED_BY = "idx_report_spot_reported_by"
REPORT_INDEX_SPOT_REPORTED_BY_QUERY = f"""
CREATE INDEX IF NOT EXISTS {REPORT_INDEX_SPOT_REPORTED_BY}
ON {REPORT_TABLE_NAME}({REPORT_SPOT_ID}, {REPORT_REPORTED_BY});
"""


# Automatically sets reviewed_at when a REPORT stops being pending.
REPORT_TRIGGER_SET_REVIEWED_AT = "trg_report_set_reviewed_at"
REPORT_TRIGGER_SET_REVIEWED_AT_QUERY = f"""
CREATE TRIGGER IF NOT EXISTS {REPORT_TRIGGER_SET_REVIEWED_AT}
AFTER UPDATE OF {REPORT_STATUS} ON {REPORT_TABLE_NAME}
FOR EACH ROW
WHEN NEW.{REPORT_STATUS} != {REPORT_STATUS_PENDING}
 AND OLD.{REPORT_STATUS} = {REPORT_STATUS_PENDING}
 AND NEW.{REPORT_REVIEWED_AT} IS NULL
BEGIN
    UPDATE {REPORT_TABLE_NAME}
    SET {REPORT_REVIEWED_AT} = unixepoch()
    WHERE {REPORT_ID} = NEW.{REPORT_ID};
END;
"""




# --------------------------------------
# Views
# --------------------------------------

# Public listing of published SPOTs.
#
# The important derived fields are:
#   availability_rank: 0 if available now, 1 if upcoming
#   soon_sort: active spots sort by nearest ending; upcoming spots sort by nearest start
#
# Use this view for map/list pages, then add a WHERE clause for geohash/city/etc.
SPOT_VIEW_PUBLIC_LIST = "view_public_spot_list"
SPOT_VIEW_PUBLIC_LIST_QUERY = f"""
CREATE VIEW IF NOT EXISTS {SPOT_VIEW_PUBLIC_LIST} AS
WITH claim_counts AS (
    SELECT
        {CLAIM_SPOT_ID} AS spot_id,
        COUNT(*) AS claim_count,
        SUM(CASE WHEN {CLAIM_STATUS} = {CLAIM_STATUS_PENDING} THEN 1 ELSE 0 END) AS pending_claim_count,
        SUM(CASE WHEN {CLAIM_STATUS} = {CLAIM_STATUS_SUCCESS} THEN 1 ELSE 0 END) AS success_claim_count,
        SUM(CASE WHEN {CLAIM_STATUS} = {CLAIM_STATUS_FAILED} THEN 1 ELSE 0 END) AS failed_claim_count
    FROM {CLAIM_TABLE_NAME}
    GROUP BY {CLAIM_SPOT_ID}
),
code_counts AS (
    SELECT
        {CLAIM_CODE_SPOT_ID} AS spot_id,
        COUNT(*) AS claim_code_count,
        SUM(CASE WHEN {CLAIM_CODE_USED_BY} IS NULL THEN 1 ELSE 0 END) AS unused_code_count,
        SUM(CASE WHEN {CLAIM_CODE_USED_BY} IS NOT NULL THEN 1 ELSE 0 END) AS used_code_count
    FROM {CLAIM_CODE_TABLE_NAME}
    GROUP BY {CLAIM_CODE_SPOT_ID}
),
started_cancellations AS (
    SELECT {SPOT_ID} AS spot_id
    FROM {SPOT_TABLE_NAME}
    WHERE {SPOT_CANCELLATION_STARTED_AT} IS NOT NULL
    UNION
    SELECT DISTINCT {TRANS_SPOT_ID} AS spot_id
    FROM {TRANS_TABLE_NAME}
    WHERE {TRANS_TYPE} IN ({TRANS_TYPE_CANCEL_SPOT}, {TRANS_TYPE_PLAT_FEE})
      AND {TRANS_SPOT_ID} IS NOT NULL
)
SELECT
    s.*,

    pd.{PRIZEDRAW_PRIZE_COUNT},

    COALESCE(cc.claim_count, 0) AS claim_count,
    COALESCE(cc.pending_claim_count, 0) AS pending_claim_count,
    COALESCE(cc.success_claim_count, 0) AS success_claim_count,
    COALESCE(cc.failed_claim_count, 0) AS failed_claim_count,

    COALESCE(cdc.claim_code_count, 0) AS claim_code_count,
    COALESCE(cdc.unused_code_count, 0) AS unused_code_count,
    COALESCE(cdc.used_code_count, 0) AS used_code_count,

    CASE
        WHEN s.{SPOT_STARTS_AT} IS NULL OR s.{SPOT_STARTS_AT} <= unixepoch()
            THEN 0
        ELSE 1
    END AS availability_rank,

    CASE
        WHEN s.{SPOT_STARTS_AT} IS NULL OR s.{SPOT_STARTS_AT} <= unixepoch()
            THEN COALESCE(s.{SPOT_STARTS_AT} + s.{SPOT_ENDS_AT}, 9223372036854775807)
        ELSE s.{SPOT_STARTS_AT}
    END AS soon_sort

FROM {SPOT_TABLE_NAME} s
LEFT JOIN {PRIZEDRAW_TABLE_NAME} pd
    ON pd.{PRIZEDRAW_SPOT_ID} = s.{SPOT_ID}
LEFT JOIN claim_counts cc
    ON cc.spot_id = s.{SPOT_ID}
LEFT JOIN code_counts cdc
    ON cdc.spot_id = s.{SPOT_ID}
LEFT JOIN started_cancellations sc
    ON sc.spot_id = s.{SPOT_ID}
WHERE s.{SPOT_STATUS} = {SPOT_STATUS_PUBLISHED}
  AND sc.spot_id IS NULL
  AND s.{SPOT_LAT} IS NOT NULL
  AND s.{SPOT_LONG} IS NOT NULL
  AND (s.{SPOT_STARTS_AT} IS NULL OR (s.{SPOT_STARTS_AT} + s.{SPOT_ENDS_AT}) > unixepoch());
"""


# Owner/admin summary of every SPOT, including CLAIM, CLAIM_CODE, REPORT,
# TRANSACTION, and PRIZEDRAW aggregates.
SPOT_VIEW_OWNER_SUMMARY = "view_spot_owner_summary"
SPOT_VIEW_OWNER_SUMMARY_QUERY = f"""
CREATE VIEW IF NOT EXISTS {SPOT_VIEW_OWNER_SUMMARY} AS
WITH claim_counts AS (
    SELECT
        {CLAIM_SPOT_ID} AS spot_id,
        COUNT(*) AS claim_count,
        SUM(CASE WHEN {CLAIM_STATUS} = {CLAIM_STATUS_PENDING} THEN 1 ELSE 0 END) AS pending_claim_count,
        SUM(CASE WHEN {CLAIM_STATUS} = {CLAIM_STATUS_SUCCESS} THEN 1 ELSE 0 END) AS success_claim_count,
        SUM(CASE WHEN {CLAIM_STATUS} = {CLAIM_STATUS_FAILED} THEN 1 ELSE 0 END) AS failed_claim_count
    FROM {CLAIM_TABLE_NAME}
    GROUP BY {CLAIM_SPOT_ID}
),
code_counts AS (
    SELECT
        {CLAIM_CODE_SPOT_ID} AS spot_id,
        COUNT(*) AS claim_code_count,
        SUM(CASE WHEN {CLAIM_CODE_USED_BY} IS NULL THEN 1 ELSE 0 END) AS unused_code_count,
        SUM(CASE WHEN {CLAIM_CODE_USED_BY} IS NOT NULL THEN 1 ELSE 0 END) AS used_code_count
    FROM {CLAIM_CODE_TABLE_NAME}
    GROUP BY {CLAIM_CODE_SPOT_ID}
),
report_counts AS (
    SELECT
        {REPORT_SPOT_ID} AS spot_id,
        COUNT(*) AS report_count,
        SUM(CASE WHEN {REPORT_STATUS} = {REPORT_STATUS_PENDING} THEN 1 ELSE 0 END) AS pending_report_count,
        SUM(CASE WHEN {REPORT_STATUS} = {REPORT_STATUS_APPROVED} THEN 1 ELSE 0 END) AS approved_report_count,
        SUM(CASE WHEN {REPORT_STATUS} = {REPORT_STATUS_DISMISSED} THEN 1 ELSE 0 END) AS dismissed_report_count
    FROM {REPORT_TABLE_NAME}
    GROUP BY {REPORT_SPOT_ID}
),
trans_counts AS (
    SELECT
        {TRANS_SPOT_ID} AS spot_id,
        COUNT(*) AS trans_count,
        SUM({TRANS_AMOUNT}) AS trans_total_amount
    FROM {TRANS_TABLE_NAME}
    WHERE {TRANS_SPOT_ID} IS NOT NULL
    GROUP BY {TRANS_SPOT_ID}
)
SELECT
    s.*,

    creator.{USER_DISPLAY_NAME} AS creator_display_name,

    pd.{PRIZEDRAW_PRIZE_COUNT},

    COALESCE(cc.claim_count, 0) AS claim_count,
    COALESCE(cc.pending_claim_count, 0) AS pending_claim_count,
    COALESCE(cc.success_claim_count, 0) AS success_claim_count,
    COALESCE(cc.failed_claim_count, 0) AS failed_claim_count,

    COALESCE(cdc.claim_code_count, 0) AS claim_code_count,
    COALESCE(cdc.unused_code_count, 0) AS unused_code_count,
    COALESCE(cdc.used_code_count, 0) AS used_code_count,

    COALESCE(rc.report_count, 0) AS report_count,
    COALESCE(rc.pending_report_count, 0) AS pending_report_count,
    COALESCE(rc.approved_report_count, 0) AS approved_report_count,
    COALESCE(rc.dismissed_report_count, 0) AS dismissed_report_count,

    COALESCE(tc.trans_count, 0) AS trans_count,
    COALESCE(tc.trans_total_amount, 0) AS trans_total_amount

FROM {SPOT_TABLE_NAME} s
JOIN {USER_TABLE_NAME} creator
    ON creator.{USER_ID} = s.{SPOT_CREATED_BY}
LEFT JOIN {PRIZEDRAW_TABLE_NAME} pd
    ON pd.{PRIZEDRAW_SPOT_ID} = s.{SPOT_ID}
LEFT JOIN claim_counts cc
    ON cc.spot_id = s.{SPOT_ID}
LEFT JOIN code_counts cdc
    ON cdc.spot_id = s.{SPOT_ID}
LEFT JOIN report_counts rc
    ON rc.spot_id = s.{SPOT_ID}
LEFT JOIN trans_counts tc
    ON tc.spot_id = s.{SPOT_ID};
"""


# Detailed view of CLAIM_CODE rows, including who used each code.
# Useful for a SPOT owner password dashboard.
CLAIM_CODE_VIEW_DETAIL = "view_claim_code_detail"
CLAIM_CODE_VIEW_DETAIL_QUERY = f"""
CREATE VIEW IF NOT EXISTS {CLAIM_CODE_VIEW_DETAIL} AS
SELECT
    cc.{CLAIM_CODE_ID},
    cc.{CLAIM_CODE_SPOT_ID},
    cc.{CLAIM_CODE_CODE},
    cc.{CLAIM_CODE_USED_BY},

    s.{SPOT_CREATED_BY} AS spot_owner_id,
    s.{SPOT_LINK},
    s.{SPOT_TITLE},

    c.{CLAIM_RECIPIENT},
    c.{CLAIM_STATUS},
    c.{CLAIM_CLAIMED_AT},
    c.{CLAIM_UPDATED_AT},

    u.{USER_DISPLAY_NAME} AS recipient_display_name

FROM {CLAIM_CODE_TABLE_NAME} cc
JOIN {SPOT_TABLE_NAME} s
    ON s.{SPOT_ID} = cc.{CLAIM_CODE_SPOT_ID}
LEFT JOIN {CLAIM_TABLE_NAME} c
    ON c.{CLAIM_ID} = cc.{CLAIM_CODE_USED_BY}
LEFT JOIN {USER_TABLE_NAME} u
    ON u.{USER_ID} = c.{CLAIM_RECIPIENT};
"""


# Detailed CLAIM view for user history and owner/admin dashboards.
CLAIM_VIEW_DETAIL = "view_claim_detail"
CLAIM_VIEW_DETAIL_QUERY = f"""
CREATE VIEW IF NOT EXISTS {CLAIM_VIEW_DETAIL} AS
SELECT
    c.*,

    s.{SPOT_CREATED_BY} AS spot_owner_id,
    s.{SPOT_LINK},
    s.{SPOT_TITLE},
    s.{SPOT_CITY},
    s.{SPOT_COUNTRY},

    u.{USER_DISPLAY_NAME} AS recipient_display_name,

    cc.{CLAIM_CODE_ID} AS claim_code_id,
    cc.{CLAIM_CODE_CODE} AS claim_code

FROM {CLAIM_TABLE_NAME} c
JOIN {SPOT_TABLE_NAME} s
    ON s.{SPOT_ID} = c.{CLAIM_SPOT_ID}
JOIN {USER_TABLE_NAME} u
    ON u.{USER_ID} = c.{CLAIM_RECIPIENT}
LEFT JOIN {CLAIM_CODE_TABLE_NAME} cc
    ON cc.{CLAIM_CODE_USED_BY} = c.{CLAIM_ID};
"""


# Detailed TRANSACTION view linking transactions to USERS, SPOTS, and CLAIMS.
TRANS_VIEW_DETAIL = "view_trans_detail"
TRANS_VIEW_DETAIL_QUERY = f"""
CREATE VIEW IF NOT EXISTS {TRANS_VIEW_DETAIL} AS
SELECT
    t.*,

    tu.{USER_DISPLAY_NAME} AS trans_user_display_name,

    s.{SPOT_CREATED_BY} AS spot_owner_id,
    s.{SPOT_LINK},
    s.{SPOT_TITLE},

    c.{CLAIM_RECIPIENT},
    c.{CLAIM_STATUS},
    c.{CLAIM_CLAIMED_AT},

    cu.{USER_DISPLAY_NAME} AS claim_recipient_display_name

FROM {TRANS_TABLE_NAME} t
JOIN {USER_TABLE_NAME} tu
    ON tu.{USER_ID} = t.{TRANS_USER_ID}
LEFT JOIN {SPOT_TABLE_NAME} s
    ON s.{SPOT_ID} = t.{TRANS_SPOT_ID}
LEFT JOIN {CLAIM_TABLE_NAME} c
    ON c.{CLAIM_ID} = t.{TRANS_CLAIM_ID}
LEFT JOIN {USER_TABLE_NAME} cu
    ON cu.{USER_ID} = c.{CLAIM_RECIPIENT};
"""


# Detailed REPORT view for moderation screens.
REPORT_VIEW_DETAIL = "view_report_detail"
REPORT_VIEW_DETAIL_QUERY = f"""
CREATE VIEW IF NOT EXISTS {REPORT_VIEW_DETAIL} AS
SELECT
    r.*,

    reporter.{USER_DISPLAY_NAME} AS reporter_display_name,

    s.{SPOT_CREATED_BY} AS spot_owner_id,
    s.{SPOT_LINK},
    s.{SPOT_TITLE},
    s.{SPOT_CITY},
    s.{SPOT_COUNTRY},

    owner.{USER_DISPLAY_NAME} AS spot_owner_display_name

FROM {REPORT_TABLE_NAME} r
JOIN {USER_TABLE_NAME} reporter
    ON reporter.{USER_ID} = r.{REPORT_REPORTED_BY}
JOIN {SPOT_TABLE_NAME} s
    ON s.{SPOT_ID} = r.{REPORT_SPOT_ID}
JOIN {USER_TABLE_NAME} owner
    ON owner.{USER_ID} = s.{SPOT_CREATED_BY};
"""


# --------------------------------------
# Fresh-schema development policy
# --------------------------------------

async def _require_empty_or_current_schema(db) -> bool:
    """Reject databases whose schema version this code cannot safely operate.

    Local development can recreate its mock database. Public databases are
    never reset automatically and instead require compatible code or an
    explicit migration. An empty SQLite file is accepted and initialised below.
    """
    cur = await db.execute("PRAGMA user_version;")
    row = await cur.fetchone()
    current_version = int(row[0]) if row is not None else 0

    cur = await db.execute(
        f"""
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN (
              '{USER_TABLE_NAME}',
              '{SPOT_TABLE_NAME}',
              '{CLAIM_TABLE_NAME}',
              '{TRANS_TABLE_NAME}'
          )
        LIMIT 1;
        """
    )
    has_app_tables = await cur.fetchone() is not None

    if has_app_tables and current_version != SCHEMA_VERSION:
        if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
            raise RuntimeError(
                "This public NimHunt database uses an unsupported schema. Do not reset it; "
                "restore a compatible deployment or perform a deliberate migration."
            )
        raise RuntimeError(
            "This NimHunt development database uses an unsupported schema. "
            "Stop the server and run ./nimhunt_reset_mock_data.sh to recreate records.db."
        )
    return has_app_tables


async def _ensure_database_network_identity(db, *, had_app_tables: bool) -> None:
    """Bind a database to one network/mode and reject unsafe later reuse."""
    await db.executescript(CREATE_APP_METADATA_TABLE)
    cur = await db.execute(
        f"""
        SELECT {APP_METADATA_KEY}, {APP_METADATA_VALUE}
        FROM {APP_METADATA_TABLE_NAME}
        WHERE {APP_METADATA_KEY} IN (?, ?, ?);
        """,
        (METADATA_NIMIQ_NETWORK, METADATA_NIMIQ_NETWORK_ID, METADATA_DEPLOYMENT_MODE),
    )
    metadata = {str(row[0]): str(row[1]) for row in await cur.fetchall()}
    configured_network = str(getattr(const, "NIMIQ_NETWORK", "")).strip()
    configured_network_id = str(int(getattr(const, "NIMIQ_NETWORK_ID", 0)))
    configured_mode = str(getattr(const, "DEPLOYMENT_MODE", "development")).strip()

    if not metadata:
        if had_app_tables and bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
            raise RuntimeError(
                "This existing NimHunt database has no recorded Nimiq network identity. "
                "Use a fresh database for a public deployment, or open it once in "
                "development with the correct network to bind it deliberately."
            )
        await db.executemany(
            f"""
            INSERT INTO {APP_METADATA_TABLE_NAME} ({APP_METADATA_KEY}, {APP_METADATA_VALUE})
            VALUES (?, ?);
            """,
            (
                (METADATA_NIMIQ_NETWORK, configured_network),
                (METADATA_NIMIQ_NETWORK_ID, configured_network_id),
                (METADATA_DEPLOYMENT_MODE, configured_mode),
            ),
        )
        return

    stored_network = metadata.get(METADATA_NIMIQ_NETWORK)
    stored_network_id = metadata.get(METADATA_NIMIQ_NETWORK_ID)
    stored_mode = metadata.get(METADATA_DEPLOYMENT_MODE)
    if stored_network is None or stored_network_id is None or stored_mode is None:
        raise RuntimeError("NimHunt database deployment metadata is incomplete")
    if stored_network != configured_network or stored_network_id != configured_network_id:
        raise RuntimeError(
            "NimHunt database network mismatch: database is bound to "
            f"{stored_network} (ID {stored_network_id}), but this process is configured "
            f"for {configured_network} (ID {configured_network_id}). Use a separate database."
        )
    if stored_mode != configured_mode:
        raise RuntimeError(
            "NimHunt database deployment-mode mismatch: database is bound to "
            f"{stored_mode}, but this process is configured for {configured_mode}. "
            "Use a separate database rather than exposing development or cross-mode data."
        )


# --------------------------------------
# Initialize database and create relevant tables if necessary
# --------------------------------------

async def init_db():
    """
    Creates the database tables, indexes, triggers, and views.

    Call this once when the FastAPI app starts.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA synchronous = NORMAL;")
        await db.execute("PRAGMA foreign_keys = ON;")
        had_app_tables = await _require_empty_or_current_schema(db)
        await _ensure_database_network_identity(db, had_app_tables=had_app_tables)

        await db.executescript(CREATE_USER_TABLE)
        await db.executescript(USER_INDEX_STATUS_QUERY)
        await db.executescript(USER_INDEX_LAST_SEEN_QUERY)


        await db.executescript(CREATE_SPOT_TABLE)
        await db.executescript(SPOT_INDEX_CREATED_BY_QUERY)
        await db.executescript(SPOT_INDEX_STATUS_CREATED_QUERY)
        await db.executescript(SPOT_INDEX_GEOHASH_QUERY)
        await db.executescript(SPOT_INDEX_PUBLIC_GEOHASH_SOON_QUERY)
        await db.executescript(SPOT_INDEX_PUBLIC_CITY_COUNTRY_SOON_QUERY)
        await db.executescript(SPOT_INDEX_PUBLIC_SOON_QUERY)
        await db.executescript(SPOT_TRIGGER_STAMP_UPDATED_AT_QUERY)

        await db.executescript(CREATE_PRIZEDRAW_TABLE)
        await db.executescript(PRIZEDRAW_TRIGGER_PRIZE_COUNT_INSERT_QUERY)
        await db.executescript(PRIZEDRAW_TRIGGER_PRIZE_COUNT_UPDATE_QUERY)
        await db.executescript(SPOT_TRIGGER_MAX_TOTAL_CLAIMS_PRIZEDRAW_UPDATE_QUERY)

        await db.executescript(CREATE_CLAIM_TABLE)
        await db.executescript(CLAIM_INDEX_SPOT_QUERY)
        await db.executescript(CLAIM_INDEX_RECIPIENT_QUERY)
        await db.executescript(CLAIM_INDEX_SPOT_RECIPIENT_STATUS_QUERY)
        await db.executescript(CLAIM_INDEX_SPOT_STATUS_QUERY)
        await db.executescript(CLAIM_TRIGGER_STAMP_UPDATED_AT_QUERY)
        await db.executescript(CLAIM_TRIGGER_BLOCK_CANCELLING_SPOT_INSERT_QUERY)

        await db.executescript(CREATE_CLAIM_CODE_TABLE)
        await db.executescript(CLAIM_CODE_INDEX_SPOT_QUERY)
        await db.executescript(CLAIM_CODE_INDEX_UNUSED_BY_SPOT_QUERY)
        await db.executescript(CLAIM_CODE_INDEX_USED_BY_UNIQUE_QUERY)
        await db.executescript(CLAIM_CODE_TRIGGER_MATCH_SPOT_INSERT_QUERY)
        await db.executescript(CLAIM_CODE_TRIGGER_MATCH_SPOT_UPDATE_QUERY)

        await db.executescript(CREATE_CLAIM_CODE_ATTEMPT_TABLE)
        await db.executescript(CLAIM_CODE_ATTEMPT_INDEX_CODE_QUERY)
        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_MATCH_SPOT_QUERY)
        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_PENDING_QUERY)
        await db.executescript(CLAIM_CODE_ATTEMPT_TRIGGER_CLEANUP_QUERY)

        await db.executescript(CREATE_TRANS_TABLE)
        await db.executescript(TRANS_INDEX_USER_CREATED_QUERY)
        await db.executescript(TRANS_INDEX_SPOT_CREATED_QUERY)
        await db.executescript(TRANS_INDEX_CLAIM_QUERY)
        await db.executescript(TRANS_INDEX_CLAIM_ACTIVE_PAYOUT_UNIQUE_QUERY)
        await db.executescript(TRANS_INDEX_SPOT_ACTIVE_CANCELLATION_UNIQUE_QUERY)
        await db.executescript(TRANS_INDEX_SPOT_ACTIVE_CREATION_FEE_UNIQUE_QUERY)
        await db.executescript(TRANS_INDEX_SPOT_ACTIVE_REMAINDER_REFUND_UNIQUE_QUERY)
        await db.executescript(TRANS_INDEX_STATUS_CREATED_QUERY)
        await db.executescript(TRANS_INDEX_TYPE_STATUS_CREATED_QUERY)
        await db.executescript(TRANS_TRIGGER_SET_COMPLETED_AT_UPDATE_QUERY)
        await db.executescript(TRANS_TRIGGER_SET_COMPLETED_AT_INSERT_QUERY)
        await db.executescript(TRANS_TRIGGER_MATCH_CLAIM_SPOT_INSERT_QUERY)
        await db.executescript(TRANS_TRIGGER_MATCH_CLAIM_SPOT_UPDATE_QUERY)

        await db.executescript(CREATE_REPORT_TABLE)
        await db.executescript(REPORT_INDEX_STATUS_CREATED_QUERY)
        await db.executescript(REPORT_INDEX_REPORTED_BY_CREATED_QUERY)
        await db.executescript(REPORT_INDEX_SPOT_STATUS_CREATED_QUERY)
        await db.executescript(REPORT_INDEX_SPOT_REPORTED_BY_QUERY)
        await db.executescript(REPORT_TRIGGER_SET_REVIEWED_AT_QUERY)

        await db.executescript(SPOT_VIEW_PUBLIC_LIST_QUERY)
        await db.executescript(SPOT_VIEW_OWNER_SUMMARY_QUERY)
        await db.executescript(CLAIM_CODE_VIEW_DETAIL_QUERY)
        await db.executescript(CLAIM_VIEW_DETAIL_QUERY)
        await db.executescript(TRANS_VIEW_DETAIL_QUERY)
        await db.executescript(REPORT_VIEW_DETAIL_QUERY)

        await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
        await db.commit()


# --------------------------------------
# Getter for database connection
#
# Use this everywhere database operations are required.
#
# Example:
#
#   async with get_db() as db:
#       await db.execute(...)
#       await db.commit()
# --------------------------------------

@asynccontextmanager
async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row

    try:
        # These are per-connection settings.
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA synchronous = NORMAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")

        yield db

    except Exception:
        # If something goes wrong halfway through a transaction,
        # roll back safely.
        with suppress(Exception):
            await db.rollback()
        raise

    finally:
        await db.close()



# --------------------------------------
# Common read helpers
# --------------------------------------

async def fetch_public_spots_by_geohash(
    geohash_prefix: str,
    limit: int = 50,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns published, non-expired SPOTs whose geohash begins with geohash_prefix.

    This is the main map-style lookup.

    Geohash note:
    Geohashes work as a prefix search. A short prefix covers a large area;
    a longer prefix covers a smaller area. For example, the frontend/backend
    can choose a prefix length based on zoom level.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {SPOT_VIEW_PUBLIC_LIST}
            WHERE {SPOT_GEOHASH} LIKE ?
            ORDER BY availability_rank ASC, soon_sort ASC, {SPOT_ID} ASC
            LIMIT ? OFFSET ?;
            """,
            (f"{geohash_prefix}%", limit, offset),
        )
        return await cursor.fetchall()


async def fetch_public_spots_by_city_country(
    city: str | None = None,
    country: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Fallback public SPOT lookup using stored city/country labels.

    This is less precise than geohash, but useful for simple browse pages.
    """
    where_parts = []
    params = []

    if city is not None:
        where_parts.append(f"{SPOT_CITY} = ?")
        params.append(city)

    if country is not None:
        where_parts.append(f"{SPOT_COUNTRY} = ?")
        params.append(country)

    where_sql = ""
    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)

    params.extend([limit, offset])

    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {SPOT_VIEW_PUBLIC_LIST}
            {where_sql}
            ORDER BY availability_rank ASC, soon_sort ASC, {SPOT_ID} ASC
            LIMIT ? OFFSET ?;
            """,
            params,
        )
        return await cursor.fetchall()


async def fetch_spot_owner_summaries(
    user_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns the SPOTs created by a USER, including aggregate CLAIM,
    CLAIM_CODE, REPORT, TRANSACTION, and PRIZEDRAW data.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {SPOT_VIEW_OWNER_SUMMARY}
            WHERE {SPOT_CREATED_BY} = ?
            ORDER BY {SPOT_CREATED_AT} DESC, {SPOT_ID} DESC
            LIMIT ? OFFSET ?;
            """,
            (user_id, limit, offset),
        )
        return await cursor.fetchall()


async def fetch_owner_spot_claim_codes(
    owner_user_id: int,
    spot_id: int,
) -> list[aiosqlite.Row]:
    """
    Returns all CLAIM_CODE rows for one SPOT, but only if the requesting
    USER owns that SPOT.

    This is what the owner would use to see which codes/passwords are left,
    which have been used, and who used them.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {CLAIM_CODE_VIEW_DETAIL}
            WHERE spot_owner_id = ?
              AND {CLAIM_CODE_SPOT_ID} = ?
            ORDER BY
                CASE WHEN {CLAIM_CODE_USED_BY} IS NULL THEN 0 ELSE 1 END ASC,
                {CLAIM_CODE_ID} ASC;
            """,
            (owner_user_id, spot_id),
        )
        return await cursor.fetchall()


async def fetch_owner_spot_claims(
    owner_user_id: int,
    spot_id: int,
) -> list[aiosqlite.Row]:
    """
    Returns CLAIMs for a SPOT, but only if the requesting USER owns that SPOT.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {CLAIM_VIEW_DETAIL}
            WHERE spot_owner_id = ?
              AND {CLAIM_SPOT_ID} = ?
            ORDER BY {CLAIM_CLAIMED_AT} DESC, {CLAIM_ID} DESC;
            """,
            (owner_user_id, spot_id),
        )
        return await cursor.fetchall()


async def fetch_user_claims(
    user_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns CLAIMs made by a USER.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {CLAIM_VIEW_DETAIL}
            WHERE {CLAIM_RECIPIENT} = ?
            ORDER BY {CLAIM_CLAIMED_AT} DESC, {CLAIM_ID} DESC
            LIMIT ? OFFSET ?;
            """,
            (user_id, limit, offset),
        )
        return await cursor.fetchall()


async def fetch_user_transactions(
    user_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns TRANSACTIONs directly associated with a USER.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {TRANS_VIEW_DETAIL}
            WHERE {TRANS_USER_ID} = ?
            ORDER BY {TRANS_CREATED_AT} DESC, {TRANS_ID} DESC
            LIMIT ? OFFSET ?;
            """,
            (user_id, limit, offset),
        )
        return await cursor.fetchall()


async def fetch_owner_spot_transactions(
    owner_user_id: int,
    spot_id: int,
) -> list[aiosqlite.Row]:
    """
    Returns TRANSACTIONs for a SPOT, but only if the requesting USER owns it.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {TRANS_VIEW_DETAIL}
            WHERE spot_owner_id = ?
              AND {TRANS_SPOT_ID} = ?
            ORDER BY {TRANS_CREATED_AT} DESC, {TRANS_ID} DESC;
            """,
            (owner_user_id, spot_id),
        )
        return await cursor.fetchall()


async def fetch_reports_by_status(
    report_status: int,
    limit: int = 100,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns REPORTs by moderation status.

    Useful for a moderation queue.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {REPORT_VIEW_DETAIL}
            WHERE {REPORT_STATUS} = ?
            ORDER BY {REPORT_CREATED_AT} ASC, {REPORT_ID} ASC
            LIMIT ? OFFSET ?;
            """,
            (report_status, limit, offset),
        )
        return await cursor.fetchall()


async def fetch_reports_for_spot(
    spot_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns REPORTs for a particular SPOT.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {REPORT_VIEW_DETAIL}
            WHERE {REPORT_SPOT_ID} = ?
            ORDER BY {REPORT_CREATED_AT} DESC, {REPORT_ID} DESC
            LIMIT ? OFFSET ?;
            """,
            (spot_id, limit, offset),
        )
        return await cursor.fetchall()


async def fetch_reports_by_user(
    user_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    """
    Returns REPORTs submitted by a USER.
    """
    async with get_db() as db:
        cursor = await db.execute(
            f"""
            SELECT *
            FROM {REPORT_VIEW_DETAIL}
            WHERE {REPORT_REPORTED_BY} = ?
            ORDER BY {REPORT_CREATED_AT} DESC, {REPORT_ID} DESC
            LIMIT ? OFFSET ?;
            """,
            (user_id, limit, offset),
        )
        return await cursor.fetchall()

# --------------------------------------
# Basic diagnosis tool
# --------------------------------------

async def db_health_check() -> dict:
    """
    Returns a small dictionary describing the database state.

    This is useful for a /health route later.
    """
    result = {
        "ok": False,
        "integrity_check": None,
        "foreign_key_violations": None,
        "journal_mode": None,
        "foreign_keys": None,
    }

    async with get_db() as db:
        # Check whether foreign keys are enabled on this connection.
        cursor = await db.execute("PRAGMA foreign_keys;")
        row = await cursor.fetchone()
        result["foreign_keys"] = int(row[0]) if row else None

        # Check journal mode. Ideally this should be "wal".
        cursor = await db.execute("PRAGMA journal_mode;")
        row = await cursor.fetchone()
        result["journal_mode"] = row[0] if row else None

        # Check for foreign key problems.
        cursor = await db.execute("PRAGMA foreign_key_check;")
        rows = await cursor.fetchall()
        result["foreign_key_violations"] = len(rows)

        # General SQLite integrity check.
        cursor = await db.execute("PRAGMA integrity_check;")
        row = await cursor.fetchone()
        result["integrity_check"] = row[0] if row else None

    result["ok"] = (
        result["foreign_keys"] == 1
        and result["foreign_key_violations"] == 0
        and result["integrity_check"] == "ok"
    )

    return result
