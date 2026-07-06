"""
─────────────────────────────────────────────

constants.py

Shared constants for NimHunt.

─────────────────────────────────────────────
"""


# -----------------------------
# APP Settings
# -----------------------------

# Change this once if the app is ever renamed.
APP_NAME = "NimHunt"

# Browser/tab icon. The actual SVG lives in static/favicon.svg.
APP_ICON_PATH = "/favicon.ico"

# Public link for installing/opening Nimiq Pay.
# Used by homepage copy when the mini app is opened outside Nimiq Pay.
NIMIQ_PAY_URL = "https://nimpay.app"

# -----------------------------
# Development / test settings
# -----------------------------

# Development helper: when True, the home/session API can return TEST_USER_ID
# if the webview is opened outside Nimiq Pay and no device hash is available.
# Set this to False before any public/production deployment.
DEFAULT_TO_TEST_USER = True

# Mock desktop user created by spoof.py. SQLite allows an explicit INTEGER
# PRIMARY KEY value of 0, so this is safe for the test dataset.
TEST_USER_ID = 0

# Display-name validation. Kept in constants so frontend and backend use the
# same simple rule, and so the rule can be expanded later in one place.
DISPLAY_NAME_MIN_CHARS = 3
DISPLAY_NAME_MAX_CHARS = 18

# Spot title validation. A draft SPOT can be created with only a title;
# the remaining fields are filled on the full Create Spot form.
SPOT_TITLE_MIN_CHARS = 3
SPOT_TITLE_MAX_CHARS = DISPLAY_NAME_MAX_CHARS

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

# Future page URLs. These are centralised even before the pages exist, so copy
# and links remain easy to change later.
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
DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS = 7 * 24 * 60 * 60

# Per-user claim bounds. 0 means unlimited claims per user.
MIN_SPOT_MAX_CLAIMS_PER_USER = 0
MAX_SPOT_MAX_CLAIMS_PER_USER = 10

# Total participant/claim bounds. 0 means unlimited total participants, which is
# only valid for Prizedraw spots. Standard spots must use at least 1.
MIN_SPOT_MAX_TOTAL_CLAIMS = 1
MIN_PRIZEDRAW_MAX_TOTAL_CLAIMS = 0
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

# Cancellation rules. Values are stored in Luna. Frontend display should convert
# to NIM only at the UI boundary. The fee address is a development placeholder
# until a real pooled-fee Nimiq address is configured.
SPOT_CANCELLATION_FEE = 1 * LUNA_PER_NIM
SPOT_CANCELLATION_FEE_ADDRESS = "NQ00 NIMHUNT DEV CANCELLATION FEE POOL"

# Draft SPOT defaults used when a creator has only entered the initial title.
# These keep the row valid while the full Create Spot form is still incomplete.
DEFAULT_DRAFT_SPOT_RADIUS_METRES = MIN_SPOT_RADIUS_METRES
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
# Network names match the Nimiq Web Client configuration names. TestAlbatross
# should be used while developing. MainAlbatross is the production network.
NIMIQ_NETWORK = "TestAlbatross"
NIMIQ_NETWORK_ID = 6
NIMIQ_RPC_URL = "https://rpc.nimiqwatch.com"
NIMIQ_RPC_TIMEOUT_SECONDS = 12
NIMIQ_TRANSACTION_FEE = 0

# Nimiq Pay / Hub endpoints used by browser-side user deposits. The server does
# not drive the user's Pay app directly; it returns deposit intents and records
# the tx hash after Pay confirms the transaction.
NIMIQ_HUB_URL = "https://hub.nimiq-testnet.com"
NIMIQ_PAY_PROVIDER = "nimiq-pay-mini-app-sdk"

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
NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV = "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND"
NIMHUNT_NIMIQ_SEND_COMMAND_ENV = "NIMHUNT_NIMIQ_SEND_COMMAND"
NIMHUNT_NIMIQ_CONFIRM_COMMAND_ENV = "NIMHUNT_NIMIQ_CONFIRM_COMMAND"

# Development fallbacks. Disable these before production. Placeholder addresses
# and fake tx hashes are only for local UI/backend flow testing.
ALLOW_DEV_WALLET_PLACEHOLDERS = DEFAULT_TO_TEST_USER
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
SETTLEMENT_INTERVAL_SECONDS = 60
MAX_SETTLEMENTS_PER_RUN = 50
MAX_DURATION_CLAIMS_PER_RUN = 200

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


# -----------------------------
# USER Statuses
# -----------------------------

# Normal user. Can create SPOTs and CLAIM caches.
USER_STATUS_ACTIVE = 1

# Restricted user. Can CLAIM caches, but cannot create new SPOTs.
USER_STATUS_LIMITED = 2

# Banned user. Cannot create SPOTs or CLAIM caches.
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
