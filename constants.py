"""
─────────────────────────────────────────────

constants.py

Shared constants for NimHunt.

─────────────────────────────────────────────
"""

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """Read an integer setting while keeping a clear import-time error."""
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return int(default)
    try:
        return int(raw_value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_nim_amount(name: str, default_nim: str, *, luna_per_nim: int) -> int:
    """Read a non-negative NIM amount and convert it exactly to Luna."""
    raw_value = os.getenv(name, default_nim).strip()
    try:
        nim = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a valid NIM amount") from exc

    if not nim.is_finite() or nim < 0:
        raise ValueError(f"{name} must be a non-negative NIM amount")

    luna = nim * int(luna_per_nim)
    if luna != luna.to_integral_value():
        raise ValueError(f"{name} cannot use more than 5 decimal places")
    return int(luna)


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _optional_env_bool(name: str) -> bool | None:
    """Read an optional boolean environment variable with strict validation."""
    if name not in os.environ or not os.environ[name].strip():
        return None
    value = os.environ[name].strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off")


def _deployment_mode() -> str:
    """Resolve the preferred deployment mode and the legacy production flag."""
    mode_env = "NIMHUNT_DEPLOYMENT_MODE"
    legacy_env = "NIMHUNT_PRODUCTION"
    raw_mode = os.getenv(mode_env, "").strip().lower().replace("_", "-")
    legacy_production = _optional_env_bool(legacy_env)

    if raw_mode:
        allowed = {"development", "public-testnet", "production"}
        if raw_mode not in allowed:
            raise ValueError(
                f"{mode_env} must be development, public-testnet, or production"
            )
        mode_is_production = raw_mode == "production"
        if legacy_production is not None and legacy_production != mode_is_production:
            raise ValueError(
                f"{mode_env}={raw_mode!r} conflicts with {legacy_env}="
                f"{int(legacy_production)}"
            )
        return raw_mode

    return "production" if legacy_production else "development"


def _canonical_nimiq_network(value: str) -> str:
    """Normalise known Nimiq network names without hiding unknown values."""
    clean_value = str(value or "").strip()
    known_networks = {
        "testalbatross": "TestAlbatross",
        "mainalbatross": "MainAlbatross",
        "devalbatross": "DevAlbatross",
    }
    return known_networks.get(clean_value.lower(), clean_value)


# -----------------------------
# APP Settings
# -----------------------------

# Absolute project paths keep static files and templates available even when
# Uvicorn is launched from a different working directory.
PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Change this once if the app is ever renamed.
APP_NAME = "NimHunt"

# Browser/tab icon. The actual SVG lives in static/favicon.svg.
APP_ICON_PATH = "/favicon.ico"

# Public link for installing/opening Nimiq Pay.
# Used by homepage copy when the mini app is opened outside Nimiq Pay.
NIMIQ_PAY_URL = "https://nimpay.app"

# Show the prominent Home-page safety notice. This is deliberately a simple
# server-side switch rather than browser state, so one deployment setting
# controls every visitor consistently.
SHOW_PROJECT_DISCLAIMER = True

# -----------------------------
# Development / test settings
# -----------------------------

# Deployment safety is independent from the selected blockchain network.
# NIMHUNT_DEPLOYMENT_MODE is preferred; NIMHUNT_PRODUCTION remains a strict
# compatibility alias for production only. Public modes disable every local
# shortcut, while production specifically means real-NIM MainAlbatross.
DEPLOYMENT_MODE = _deployment_mode()
PUBLIC_TESTNET_MODE = DEPLOYMENT_MODE == "public-testnet"
PRODUCTION_MODE = DEPLOYMENT_MODE == "production"
PUBLIC_DEPLOYMENT = PUBLIC_TESTNET_MODE or PRODUCTION_MODE
TEST_FEATURES_ENABLED = DEPLOYMENT_MODE == "development"

# Development helper: the home/session API may return TEST_USER_ID when the
# webview is opened outside Nimiq Pay and no device hash is available.
DEFAULT_TO_TEST_USER = TEST_FEATURES_ENABLED

# Mock desktop user created by spoof.py. SQLite allows an explicit INTEGER
# PRIMARY KEY value of 0, so this is safe for the test dataset.
TEST_USER_ID = 0

# Display-name validation. Kept in constants so frontend and backend use the
# same simple rule, and so the rule can be expanded later in one place.
DISPLAY_NAME_MIN_CHARS = 3
DISPLAY_NAME_MAX_CHARS = 18

# Spot title validation. A draft SPOT can be created with only a title;
# the remaining fields are filled on the full Create Spot form. Keep this
# independent from display names because useful location/event titles are longer.
SPOT_TITLE_MIN_CHARS = 3
SPOT_TITLE_MAX_CHARS = 27

# Maximum number of editable draft SPOTs one user may have at once.
# Once a draft is published, cancelled, or deleted, it no longer counts.
MAX_DRAFT_SPOTS_PER_USER = 3


# Find Spots map defaults.
# MAX_MAP_INIT_SPOTS is the target number of nearby spots the initial map view
# should try to include. MAX_MAP_ZOOM_OUT prevents the page from zooming too
# far out just to find that many spots.
MAX_MAP_INIT_SPOTS = 10
MAX_MAP_ZOOM_OUT = 11

# Leaflet/OpenStreetMap defaults for the Find Spots page.
# Keep these here so the tile provider can be changed from Python later.
LEAFLET_CSS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_ATTRIBUTION = "&copy; OpenStreetMap contributors"

# Page URLs are centralised so browser copy and redirects stay consistent.
CREATE_SPOT_URL = "/create"
SPOT_PAGE_URL_PREFIX = "/spot"
CLAIM_PAGE_URL_PREFIX = "/claim"


# -----------------------------
# SPOT creation limits
# -----------------------------

# Claim radius bounds in metres. These are enforced by db_access.py and exposed
# to the Create Spot form so frontend and backend stay in step.
MIN_SPOT_RADIUS_METRES = 25
MAX_SPOT_RADIUS_METRES = 1000

# Claim duration bounds in seconds. 0 means no required waiting period.
MIN_SPOT_CLAIM_DURATION_SECONDS = 0
# Smallest non-zero duration exposed by the Create Spot form. 0 still means None.
MIN_SPOT_NONZERO_CLAIM_DURATION_SECONDS = 10 * 60
MAX_SPOT_CLAIM_DURATION_SECONDS = 12 * 60 * 60

# Spot active-window duration. SPOT.ends_at now stores seconds after
# SPOT.starts_at, not an absolute unix timestamp.
MIN_SPOT_ENDS_AFTER_SECONDS = 60 * 60
MAX_SPOT_ENDS_AFTER_SECONDS = 4 * 7 * 24 * 60 * 60
DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS = 24 * 60 * 60

# Per-user claim bounds. 0 means unlimited claims per user.
MIN_SPOT_MAX_CLAIMS_PER_USER = 0
MAX_SPOT_MAX_CLAIMS_PER_USER = 10

# Total participant/claim bounds. 0 means unlimited total participants and is
# only valid for Prizedraw spots. Finite Prizedraws need at least two people;
# standard spots retain their existing minimum of one claim.
MIN_SPOT_MAX_TOTAL_CLAIMS = 1
MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS = 0
MIN_FINITE_PRIZEDRAW_TOTAL_PARTICIPANTS = 2
MAX_SPOT_MAX_TOTAL_CLAIMS = 1000


# Prizedraw prize count bounds and slider options.
MIN_PRIZEDRAW_PRIZE_COUNT = 1
MAX_PRIZEDRAW_PRIZE_COUNT = 100
DEFAULT_DRAFT_PRIZEDRAW_PRIZE_COUNT = 1
PRIZEDRAW_PRIZE_COUNT_OPTIONS = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 20, 25, 50, 100,
)

# A new Prizedraw draft begins with unlimited total participants.
DEFAULT_DRAFT_PRIZEDRAW_MAX_TOTAL_CLAIMS = MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS

# Nimiq amount conversion and total spot value bounds.
# total_value is stored in Luna; 1 NIM = 100,000 Luna.
LUNA_PER_NIM = 100_000
MIN_SPOT_TOTAL_VALUE_NIM = 100
MIN_SPOT_TOTAL_VALUE = MIN_SPOT_TOTAL_VALUE_NIM * LUNA_PER_NIM

# Minimum payout floors. Standard spots divide total_value by max_total_claims;
# Prizedraw spots divide total_value by prize_count. These are enforced before
# a draft can be saved and again before it can be published.
MIN_STANDARD_CLAIM_PAYOUT_NIM = 100
MIN_STANDARD_CLAIM_PAYOUT = MIN_STANDARD_CLAIM_PAYOUT_NIM * LUNA_PER_NIM
MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM = 1000
MIN_PRIZEDRAW_PRIZE_PAYOUT = MIN_PRIZEDRAW_PRIZE_PAYOUT_NIM * LUNA_PER_NIM

# Platform-fee rules. Creation fees are snapshotted onto each new Spot so a
# later configuration change cannot alter an already-created draft's funding
# target. Both creation and cancellation fees use the same operator-controlled
# destination address.
STANDARD_SPOT_CREATION_FEE = _env_nim_amount(
    "NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM",
    "1",
    luna_per_nim=LUNA_PER_NIM,
)
PRIZEDRAW_SPOT_CREATION_FEE = _env_nim_amount(
    "NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM",
    "1",
    luna_per_nim=LUNA_PER_NIM,
)

# This valid TestAlbatross address is derived from the repository's public
# development mnemonic at a reserved path. It is convenient for local testing
# only: anyone can derive its key, so public deployments explicitly reject it.
DEV_PLATFORM_FEE_ADDRESS = "NQ35 6EUX JD08 6F88 KYA2 EDMC V3BC PXLB ELSB"

# The operator supplies a human-readable cancellation fee, converted exactly
# to Luna at import time. One shared address receives both fee types.
SPOT_CANCELLATION_FEE = _env_nim_amount(
    "NIMHUNT_SPOT_CANCELLATION_FEE_NIM",
    "1",
    luna_per_nim=LUNA_PER_NIM,
)
SPOT_CANCELLATION_FEE_ADDRESS = os.getenv(
    "NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS",
    DEV_PLATFORM_FEE_ADDRESS,
).strip()

# Draft SPOT defaults used when a creator has only entered the initial title.
# These keep the row valid while the full Create Spot form is still incomplete.
DEFAULT_DRAFT_SPOT_RADIUS_METRES = 200
DEFAULT_DRAFT_SPOT_CLAIM_DURATION_SECONDS = 0
DEFAULT_DRAFT_SPOT_MAX_CLAIMS_PER_USER = 1
DEFAULT_DRAFT_SPOT_MAX_TOTAL_CLAIMS = MIN_SPOT_MAX_TOTAL_CLAIMS
DEFAULT_DRAFT_SPOT_TOTAL_VALUE = MIN_SPOT_TOTAL_VALUE
DEFAULT_DRAFT_SPOT_USE_PASSWORD = 0

# Wallet / deposit-address generation and Nimiq transaction integration.
#
# The database stores a public deposit address plus immutable derivation
# metadata. Private signing material should be derived by wallet.py from an
# encrypted master seed or delegated to a configured signing command. It should
# never be stored in SQLite.
#
# Network names match the official Nimiq client configuration names. The
# numeric IDs are protocol values used when constructing and verifying
# transactions: TestAlbatross=5, MainAlbatross=24, DevAlbatross=6.
NIMIQ_NETWORK_IDS = {
    "TestAlbatross": 5,
    "MainAlbatross": 24,
    "DevAlbatross": 6,
}
NIMIQ_NETWORK = _canonical_nimiq_network(
    os.getenv("NIMHUNT_NIMIQ_NETWORK", "TestAlbatross")
)
NIMIQ_NETWORK_ID = _env_int(
    "NIMHUNT_NIMIQ_NETWORK_ID",
    NIMIQ_NETWORK_IDS.get(NIMIQ_NETWORK, 0),
)

# Public RPC endpoints are convenient defaults for this small application. A
# deployment can override them with a trusted provider or its own node. Keeping
# the default aligned with the selected network prevents test transactions from
# accidentally being queried on mainnet (or vice versa).
_DEFAULT_NIMIQ_RPC_URLS = {
    "TestAlbatross": "https://rpc.testnet.nimiqwatch.com/",
    "MainAlbatross": "https://rpc.nimiqwatch.com",
    "DevAlbatross": "",
}
NIMIQ_RPC_URL = os.getenv(
    "NIMHUNT_NIMIQ_RPC_URL",
    _DEFAULT_NIMIQ_RPC_URLS.get(NIMIQ_NETWORK, ""),
).strip()
NIMIQ_RPC_TIMEOUT_SECONDS = _env_int("NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS", 12)
# How many recent address transactions to inspect when getTransactionByHash
# returns an unstructured response that still needs from/to/amount proof.
NIMIQ_ADDRESS_TX_LOOKUP_LIMIT = _env_int("NIMHUNT_NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)
NIMIQ_TRANSACTION_FEE = _env_int("NIMHUNT_NIMIQ_TRANSACTION_FEE", 0)
# Keep human-readable transaction descriptions compact. The limit is measured
# in UTF-8 bytes because that is what is placed in Nimiq transaction data.
NIMIQ_TRANSACTION_DESCRIPTION_MAX_BYTES = 30

# Nimiq Pay / Hub endpoints used by browser-side user deposits. The server does
# not drive the user's Pay app directly; it returns deposit intents and records
# the tx hash after Pay confirms the transaction.
_DEFAULT_NIMIQ_HUB_URLS = {
    "TestAlbatross": "https://hub.nimiq-testnet.com",
    "MainAlbatross": "https://hub.nimiq.com",
    "DevAlbatross": "https://hub.nimiq-testnet.com",
}
NIMIQ_HUB_URL = os.getenv(
    "NIMHUNT_NIMIQ_HUB_URL",
    _DEFAULT_NIMIQ_HUB_URLS.get(NIMIQ_NETWORK, ""),
).strip()
# Address/signing integration. If NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND or
# NIMHUNT_NIMIQ_SEND_COMMAND are set, wallet.py calls those commands with JSON
# on stdin and expects JSON on stdout. This lets the Python app remain small
# while the actual Nimiq signing implementation can live in the official
# JS/Rust tooling.
SPOT_DEPOSIT_KEY_VERSION = 1
# Nimiq's wallet guide shows account-style HD paths such as
# m/44'/242'/0'/0' and m/44'/242'/1'/0'. NimHunt therefore uses
# the Spot deposit-key index as the account segment and keeps the final
# change segment hardened.
SPOT_DEPOSIT_KEY_PATH_TEMPLATE = "m/44'/242'/{index}'/0'"
NIMHUNT_MASTER_SEED_ENV = "NIMHUNT_MASTER_SEED_ENC"
NIMHUNT_MASTER_SEED_SECRET_ENV = "NIMHUNT_MASTER_SEED_SECRET"
NIMHUNT_DEV_MASTER_SEED_ENV = "NIMHUNT_DEV_MASTER_SEED"
NIMHUNT_NIMIQ_MNEMONIC_ENV = "NIMHUNT_NIMIQ_MNEMONIC"
NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV = "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC"
NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV = "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND"
NIMHUNT_NIMIQ_SEND_COMMAND_ENV = "NIMHUNT_NIMIQ_SEND_COMMAND"
NIMHUNT_NIMIQ_EXTERNAL_SIGNER_ENV = "NIMHUNT_NIMIQ_EXTERNAL_SIGNER"

# Development fallbacks. Placeholder addresses are automatically disabled in
# every public deployment. Fake sends remain opt-in even during development.
ALLOW_DEV_WALLET_PLACEHOLDERS = TEST_FEATURES_ENABLED
ALLOW_DEV_WALLET_SENDS = False
PLACEHOLDER_SPOT_DEPOSIT_ADDRESS_PREFIX = "NQ00 NIMHUNT DEV SPOT DEPOSIT"
PLACEHOLDER_SEND_TX_HASH_PREFIX = "devsend"
DEV_PRIZEDRAW_PAYOUT_ADDRESS_TEMPLATE = "NQ00 NIMHUNT DEV CLAIM PAYOUT USER {user_id}"

# Claim payout addresses are collected from Nimiq Pay when the user starts a
# claim. They are deliberately stored on the CLAIM, because the server must be
# able to pay later without the user being online.
CLAIM_PAYOUT_ADDRESS_MAX_CHARS = 160

# Settlement loop. This only decides app-level outcomes such as completed
# Prizedraws. Chain-facing sends remain routed through trans_updater.py.
SETTLEMENT_INTERVAL_SECONDS = 15
MAX_SETTLEMENTS_PER_RUN = 50
MAX_DURATION_CLAIMS_PER_RUN = 200

# Transaction loop. This checks pending Nimiq transaction hashes and moves them
# from pending to confirmed/failed when the chain/RPC result is known.
TRANSACTION_CHECK_INTERVAL_SECONDS = 60
# Nimiq transactions expire after a 120-block validity window. We use a much
# longer wall-clock grace period before treating a user-submitted hash as absent,
# and require a successful recipient-history check before releasing the draft.
USER_DEPOSIT_STALE_AFTER_SECONDS = _env_int("NIMHUNT_USER_DEPOSIT_STALE_AFTER_SECONDS", 30 * 60)
# A large block-height gap is a practical indication that Nimiq Pay and the
# server RPC are connected to different networks or one side is badly stale.
NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE = _env_int("NIMHUNT_NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE", 120)
# The transaction refresher keeps a recent RPC height in memory. Deposit dialogs
# use this validated value instead of making a rate-limited public RPC request on
# every click. A five-minute age still distinguishes TestAlbatross from mainnet
# while tolerating a brief public-node outage.
NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS = _env_int(
    "NIMHUNT_NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS",
    5 * 60,
)

# Duration-claim location verification. The browser must keep sending location
# heartbeats while a duration-based CLAIM is pending. GPS accuracy is used as
# a mercy margin, but it is capped so very vague readings cannot bless a user
# who is obviously outside the Spot.
CLAIM_LOCATION_CHECK_INTERVAL_SECONDS = 60
CLAIM_LOCATION_STALE_AFTER_SECONDS = 3 * 60
CLAIM_LOCATION_MAX_ACCURACY_MARGIN_METRES = 50
CLAIM_LOCATION_SOFT_OUTSIDE_MARGIN_METRES = 25
CLAIM_LOCATION_SOFT_PENALTY = 0.10
CLAIM_LOCATION_HARD_PENALTY = 0.35

# Claim-location spoof safeguards. All distances are measured after subtracting
# both Spot radii, giving the claimant the shortest plausible journey. The hard
# rule is reserved for extreme travel; the softer rule starts a global claim
# cooldown and uses a calculated retry time instead of immediately banning.
CLAIM_LOCATION_RECENT_CLAIM_LIMIT = 5
CLAIM_LOCATION_HARD_BAN_MIN_DISTANCE_METRES = 3_000
CLAIM_LOCATION_HARD_BAN_MAX_SPEED_METRES_PER_SECOND = 500
CLAIM_LOCATION_SOFT_COOLDOWN_MIN_DISTANCE_METRES = 1_000
CLAIM_LOCATION_SOFT_COOLDOWN_MAX_SPEED_METRES_PER_SECOND = 75


# -----------------------------
# USER Statuses
# -----------------------------

# Normal user. Can create Spots and make claims.
USER_STATUS_ACTIVE = 1

# Restricted user. Can make claims, but cannot create new Spots.
USER_STATUS_LIMITED = 2

# Banned user. Cannot create Spots or make claims.
USER_STATUS_BANNED = 3



# -----------------------------
# SPOT Statuses
# -----------------------------

# Draft SPOT. Created, but not visible or claimable yet.
SPOT_STATUS_DRAFT = 0

# Published SPOT. Visible and claimable if all other conditions are met.
SPOT_STATUS_PUBLISHED = 1

# Completed SPOT. Finished because all claims were used or the drop ended cleanly.
SPOT_STATUS_COMPLETED = 2

# Cancelled SPOT. Stopped by the creator before completion.
SPOT_STATUS_CANCELLED = 3

# Banned SPOT. Removed/blocked by moderation.
SPOT_STATUS_BANNED = 4



# -----------------------------
# CLAIM Statuses
# -----------------------------

# CLAIM has been created, but has not yet succeeded or failed.
CLAIM_STATUS_PENDING = 0

# CLAIM succeeded.
CLAIM_STATUS_SUCCESS = 1

# CLAIM failed.
CLAIM_STATUS_FAILED = 2



# -----------------------------
# TRANSACTION Types
# -----------------------------

# Creator fills/funds a SPOT.
TRANS_TYPE_FILL_SPOT = 10

# Creator cancels a SPOT and receives a refund or related movement.
TRANS_TYPE_CANCEL_SPOT = 11

# Recipient receives a successful CLAIM payout.
TRANS_TYPE_CLAIM = 20

# Platform fee transaction.
TRANS_TYPE_PLAT_FEE = 30

# One-time fee charged after a Spot's full funding target confirms.
TRANS_TYPE_CREATION_FEE = 31



# -----------------------------
# TRANSACTION Statuses
# -----------------------------

# Transaction has been created but not confirmed/finalised yet.
TRANS_STATUS_PENDING = 0

# Transaction has been confirmed.
TRANS_STATUS_CONFIRMED = 1

# Transaction failed.
TRANS_STATUS_FAILED = 2



# -----------------------------
# REPORT Statuses
# -----------------------------

# REPORT has not yet been reviewed.
REPORT_STATUS_PENDING = 0

# REPORT was accepted/approved by moderation.
REPORT_STATUS_APPROVED = 1

# REPORT was dismissed by moderation.
REPORT_STATUS_DISMISSED = 2


# -----------------------------
# REPORT Reasons
# -----------------------------

# These reason codes are stored in REPORT.reason. The visible wording lives in
# static/interface_text.js so it remains easy to adjust without touching the
# database layer.
REPORT_REASON_SPAM = 10
REPORT_REASON_INAPPROPRIATE = 20
REPORT_REASON_FALSE_LOCATION = 30
REPORT_REASON_SCAM = 40
REPORT_REASON_OTHER = 90

REPORT_REASON_VALUES = {
    REPORT_REASON_SPAM,
    REPORT_REASON_INAPPROPRIATE,
    REPORT_REASON_FALSE_LOCATION,
    REPORT_REASON_SCAM,
    REPORT_REASON_OTHER,
}

REPORT_DETAILS_MAX_CHARS = 300

# Claim modal captcha bounds. Kept with other human-friction constants so the
# frontend and backend can stay aligned if the challenge is tuned later.
CLAIM_CAPTCHA_MIN = 1
CLAIM_CAPTCHA_MAX = 9
