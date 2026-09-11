# Configuration and deployment

This is the operator-facing reference for NimHunt configuration. `constants.py`
and the modules named below remain the implementation source of truth. Most
installations should set only the deployment, storage, network, signer, fee and
IP-geolocation variables in the first tables; the later policy knobs exist for
careful operational tuning, not as a suggested checklist.

NimHunt reads process environment variables at import/startup and does **not**
load `.env` automatically. Never commit mnemonics, passphrases, OAuth credentials,
administrator hashes or database copies.

## Deployment modes

| `NIMHUNT_DEPLOYMENT_MODE` | Network requirement | Local test features | Intended use |
|---|---|---:|---|
| `development` (default) | normally TestAlbatross | enabled | local development |
| `public-testnet` | TestAlbatross, network ID `5` | disabled | public testing with test NIM |
| `production` | MainAlbatross, network ID `24` | disabled | real-NIM public service |

`NIMHUNT_PRODUCTION` is a deprecated compatibility flag. When the preferred mode
is absent, a true value selects `production`; when both are set they must agree.
New deployments should set only `NIMHUNT_DEPLOYMENT_MODE`.

Both public modes require HTTPS RPC/Hub endpoints, a persistent absolute database
path, signer commands, signer key access, an operator-controlled fee address and
an HTTPS IP-geolocation URL containing `{ip}`. Startup verifies the live RPC
network. Production and public TestAlbatross databases are bound durably to their
network and mode and cannot be reused across modes.

## Core application and public endpoints

| Variable | Default | Unit / purpose |
|---|---|---|
| `NIMHUNT_DEPLOYMENT_MODE` | `development` | `development`, `public-testnet`, or `production` |
| `NIMHUNT_DB_PATH` | `records.db` | SQLite path; an absolute persistent path is required publicly |
| `NIMHUNT_MAX_HTTP_REQUEST_BODY_BYTES` | `65536` | bytes; outer request-body limit |
| `NIMHUNT_PUBLIC_BASE_URL` | `https://nimhunt.app` | canonical absolute HTTP(S) base URL for social metadata |
| `NIMHUNT_IP_GEOLOCATION_URL` | empty | provider URL template containing `{ip}`; required and HTTPS publicly |
| `NIMHUNT_IP_GEOLOCATION_API_KEY` | empty | optional provider bearer token |
| `NIMHUNT_IP_GEOLOCATION_TIMEOUT_SECONDS` | `4` | seconds per provider request |
| `NIMHUNT_ADMIN_PASSWORD_HASH` | empty | optional `scrypt$...` admin hash; empty disables admin login |

`NIMHUNT_HOST`, `NIMHUNT_PORT` and `NIMHUNT_PROJECT_DIR` are conveniences read
only by the local shell scripts (defaults `0.0.0.0`, `8000` and the repository
directory). Railway supplies its standard `PORT` variable to `railway_start.py`.

## Nimiq network and signing

| Variable | Default | Unit / purpose |
|---|---|---|
| `NIMHUNT_NIMIQ_NETWORK` | `TestAlbatross` | `TestAlbatross`, `MainAlbatross`, or `DevAlbatross` |
| `NIMHUNT_NIMIQ_NETWORK_ID` | selected network ID | protocol ID: `5`, `24`, or `6` respectively |
| `NIMHUNT_NIMIQ_RPC_URL` | network-specific | TestAlbatross: `https://rpc.testnet.nimiqwatch.com/`; MainAlbatross: `https://rpc.nimiqwatch.com`; DevAlbatross: empty |
| `NIMHUNT_NIMIQ_HUB_URL` | network-specific | testnet Hub except MainAlbatross, which uses `https://hub.nimiq.com` |
| `NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS` | `12` | seconds per server RPC request |
| `NIMHUNT_NIMIQ_MNEMONIC` | empty | private mnemonic used by the bundled helper |
| `NIMHUNT_NIMIQ_MNEMONIC_PASSWORD` | empty | optional BIP39 passphrase for that mnemonic |
| `NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND` | automatic only in development | JSON stdin/stdout address-derivation command; required publicly |
| `NIMHUNT_NIMIQ_SEND_COMMAND` | automatic only in development | JSON stdin/stdout signing/broadcast command; required publicly |
| `NIMHUNT_NIMIQ_EXTERNAL_SIGNER` | false | assert that custom commands manage their own key when no mnemonic is supplied |
| `NIMHUNT_NIMIQ_HELPER_PATH` | `helpers/nimiq_helper.mjs` | bundled helper override used by transaction sending |
| `NIMHUNT_NIMIQ_NODE_BINARY` | `node` | Node executable for signature verification/helper use |
| `NIMHUNT_NIMIQ_TRANSACTION_FEE` | `0` | Luna; must remain `0` publicly because Spot targets do not reserve network fees |
| `NIMHUNT_NIMIQ_ADDRESS_TX_LOOKUP_LIMIT` | `500` | transactions inspected during fallback address-history proof |
| `NIMHUNT_NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE` | `120` | blocks tolerated between Nimiq Pay and server provider |
| `NIMHUNT_NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS` | `300` | seconds; cached server chain-head age |
| `NIMHUNT_USER_DEPOSIT_STALE_AFTER_SECONDS` | `1800` | seconds before an unseen submitted deposit may be resolved conservatively |

The older Python-native seed path (`NIMHUNT_MASTER_SEED_ENC` plus
`NIMHUNT_MASTER_SEED_SECRET`) and development-only `NIMHUNT_DEV_MASTER_SEED`
remain supported compatibility paths. Prefer the pinned Nimiq helper or a
key-managed external signer. `NIMHUNT_DEV_MASTER_SEED` is rejected publicly.

## Fees

All amount variables in this table are decimal **NIM**, not Luna. They accept no
more than five decimal places and are snapshotted where the financial lifecycle
requires it.

| Variable | Default | Purpose |
|---|---:|---|
| `NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM` | `200` | Standard Spot creation fee |
| `NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM` | `200` | Prizedraw creation fee |
| `NIMHUNT_SPOT_CANCELLATION_FEE_NIM` | `500` | creator-cancellation fee |
| `NIMHUNT_SPOT_FEE_ADDRESS` | development test address | shared operator destination; a valid non-development address is required publicly |

`NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS` is a deprecated alias for the shared fee
address. If both address names are set, they must normalise to the same address.

## Transaction reconciliation

| Variable | Default | Unit / purpose |
|---|---:|---|
| `NIMHUNT_TRANSACTION_CHECK_INTERVAL_SECONDS` | `60` | seconds between passes |
| `NIMHUNT_TRANS_FAIL_AFTER_SECONDS` | `5400` | seconds before an unseen hash can be treated as failed |
| `NIMHUNT_TRANS_MAX_CHECKS_PER_RUN` | `100` | pending transactions per pass |

Do not shorten the failure window casually. An unseen transaction may already
have been broadcast; retaining ambiguous intent prevents a blind duplicate send.

## Optional X announcements

The worker is disabled by default and is hard-gated to `production` on
MainAlbatross network ID `24`. The credentials determine the posting account;
the configured handle is checked against the authenticated X user before posting.

| Variable | Default | Unit / purpose |
|---|---:|---|
| `NIMHUNT_X_AUTO_POST_ENABLED` | false | strict boolean master switch |
| `NIMHUNT_X_ACCOUNT_HANDLE` | empty | expected account username, with or without `@` |
| `NIMHUNT_X_API_KEY` | empty | OAuth consumer/API key |
| `NIMHUNT_X_API_SECRET` | empty | OAuth consumer/API secret |
| `NIMHUNT_X_ACCESS_TOKEN` | empty | OAuth user access token |
| `NIMHUNT_X_ACCESS_TOKEN_SECRET` | empty | OAuth user access-token secret |
| `NIMHUNT_X_POST_INTERVAL_SECONDS` | `30` | seconds between worker passes |
| `NIMHUNT_X_HTTP_TIMEOUT_SECONDS` | `10` | seconds per X request |
| `NIMHUNT_X_RETRY_AFTER_SECONDS` | `900` | seconds after an authoritative retryable response |
| `NIMHUNT_X_MAX_SPOTS_PER_RUN` | `10` | announcements/retries considered per pass |

## Social-card rendering

| Variable | Default | Unit / purpose |
|---|---|---|
| `NIMHUNT_SOCIAL_CARD_CACHE_DIR` | `/tmp/nimhunt-social-cards` | generated-card cache directory |
| `NIMHUNT_SOCIAL_TILE_CACHE_DIR` | `/tmp/nimhunt-social-tiles` | downloaded map-tile cache directory |
| `NIMHUNT_SOCIAL_MAP_TILE_URL` | OpenStreetMap tile URL | tile URL template with `{z}`, `{x}`, `{y}` |
| `NIMHUNT_SOCIAL_TILE_USER_AGENT` | NimHunt default | outbound tile request user agent |
| `NIMHUNT_SOCIAL_TILE_TIMEOUT_SECONDS` | `2` | seconds, with an enforced minimum of `0.5` |
| `NIMHUNT_SOCIAL_TILE_WORKERS` | `4` | tile download threads, minimum `1` |

These caches are generated runtime data and do not belong in Git. A platform with
an ephemeral `/tmp` may regenerate them after a deployment.

## Advanced claim and abuse-policy settings

These defaults implement security and financial policy. Changing them can alter
claim availability or value exposure even though they are configuration rather
than source code. Review [`SECURITY.md`](../SECURITY.md) and
[`security-architecture.md`](security-architecture.md), use a separate deployment
change, and test state transitions before overriding them.

### Wallet sessions and signed claim authorization

| Variable | Default | Unit / purpose |
|---|---:|---|
| `NIMHUNT_CLAIM_AUTH_CHALLENGE_TTL_SECONDS` | `300` | challenge lifetime, seconds |
| `NIMHUNT_CLAIM_AUTH_SESSION_TTL_SECONDS` | `2592000` | wallet-session lifetime, seconds |
| `NIMHUNT_CLAIM_AUTHORIZATION_TTL_SECONDS` | `90` | claim authorization lifetime, seconds |
| `NIMHUNT_CLAIM_AUTHORIZATION_VERIFY_GRACE_SECONDS` | `300` | worker recovery grace, seconds |
| `NIMHUNT_CLAIM_AUTHORIZATION_CLEANUP_BATCH` | `64` | rows reclaimed during issuance |
| `NIMHUNT_CLAIM_AUTHORIZATION_RATE_WINDOW_SECONDS` | `600` | issuance window, seconds |
| `NIMHUNT_CLAIM_AUTHORIZATION_RATE_PER_DEVICE` | `8` | authorizations per device/window |
| `NIMHUNT_CLAIM_AUTHORIZATION_RATE_PER_WALLET` | `16` | authorizations per wallet/window |
| `NIMHUNT_CLAIM_AUTH_RATE_WINDOW_SECONDS` | `600` | wallet challenge window, seconds |
| `NIMHUNT_CLAIM_AUTH_RATE_LIMIT_PER_IP` | `64` | wallet challenges per source/window |
| `NIMHUNT_CLAIM_AUTH_RATE_LIMIT_PER_DEVICE` | `5` | wallet challenges per device/window |
| `NIMHUNT_CLAIM_AUTH_VERIFY_RATE_LIMIT_PER_IP` | `64` | signature verifications per source/window |
| `NIMHUNT_CLAIM_AUTH_VERIFY_RATE_LIMIT_PER_DEVICE` | `8` | signature verifications per device/window |

### Registration, evidence and restriction state

| Variable | Default | Unit / purpose |
|---|---:|---|
| `NIMHUNT_USER_REGISTRATION_SOURCE_BURST_WINDOW_SECONDS` | `600` | source burst window, seconds |
| `NIMHUNT_USER_REGISTRATION_SOURCE_BURST_LIMIT` | `40` | new USERs per source/burst |
| `NIMHUNT_USER_REGISTRATION_SOURCE_HOURLY_LIMIT` | `120` | new USERs per source/hour |
| `NIMHUNT_USER_REGISTRATION_SOURCE_DAILY_LIMIT` | `300` | new USERs per source/rolling day |
| `NIMHUNT_USER_REGISTRATION_GLOBAL_HOURLY_LIMIT` | `500` | new USERs globally/hour |
| `NIMHUNT_CLAIM_IDENTITY_TRUST_AGE_SECONDS` | `2592000` | signer-history age considered established |
| `NIMHUNT_CLAIM_SIGNER_HISTORY_PAGE_SIZE` | `500` | history records per RPC page |
| `NIMHUNT_CLAIM_SIGNER_HISTORY_MAX_PAGES` | `100` | maximum signer-history pages |
| `NIMHUNT_CLAIM_SIGNER_HISTORY_NEGATIVE_CACHE_SECONDS` | `86400` | negative-history cache lifetime |
| `NIMHUNT_CLAIM_SIGNER_HISTORY_FAILURE_RETRY_SECONDS` | `600` | unknown-history retry delay |
| `NIMHUNT_CLAIM_FIRST_LOCATION_MISMATCH_METRES` | `1500000` | first GPS/network mismatch distance |
| `NIMHUNT_CLAIM_FIRST_LOCATION_COOLDOWN_SECONDS` | `86400` | first mismatch temporary restriction |
| `NIMHUNT_CLAIM_FIRST_LOCATION_SECOND_COOLDOWN_SECONDS` | `604800` | repeated mismatch restriction |
| `NIMHUNT_CLAIM_FIRST_LOCATION_UNKNOWN_RETRY_SECONDS` | `900` | unknown IP-location retry delay |
| `NIMHUNT_CLAIM_SAME_IP_GPS_MIN_DISTANCE_METRES` | `100000` | same-source suspicious jump distance |
| `NIMHUNT_CLAIM_SAME_IP_GPS_MAX_SPEED_METRES_PER_SECOND` | `75` | same-source jump speed threshold |
| `NIMHUNT_CLAIM_BEHAVIOURAL_RESTRICTION_SECONDS` | `86400` | weak-evidence restriction duration |
| `NIMHUNT_CLAIM_GPS_ANCHOR_REFRESH_SECONDS` | `3600` | stable GPS anchor refresh interval |
| `NIMHUNT_CLAIM_GPS_ANCHOR_MIN_MOVEMENT_METRES` | `1000` | movement required to replace anchor |
| `NIMHUNT_CLAIM_FUNDING_HISTORY_PAGE_SIZE` | `100` | funding-history records per RPC page |
| `NIMHUNT_CLAIM_FUNDING_HISTORY_MAX_PAGES` | `4` | maximum funding-history pages |
| `NIMHUNT_CLAIM_FUNDING_SOURCE_SAMPLE_SIZE` | `100` | sampled funding-source transactions |
| `NIMHUNT_CLAIM_FUNDING_SOURCE_SERVICE_DEGREE` | `50` | claimant degree treated as service-like |
| `NIMHUNT_CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS` | `5` | claimant threshold for corroboration |
| `NIMHUNT_CLAIM_FUNDING_CLUSTER_WINDOW_SECONDS` | `604800` | funding-cluster observation window |
| `NIMHUNT_CLAIM_FUNDING_CLUSTER_MEMBER_LIMIT` | `16` | retained members per source |
| `NIMHUNT_CLAIM_FUNDING_CACHE_SECONDS` | `2592000` | stable funding observation lifetime |

### Burst, travel and audit state

| Variable | Default | Unit / purpose |
|---|---:|---|
| `NIMHUNT_CLAIM_PAYOUT_SECURITY_HOLD_SECONDS` | `300` | initial payout hold, seconds; composition may raise the effective default for burst observation |
| `NIMHUNT_CLAIM_SECURITY_EVENT_RETENTION_SECONDS` | `86400` | recent-event retention |
| `NIMHUNT_CLAIM_SECURITY_MAX_RECENT_EVENTS` | `200` | bounded recent-event count |
| `NIMHUNT_CLAIM_SECURITY_TRAVEL_MIN_METRES` | `1000` | strong travel minimum distance |
| `NIMHUNT_CLAIM_SECURITY_TRAVEL_MAX_MPS` | `75` | strong travel maximum metres/second (legacy suffix retained) |
| `NIMHUNT_CLAIM_SECURITY_IP_TRAVEL_MIN_METRES` | `20000` | network travel minimum distance |
| `NIMHUNT_CLAIM_SECURITY_IP_TRAVEL_MAX_MPS` | `200` | network travel maximum metres/second (legacy suffix retained) |
| `NIMHUNT_CLAIM_SECURITY_WALLET_HOURLY_LIMIT` | `20` | claims per wallet/hour |
| `NIMHUNT_CLAIM_SECURITY_BURST_WINDOW_SECONDS` | `600` | concentrated burst window |
| `NIMHUNT_CLAIM_SECURITY_BURST_MIN_IDENTITIES` | `4` | identities needed for concentrated burst evidence |
| `NIMHUNT_CLAIM_SECURITY_BURST_MIN_SPREAD_METRES` | `50000` | prior-location spread threshold |
| `NIMHUNT_CLAIM_SECURITY_BURST_CENTRE_TOLERANCE_METRES` | `5` | centre-coordinate tolerance |
| `NIMHUNT_CLAIM_SECURITY_NEW_IDENTITY_MAX_AGE_SECONDS` | `3600` | new-identity age boundary |
| `NIMHUNT_CLAIM_SECURITY_BROAD_BURST_WINDOW_SECONDS` | `900` | broad sweep window |
| `NIMHUNT_CLAIM_SECURITY_BROAD_BURST_MIN_IDENTITIES` | `5` | identities needed for broad evidence |
| `NIMHUNT_CLAIM_SECURITY_BROAD_BURST_MAX_TARGET_SPOTS` | `4` | maximum target Spots for broad evidence |
| `NIMHUNT_CLAIM_SECURITY_BROAD_BURST_MIN_SPREAD_METRES` | `50000` | broad prior-location spread |

### Automatic payout exposure

| Variable | Default | Unit / purpose |
|---|---:|---|
| `NIMHUNT_CLAIM_PAYOUT_THROTTLE_WINDOW_SECONDS` | `600` | global rolling window |
| `NIMHUNT_CLAIM_PAYOUT_THROTTLE_MAX_COUNT` | `8` | payouts per rolling window |
| `NIMHUNT_CLAIM_PAYOUT_THROTTLE_MAX_NIM` | `10000` | NIM per rolling window |
| `NIMHUNT_CLAIM_PAYOUT_DAILY_WINDOW_SECONDS` | `86400` | long rolling window |
| `NIMHUNT_CLAIM_PAYOUT_DAILY_MAX_COUNT` | `100` | payouts per long window |
| `NIMHUNT_CLAIM_PAYOUT_DAILY_MAX_NIM` | `50000` | NIM per long window |
| `NIMHUNT_CLAIM_MAX_AUTOMATIC_PAYOUT_NIM` | `10000` | NIM absolute per-payout ceiling |
| `NIMHUNT_OPEN_SPOT_PAYOUT_WINDOW_SECONDS` | `86400` | per-Open-Spot rolling window |
| `NIMHUNT_OPEN_SPOT_PAYOUT_MAX_COUNT` | `10` | automatic payouts per Open Spot/window |
| `NIMHUNT_OPEN_SPOT_PAYOUT_MAX_NIM` | `5000` | automatic NIM per Open Spot/window |
| `NIMHUNT_OPEN_SPOT_LIFETIME_AUTOMATIC_PERCENT` | `50` | percent of Open Spot capacity/value automatically payable; `0` holds all |

## Public deployment examples

Use separate keys and databases for TestAlbatross and MainAlbatross. The examples
show required core values without real credentials:

```bash
# Public TestAlbatross
export NIMHUNT_DEPLOYMENT_MODE=public-testnet
export NIMHUNT_DB_PATH=/srv/nimhunt-testnet/records.db
export NIMHUNT_NIMIQ_NETWORK=TestAlbatross
export NIMHUNT_NIMIQ_NETWORK_ID=5
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.testnet.nimiqwatch.com/
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq-testnet.com
export NIMHUNT_NIMIQ_MNEMONIC='private testnet mnemonic from secret storage'
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_SPOT_FEE_ADDRESS='operator-controlled TestAlbatross NQ address'
export NIMHUNT_IP_GEOLOCATION_URL='https://provider.example/location/{ip}'
```

```bash
# MainAlbatross production
export NIMHUNT_DEPLOYMENT_MODE=production
export NIMHUNT_DB_PATH=/srv/nimhunt-mainnet/records.db
export NIMHUNT_NIMIQ_NETWORK=MainAlbatross
export NIMHUNT_NIMIQ_NETWORK_ID=24
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.nimiqwatch.com
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq.com
export NIMHUNT_NIMIQ_MNEMONIC='private mainnet mnemonic from secret storage'
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_SPOT_FEE_ADDRESS='operator-controlled MainAlbatross NQ address'
export NIMHUNT_IP_GEOLOCATION_URL='https://provider.example/location/{ip}'
```

For a key-managed external signer, omit the mnemonic, set
`NIMHUNT_NIMIQ_EXTERNAL_SIGNER=1`, and point both command variables at the
operator's compatible signer integration.

Start a non-Railway public service with one worker behind HTTPS:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Railway

`railway.json` installs the pinned Python and Node dependencies and runs
`railway_start.py` with one worker. `mise.toml` selects Python 3.11 and Node 20.
For each network:

1. create one Railway service and exactly one replica;
2. attach a persistent volume at `/data` and set `NIMHUNT_DB_PATH=/data/records.db`;
3. use the matching public example above, changing helper paths to
   `/app/helpers/nimiq_helper.mjs`;
4. store private values as Railway secrets;
5. leave Railway as the only public ingress for the supplied `100.0.0.0/8`
   trusted-proxy configuration;
6. disable sleeping and deployment overlap; and
7. configure and test volume backups.

The Railway entry point permits startup during temporary RPC unavailability, but
chain work remains unavailable. It verifies a recovered RPC's network before any
normal RPC call or recorded send resumes. A wrong or unverifiable network still
fails closed.

## Network cutover and launch checklist

Mainnet is a new environment and data volume, not a conversion of testnet state.
Finish or manually resolve pending testnet work, back up the complete SQLite
database and sidecars consistently, retain it as a testnet archive, and start
MainAlbatross with a fresh persistent database and separate signing material.
Never copy testnet Spots, claims, deposits or transaction hashes into mainnet.

Before accepting meaningful funds:

1. install from `requirements.txt`, `requirements-dev.txt` where checks are run,
   and `helpers/package-lock.json`;
2. verify key recovery without exposing the secret;
3. check deployment mode, network, ID, RPC, Hub, fee address and IP provider;
4. enable administrator access only with a generated hash from
   `python scripts/hash_admin_password.py`;
5. serve over HTTPS with one worker and one replica;
6. verify `/healthz`, administrator state and external monitoring;
7. test persistent-volume backup and restoration; and
8. complete deliberately small funding, claim, Prizedraw, expiry/refund and
   creator-cancellation exercises before increasing values.

Do not use a funded administrator Spot ban as a routine smoke test. See
[`ADMIN.md`](../ADMIN.md) for that severe money-moving operation.
