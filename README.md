# NimHunt

![CI](https://github.com/NIMHunt/nim-hunt/actions/workflows/ci.yml/badge.svg)

NimHunt is a small geofaucet-style and Prizedraw mini-app for Nimiq Pay.

Creators fund geographic **Spots** with NIM. Other users find those Spots on a
map and can claim or enter them only when they are within the configured radius.
NimHunt is designed for a coding competition and modest community use rather
than high-volume financial infrastructure.

## Features

- **Geographic Spots** — choose a real-world location and claim radius.
- **Standard rewards** — divide a funded pool across a finite number of claims.
- **Prizedraws** — collect entries and pay one or more randomly selected winners.
- **Password-protected Spots** — generate one claim code per available participant.
- **Stay durations** — require a claimant to remain inside the radius for a set time.
- **Scheduling** — configure a future start and a fixed active duration.
- **Participation limits** — set total participants and per-user limits.
- **Creation fees** — charge separate configurable fees for Standard Spots and Prizedraws.
- **Creator tools** — inspect drafts, deposits, publishing state, claim codes and history.
- **Claim history** — users can review pending, successful and failed claims.
- **On-chain descriptions** — NimHunt-generated transactions include short Spot labels.
- **Optional X announcements** — automatically announce newly-active Spots through a configured account.
- **Deployment/network separation** — desktop shortcuts remain available locally,
  while public TestAlbatross and MainAlbatross deployments both use production-grade guards.
- **Localisation-ready UI** — interface copy is centralised and selected from
  `window.nimiqPay.language`; English is currently the only bundled language.

## How NimHunt identifies users

Inside Nimiq Pay, the mini-app SDK provides a stable device identifier. NimHunt
hashes and stores that identifier as the account identity. It does not use a
traditional username/password login.

For local development outside Nimiq Pay, NimHunt can fall back to the seeded
**Desktop User**. This and the Find Spots **Test Location** control are development
features only. They are not rendered or accepted in either `public-testnet` or
`production` deployment mode.

Display names and all Spot titles/descriptions are user-generated content. The
localisation framework translates only NimHunt's own marked interface text; it
never attempts to translate user-generated information.

## Architecture

NimHunt intentionally uses a small stack:

- **FastAPI** serves pages and JSON APIs.
- **Jinja** renders the page shells.
- **Static JavaScript and CSS** provide maps, forms and Nimiq Pay interactions.
- **Leaflet/OpenStreetMap** display Spot locations.
- **SQLite/aiosqlite** store users, Spots, claims, reports and transaction state.
- **`helpers/nimiq_helper.mjs`** uses the official pinned `@nimiq/core` package
  to derive addresses and sign outgoing transactions, then broadcasts their
  serialized form through the configured Nimiq JSON-RPC endpoint.
- **Background services** refresh caches, settle completed Prizedraws, reconcile
  pending blockchain transactions and optionally announce newly-active Spots on X.

The important chain-facing modules are:

- `wallet.py` — validates addresses and calls the configured address/signing helper.
- `trans_updater.py` — records payment intent, broadcasts outgoing payments and
  verifies transaction finality through Nimiq RPC.
- `settlement_updater.py` — decides application-level Prizedraw outcomes.
- `helpers/nimiq_helper.mjs` — holds the bundled Nimiq key derivation and signing code.

Only `wallet.py` and `trans_updater.py` should initiate or verify chain-facing work.

## Nimiq transaction flow

### Funding a Spot

1. NimHunt derives a unique deposit address for the draft Spot.
2. The draft snapshots the configured creation-fee amount and fee destination.
   Later configuration changes therefore affect only newly-created Spots.
3. The creator opens a Nimiq Pay request for **Spot funding + creation fee** from
   the My Spots page. The card shows both components and the total being sent.
4. Nimiq Pay signs and broadcasts the creator's funding transaction.
5. NimHunt records the returned hash and verifies it independently through RPC.
6. Deposits may be made in parts. The first confirmed sender becomes that Spot's
   funding wallet, and every later top-up must come from the same wallet.
7. Only after confirmed deposits cover the full Spot value plus its snapshotted
   fee does NimHunt create a durable creation-fee transaction intent.
8. The fee is sent from the Spot deposit address to the snapshotted platform-fee
   address. The draft cannot be published until that transaction confirms.
9. Once the fee confirms, the deposit address retains the intended Spot reward
   pool and the draft may be published if its other rules are satisfied.

A creation fee of `0` skips the fee transaction entirely. A failed fee transaction
may be retried only after the chain has proved failure; an ambiguous local intent
remains pending to prevent an accidental duplicate charge.

A wrong-wallet top-up is retained in the transaction record but excluded from
usable Spot funds. It requires manual recovery; NimHunt does not silently assign
it to claimants or refund it automatically.

### Standard claims

A successful standard claim creates a durable payout intent before broadcasting.
The transaction updater sends the reward from the Spot deposit address and marks
the claim complete only after the outgoing transaction confirms.

### Prizedraws

Entries are collected until the Spot ends or reaches capacity. Settlement chooses
and stores the winners, marks non-winners complete, then creates winner payout
transactions. Winners are not considered paid until their transactions confirm.

For finite Prizedraws:

- Total Participants must be at least `2`.
- finite Claims Per User must be less than Total Participants;
- Prize Count must be less than Total Participants.

A Total Participants value of `0` means **Unlimited** and therefore does not impose
those finite comparisons.

### Cancellation

A funded draft may be cancelled instead of deleted. Published Standard Spots may
also be cancelled; published Prizedraws retain their existing no-cancellation rule.
Cancellation creates up to two outgoing transactions:

- the remaining refundable balance to the original funding wallet;
- the configured cancellation fee to the platform fee address.

If the creation fee already confirmed, it is retained and excluded from the
refund. If a draft is only partly funded, no creation fee is charged; the ordinary
cancellation fee is applied to the confirmed deposit. Once deposits reach the full
Spot-plus-fee target, the creation fee is owed: cancellation waits until that fee
confirms, so a timing race cannot be used to avoid it. Cancellation also waits while
a deposit, payout, refund or cancellation-fee transaction is pending.

A draft with no deposit history can still be deleted normally. Once any deposit
transaction has been recorded, deletion is disabled so the Spot address and audit
trail cannot disappear. A draft containing only failed deposit records can instead
be archived as cancelled without an automatic refund; those records remain attached
for manual review because a failed row may represent an abandoned hash or an
on-chain wrong-wallet payment that NimHunt deliberately excluded from usable funds.

Already-confirmed claim payouts are also deducted before the refundable balance is
calculated. The Spot is marked cancelled only after every required outgoing leg
has reached a final state.

### On-chain transaction descriptions

NimHunt includes short public transaction data:

- `Funding: [Spot name]`
- `Creation Fee: [Spot name]`
- `Claim: [Spot name]`
- `Prizedraw: [Spot name]`
- `Cancelled Spot: [Spot name]`
- `Refund Fee: [Spot name]`

Descriptions are limited to 30 UTF-8 bytes and safely truncated. Because this
information is written to the blockchain, only the already-public Spot title is
included—never claim codes, device identifiers or private account information.

## Automatic X posting

NimHunt can announce a published Spot when it first becomes active. This feature
is **disabled by default** and makes no X API requests while disabled. It is also
hard-gated to the real blockchain: the worker can run only when NimHunt is in
`production` mode on `MainAlbatross` with network ID `24`. Development,
DevAlbatross and public TestAlbatross deployments remain inert even if the master
flag is accidentally set to `true`. When first enabled on production MainAlbatross,
the worker starts from that moment rather than posting a backlog of older active
Spots.

Each generated Post contains a short announcement, the Spot title and its public
`nimhunt.app` link. NimHunt generates and caches the Spot's existing map card
before creating the Post, so X can fetch a warm preview image.

The worker uses OAuth 1.0a user-context credentials. Create an approved X developer
App with posting/Read and Write permission, then configure these server variables:

| Variable | Default | Purpose |
|---|---|---|
| `NIMHUNT_X_AUTO_POST_ENABLED` | `false` | Master switch; accepts the same strict boolean values as other NimHunt flags |
| `NIMHUNT_X_ACCOUNT_HANDLE` | empty | Expected account username, with or without `@` |
| `NIMHUNT_X_API_KEY` | empty | X developer App API/consumer key |
| `NIMHUNT_X_API_SECRET` | empty | X developer App API/consumer secret |
| `NIMHUNT_X_ACCESS_TOKEN` | empty | User Access Token for the posting account |
| `NIMHUNT_X_ACCESS_TOKEN_SECRET` | empty | User Access Token Secret for the posting account |
| `NIMHUNT_X_POST_INTERVAL_SECONDS` | `30` | How often to check for newly-active Spots |
| `NIMHUNT_X_HTTP_TIMEOUT_SECONDS` | `10` | Per-request X API timeout |
| `NIMHUNT_X_RETRY_AFTER_SECONDS` | `900` | Default delay after an authoritative retryable rejection |
| `NIMHUNT_X_MAX_SPOTS_PER_RUN` | `10` | Maximum Posts/retries considered in one worker pass |

There are six required deployment variables for eventual activation: the master
flag, account handle and four OAuth credentials. The interval, timeout, retry and
batch-size variables are optional and may be omitted to use their defaults.

The credentials—not the handle setting—determine the account that can post.
Before sending anything, NimHunt calls X's authenticated-user endpoint and refuses
to post unless its returned username matches `NIMHUNT_X_ACCOUNT_HANDLE`.
Credentials stay in environment variables and are never written to SQLite,
health output or logs.

Successful Post IDs and per-Spot delivery states are stored in the existing
`app_metadata` table, so restarts do not duplicate confirmed announcements and no
schema reset is required. Rate limits and explicit authentication rejections can
be retried safely. A timeout, lost connection or X server error is recorded as
**uncertain** and is not retried automatically, because the Post may already have
been created and a blind retry could publish it twice.

Example disabled configuration:

```bash
export NIMHUNT_X_AUTO_POST_ENABLED=0
export NIMHUNT_X_ACCOUNT_HANDLE='NimHunt'
```

Only set the flag to `1` after all four private credential variables have been
added to the deployment, the intended account has authorised the App, and the
service is running in production on MainAlbatross. The worker independently
checks all three conditions before making any X request.

## Nimiq networks

NimHunt recognises the official Albatross network names and protocol IDs:

| Network | ID | Intended use |
|---|---:|---|
| `TestAlbatross` | `5` | development and public testing with test NIM |
| `MainAlbatross` | `24` | production with real NIM |
| `DevAlbatross` | `6` | advanced local/custom-network development |

Network-specific RPC and Hub defaults are selected automatically:

| Network | Default RPC | Default Hub |
|---|---|---|
| TestAlbatross | `https://rpc.testnet.nimiqwatch.com/` | `https://hub.nimiq-testnet.com` |
| MainAlbatross | `https://rpc.nimiqwatch.com` | `https://hub.nimiq.com` |
| DevAlbatross | none; configure explicitly | testnet Hub default |

A deployment may replace the public RPC with its own node or trusted provider.
The listed public RPCs are convenient community infrastructure rather than a
service-level guarantee; use a provider you trust for real funds. Public startup
checks the RPC's reported network ID, not merely the hostname.
The selected network ID and deployment mode are also stored durably in the SQLite
database so changing environment variables cannot reinterpret old chain data or
expose a development/mock database as a public service.

## Deployment modes

`NIMHUNT_DEPLOYMENT_MODE` separates public safety from blockchain choice:

| Mode | Required network | Test features | Intended use |
|---|---|---|---|
| `development` | normally TestAlbatross | enabled | local desktop and phone development |
| `public-testnet` | TestAlbatross, ID `5` | disabled | public internet deployment using test NIM |
| `production` | MainAlbatross, ID `24` | disabled | final real-NIM deployment |

If no mode is set, NimHunt retains its existing `development` behaviour. For
backwards compatibility, `NIMHUNT_PRODUCTION=1` maps to `production` when the new
variable is absent. `NIMHUNT_DEPLOYMENT_MODE` is preferred. Contradictory old and
new settings are rejected rather than silently resolved.

Both public modes disable Desktop User, Test Location, mock data, placeholder
addresses, fake sends and development seeds. They also require explicit signer
commands, private signing material, HTTPS chain endpoints and a valid
operator-controlled cancellation-fee address.

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer
- npm
- A browser for desktop testing
- Nimiq Pay for real mini-app/device and payment testing

## Local installation

Clone the repository, then enter it:

```bash
git clone git@github.com:NIMHunt/nim-hunt.git
cd nim-hunt
```

Create and activate a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the runtime, development and Nimiq helper dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
npm ci --prefix helpers
```

`requirements.txt` contains only runtime packages; `requirements-dev.txt` adds
the pinned test, lint and dependency-audit tools used by contributors and CI.
`npm ci` uses `helpers/package-lock.json` and installs the exact pinned
`@nimiq/core` version rather than a moving latest release.

## Run locally

The development launcher configures TestAlbatross and the bundled helper.
Supply a dedicated TestAlbatross mnemonic explicitly before starting:

```bash
export NIMHUNT_NIMIQ_MNEMONIC='your private TestAlbatross mnemonic'
./nimhunt_start_dev.sh
```

Use a development-only mnemonic and never commit it. The launcher stops with a
clear error if `NIMHUNT_NIMIQ_MNEMONIC` is missing.

Open:

```text
http://127.0.0.1:8000/
```

The launcher may be invoked from another directory because it resolves the
project relative to its own script:

```bash
~/nim-hunt/nimhunt_start_dev.sh
```

Development launcher settings:

| Variable | Default | Purpose |
|---|---|---|
| `NIMHUNT_PROJECT_DIR` | launcher directory | alternate project directory |
| `NIMHUNT_NIMIQ_MNEMONIC` | none; required | development-only TestAlbatross signing mnemonic |
| `NIMHUNT_HOST` | `0.0.0.0` | Uvicorn bind host |
| `NIMHUNT_PORT` | `8000` | Uvicorn port |

## Reset development data

Stop the server first, then run:

```bash
./nimhunt_reset_mock_data.sh
```

This deletes the selected development database and its SQLite sidecars, creates
the current schema and inserts mock users, Spots, claims and transactions. The
script and `spoof.py` refuse to run in either public deployment mode.

NimHunt currently follows a fresh-development-database policy rather than
maintaining a general migration framework. The creation-fee release raises the
schema to version `2`, so after pulling this release stop the server and run
this reset once before ordinary local testing. Never use the reset script on a
public deployment database; public TestAlbatross and MainAlbatross must use fresh
persistent databases for this release.

## Phone testing

Run NimHunt, then expose the local server through HTTPS in another terminal:

```bash
npx localtunnel --port 8000
```

Open the HTTPS address inside Nimiq Pay. A public deployment should use a normal
HTTPS reverse proxy or hosting platform rather than localtunnel.

## Wallet and seed configuration

### Bundled helper: recommended setup

The bundled `helpers/nimiq_helper.mjs` reads a BIP39 mnemonic from:

```bash
export NIMHUNT_NIMIQ_MNEMONIC='word1 word2 ... word24'
```

An optional BIP39 passphrase can be provided separately:

```bash
export NIMHUNT_NIMIQ_MNEMONIC_PASSWORD='optional passphrase'
```

The mnemonic is used to derive each Spot's deposit key at a path shaped like:

```text
m/44'/242'/{spot-key-index}'/0'
```

One mnemonic therefore controls all derived Spot deposit addresses. Back it up
securely. Losing it makes remaining Spot funds unrecoverable; exposing it allows
an attacker to spend those funds.

Supply secrets through the host's secret manager or protected environment—not a
committed file, shell history or the repository. The bundled sender uses
`NIMHUNT_NIMIQ_RPC_URL` for `getLatestBlock` and `sendRawTransaction`; this is
more suitable for hosted backends than requiring the Railway container to form
its own peer-to-peer Nimiq consensus connection.

### Derive and send commands

Python communicates with a signer through JSON on standard input/output. To use
the bundled helper:

```bash
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /absolute/path/to/nim-hunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /absolute/path/to/nim-hunt/helpers/nimiq_helper.mjs'
```

A custom local signer may replace either command as long as it honours the JSON
contract documented in `wallet.py`. In public modes, **both** configured commands
must support the non-broadcast `validate_signer_configuration` action and return
the same derived address for the supplied key path. NimHunt performs that check at
startup, so a missing or inconsistent send-side key fails before a payout is due.
This allows a deployment to keep key access inside a separate process or
hardware-backed service without giving NimHunt the private key itself.

In `development`, `trans_updater.py` may find the bundled helper automatically when
`NIMHUNT_NIMIQ_MNEMONIC` is supplied. Both public modes require explicit derive
and send commands so the operator's intent is unambiguous.

A custom signer that manages its own keys may omit `NIMHUNT_NIMIQ_MNEMONIC`, but
then it must use custom commands and set `NIMHUNT_NIMIQ_EXTERNAL_SIGNER=1`. The
bundled helper always requires a private mnemonic in either public mode.

Optional helper discovery variables:

| Variable | Purpose |
|---|---|
| `NIMHUNT_NIMIQ_HELPER_PATH` | override the bundled helper file path |
| `NIMHUNT_NIMIQ_NODE_BINARY` | override the Node executable, default `node` |

### Legacy/development seed variables

`wallet.py` also contains encrypted master-seed helpers:

- `NIMHUNT_MASTER_SEED_ENC`
- `NIMHUNT_MASTER_SEED_SECRET`
- `NIMHUNT_DEV_MASTER_SEED`

These support deterministic development placeholders and custom integrations;
they are **not** the mnemonic consumed by the bundled official Nimiq helper.
`NIMHUNT_DEV_MASTER_SEED` and placeholder-address behaviour are disabled in every
public deployment. These Python seed helpers do not provide signing material to
the bundled JavaScript helper. For that helper, use `NIMHUNT_NIMIQ_MNEMONIC`.

## Platform fee configuration

Creation and cancellation fees use human-readable NIM amounts and one shared
operator-controlled destination address. One NIM contains 100,000 Luna, so values
may use up to five decimal places. `0` is valid and disables that particular fee;
negative values or fractions smaller than one Luna are rejected at startup.

### Creation fee amounts

Standard Spots and Prizedraws have independent settings:

```bash
export NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM=200
export NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM=200
```

Standard Spots and Prizedraws both default to `200 NIM`. Each new draft snapshots the appropriate amount. Changing
an environment variable later does not retroactively change an existing draft's
deposit target or fee transaction.

### Cancellation fee amount

```bash
export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=500
```

The default is `500 NIM`. A value of `0` preserves the refund flow without charging
a cancellation fee. The amount and destination are snapshotted when cancellation first starts,
so later environment changes cannot alter a cancellation already in progress.

### Shared fee destination

Set the checksummed Nimiq address that receives both creation and cancellation fees:

```bash
export NIMHUNT_SPOT_FEE_ADDRESS='NQ45 ... real address ...'
```

`NIMHUNT_SPOT_FEE_ADDRESS` is the preferred name. The former
`NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS` remains a compatibility alias for an
existing deployment. If both variables are set, they must identify the same
address; conflicting values stop startup rather than risking a misdirected fee.
Update the hosting variable to the new name, then remove the old one.

The development default is a real TestAlbatross address belonging to a public
test wallet, so it is not operator-controlled. Both public modes explicitly
reject that address and require a different checksum-valid address controlled by
the operator. The destination is also snapshotted onto each new Spot for its
creation fee.

## Environment variable reference

### Application and storage

| Variable | Default | Description |
|---|---|---|
| `NIMHUNT_DEPLOYMENT_MODE` | `development` | preferred: `development`, `public-testnet`, or `production` |
| `NIMHUNT_PRODUCTION` | unset | legacy compatibility flag; `1` maps to `production` only |
| `NIMHUNT_DB_PATH` | `records.db` | SQLite file; public modes require a separate absolute persistent path |
| `NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM` | `200` | one-time creation fee for Standard Spots, in NIM |
| `NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM` | `200` | one-time creation fee for Prizedraws, in NIM |
| `NIMHUNT_SPOT_CANCELLATION_FEE_NIM` | `500` | cancellation fee amount in NIM; snapshotted when cancellation starts |
| `NIMHUNT_SPOT_FEE_ADDRESS` | public TestAlbatross development address | shared creation/cancellation fee recipient; public modes require an operator address |

### Nimiq network and RPC

| Variable | Default | Description |
|---|---|---|
| `NIMHUNT_NIMIQ_NETWORK` | `TestAlbatross` | `TestAlbatross`, `MainAlbatross` or `DevAlbatross` |
| `NIMHUNT_NIMIQ_NETWORK_ID` | selected by network | optional explicit protocol ID; must match the network |
| `NIMHUNT_NIMIQ_RPC_URL` | selected by network | RPC used to verify transactions |
| `NIMHUNT_NIMIQ_HUB_URL` | selected by network | Hub used for creator funding requests |
| `NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS` | `12` | RPC/helper timeout base |
| `NIMHUNT_NIMIQ_ADDRESS_TX_LOOKUP_LIMIT` | `500` | recent address transactions inspected during fallback verification |
| `NIMHUNT_NIMIQ_TRANSACTION_FEE` | `0` | outgoing network fee in Luna; public deployments currently require `0` |

`NIMHUNT_NIMIQ_TRANSACTION_FEE` is measured in **Luna**, unlike the platform-fee
variables. The current funding model does not reserve an extra network-fee budget
for every creation fee, claim, refund and Prizedraw payout, so both public modes
reject non-zero values rather than silently underfunding Spots. Development may
still use the setting for controlled experiments.

### Signer and mnemonic

| Variable | Default | Description |
|---|---|---|
| `NIMHUNT_NIMIQ_MNEMONIC` | none | mnemonic used by bundled helper |
| `NIMHUNT_NIMIQ_MNEMONIC_PASSWORD` | none | optional BIP39 passphrase |
| `NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND` | auto only in development | JSON address-derivation command |
| `NIMHUNT_NIMIQ_SEND_COMMAND` | auto only in development | JSON signing/broadcast command |
| `NIMHUNT_NIMIQ_EXTERNAL_SIGNER` | `0` | assert that custom signer commands manage private keys themselves |
| `NIMHUNT_NIMIQ_HELPER_PATH` | bundled helper | override automatic helper path |
| `NIMHUNT_NIMIQ_NODE_BINARY` | `node` | Node executable for automatic helper use |

### Transaction reconciliation

| Variable | Default | Description |
|---|---:|---|
| `NIMHUNT_TRANSACTION_CHECK_INTERVAL_SECONDS` | `60` | delay between transaction checks |
| `NIMHUNT_TRANS_FAIL_AFTER_SECONDS` | `5400` | age before an unseen hash can be treated as failed |
| `NIMHUNT_TRANS_MAX_CHECKS_PER_RUN` | `100` | pending transactions checked per pass |

The failure timeout is deliberately conservative. Do not shorten it casually: an
unseen-but-broadcast payout must not be retried while it could still confirm,
because that could pay twice.

### Product rules

Most product limits are intentionally ordinary constants in `constants.py`, not
deployment secrets. This includes:

- radius and duration ranges;
- total and per-user participant maxima;
- allowed Prizedraw prize-count options;
- minimum Spot reward pool (currently `500 NIM`);
- minimum standard and Prizedraw payout amounts;
- report and display-name limits;
- background settlement and cache batch sizes.

Change these in `constants.py` only when intentionally changing product behaviour,
then run the complete test suite. Environment variables are reserved for values
that genuinely differ between deployments, secrets or operational tuning. Creation
fees are environment-backed operator policy and are snapshotted when a Spot is created.

NimHunt does not automatically parse a `.env` file. Export variables in the
shell, configure them in the process manager/hosting platform, or deliberately
add a deployment tool that loads `.env`. The file is ignored by Git so local
secrets are not committed accidentally.

## Public deployment

The development scripts refuse to run in either public mode. Configure the
environment, then start FastAPI through Uvicorn or a process manager. Public
startup performs strict initial cache, settlement and transaction-reconciliation
passes before traffic is accepted.

### Public TestAlbatross environment

Use a newly generated **private testnet mnemonic** and do not plan to reuse this
seed on mainnet.

```bash
export NIMHUNT_DEPLOYMENT_MODE=public-testnet
export NIMHUNT_DB_PATH=/srv/nimhunt-testnet/records.db

export NIMHUNT_NIMIQ_NETWORK=TestAlbatross
export NIMHUNT_NIMIQ_NETWORK_ID=5
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.testnet.nimiqwatch.com/
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq-testnet.com
export NIMHUNT_NIMIQ_MNEMONIC='private testnet mnemonic -- supply as a secret'
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'

export NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM=200
export NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM=200
export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=500
export NIMHUNT_SPOT_FEE_ADDRESS='NQ... operator testnet fee address ...'
```

This mode is public software using test NIM: it has production-style safety and
background-service strictness, but it deliberately remains on TestAlbatross. On
every public startup NimHunt also calls the configured RPC's `getNetworkId` method
and requires the live response to be `5`; a custom hostname cannot bypass the
network check merely because its URL looks plausible.

### MainAlbatross production environment

Use a separate newly generated private mainnet mnemonic or signing service.

```bash
export NIMHUNT_DEPLOYMENT_MODE=production
export NIMHUNT_DB_PATH=/srv/nimhunt-mainnet/records.db

export NIMHUNT_NIMIQ_NETWORK=MainAlbatross
export NIMHUNT_NIMIQ_NETWORK_ID=24
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.nimiqwatch.com
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq.com
export NIMHUNT_NIMIQ_MNEMONIC='private mainnet mnemonic -- supply as a secret'
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'

export NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM=200
export NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM=200
export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=500
export NIMHUNT_SPOT_FEE_ADDRESS='NQ... operator mainnet fee address ...'
```

Start outside Railway with one worker:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

Place HTTPS in front of Uvicorn. The app itself does not manage TLS certificates.
Public startup validates both signer commands and asks the configured RPC for its
actual network ID before opening the database or starting background services.

### Database network and deployment identity

Every database records `nimiq_network`, `nimiq_network_id` and `deployment_mode`
in an additive `app_metadata` table. Once bound, it refuses another network or mode.

- A fresh database is bound during first startup.
- An existing local database without metadata is bound when opened in `development`
  under the deliberately selected network.
- A development database cannot later be exposed as `public-testnet`, even though both
  normally use TestAlbatross; this prevents seeded mock users and Spots reaching the
  public service.
- An existing unbound database is rejected in either public mode; use a fresh public
  database rather than guessing which chain or safety context its records belong to.
- A public database is also protected from `spoof.py` resets if the process is
  accidentally launched with development settings.
- Network/deployment metadata remains additive, but the creation-fee release also
  adds immutable Spot columns and uses schema version `3`. There is deliberately no
  `ALTER` migration: existing development databases must be recreated, and public
  deployments must start with a fresh volume/database for this release.

### Testnet-to-mainnet cutover

Mainnet is an environment and data-volume change, not a code rewrite:

1. Stop accepting new public TestAlbatross activity.
2. Allow pending testnet claims, settlements and transactions to finish, or resolve
   them manually before shutdown.
3. Back up the complete TestAlbatross SQLite database and sidecars consistently.
4. Retain that database as a read-only testnet archive.
5. Create a fresh database or fresh persistent volume for MainAlbatross.
6. Do not copy testnet Spots, claims, transaction hashes, deposits or balances into
   the mainnet database.
7. Configure a separate private mainnet mnemonic or external signing setup.
8. Configure the real mainnet fee address and both desired creation-fee amounts.
9. Set `NIMHUNT_DEPLOYMENT_MODE=production`, MainAlbatross, ID `24`, mainnet RPC
   and mainnet Hub.
10. Start the new service, allow validation to complete, then perform deliberately
    small funding, claim, Prizedraw and cancellation smoke tests before advertising it.

The database network and deployment markers are additional guards: merely changing
environment variables while retaining the testnet or development database fails startup.

### Railway deployment

The repository includes `railway.json` and `mise.toml`. They configure Railpack,
Python 3.11, Node.js 20, installation of both dependency sets, Railway's `$PORT`,
exactly one Uvicorn worker, restart-on-failure, `/healthz`, and 30 seconds for
graceful shutdown after Railway sends `SIGTERM`. Keep Railway's deployment overlap
at zero: two live NimHunt processes must not share one SQLite volume.

Railway builds the repository under `/app`, while a volume mounted at `/data` is
available only when the service runs. Create one service, attach one `/data` volume,
set the service to exactly one replica, and never run database work in a build or
pre-deploy command.

#### Railway variables: public TestAlbatross

Use these service variables. Values marked `SECRET` must be supplied by the operator:

```text
NIMHUNT_DEPLOYMENT_MODE=public-testnet
NIMHUNT_DB_PATH=/data/records.db
NIMHUNT_NIMIQ_NETWORK=TestAlbatross
NIMHUNT_NIMIQ_NETWORK_ID=5
NIMHUNT_NIMIQ_RPC_URL=https://rpc.testnet.nimiqwatch.com/
NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq-testnet.com
NIMHUNT_NIMIQ_MNEMONIC=SECRET_PRIVATE_TESTNET_MNEMONIC
NIMHUNT_NIMIQ_MNEMONIC_PASSWORD=SECRET_OPTIONAL_PASSPHRASE
NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND=node /app/helpers/nimiq_helper.mjs
NIMHUNT_NIMIQ_SEND_COMMAND=node /app/helpers/nimiq_helper.mjs
NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM=200
NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM=200
NIMHUNT_SPOT_CANCELLATION_FEE_NIM=500
NIMHUNT_SPOT_FEE_ADDRESS=OPERATOR_TESTNET_NQ_ADDRESS
```

Do not set `NIMHUNT_PRODUCTION` or `NIMHUNT_DEV_MASTER_SEED`.
`NIMHUNT_NIMIQ_EXTERNAL_SIGNER` is unnecessary when using the bundled helper.

#### Railway variables: MainAlbatross production

Use a fresh volume or a different `/data/records.db` on a separate Railway service
and a separately generated mainnet signer:

```text
NIMHUNT_DEPLOYMENT_MODE=production
NIMHUNT_DB_PATH=/data/records.db
NIMHUNT_NIMIQ_NETWORK=MainAlbatross
NIMHUNT_NIMIQ_NETWORK_ID=24
NIMHUNT_NIMIQ_RPC_URL=https://rpc.nimiqwatch.com
NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq.com
NIMHUNT_NIMIQ_MNEMONIC=SECRET_PRIVATE_MAINNET_MNEMONIC
NIMHUNT_NIMIQ_MNEMONIC_PASSWORD=SECRET_OPTIONAL_PASSPHRASE
NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND=node /app/helpers/nimiq_helper.mjs
NIMHUNT_NIMIQ_SEND_COMMAND=node /app/helpers/nimiq_helper.mjs
NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM=200
NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM=200
NIMHUNT_SPOT_CANCELLATION_FEE_NIM=500
NIMHUNT_SPOT_FEE_ADDRESS=OPERATOR_MAINNET_NQ_ADDRESS
```

Generate a public Railway domain and keep the service continuously running. Do not
enable serverless sleeping: settlement and transaction reconciliation must continue
when no visitor is making requests. `/healthz` is used by Railway while a deployment
starts; it is not continuous monitoring. Add an external uptime check if continuous
monitoring is wanted.

A Railway service with an attached volume may have brief redeployment downtime.
The configured drain period gives FastAPI time to stop its transaction, settlement
and cache loops cleanly before the process is force-killed. Configure and test
Railway volume backups before accepting meaningful funds.

### Public storage and launch checklist

SQLite is appropriate for NimHunt's intended small audience, but the database is
the durable record of derived addresses, submitted payments, claims, winners and
payout/cancellation intent. Use persistent storage, restrict permissions, back up
the database and its active sidecars consistently, and test restoration.

Before public launch:

1. Install from the lock files.
2. Store the private mnemonic and optional passphrase in the host's secret manager.
3. Back up the mnemonic separately and verify recovery.
4. Configure the correct deployment mode, network, ID, RPC and Hub.
5. Configure a real fee address, both creation fees and the cancellation fee.
6. Use a fresh schema-version-3 network-specific persistent database.
7. Serve over HTTPS with one application replica and worker.
8. Confirm strict startup and `/healthz`.
9. Complete a deliberately small-value end-to-end cycle.
10. Restart without replacing the database and verify state remains correct.

NimHunt has substantial automated coverage but no independent security audit.
Use modest values appropriate to the competition.

## Localisation

Nimiq Pay injects a read-only ISO 639-1 language code at:

```javascript
window.nimiqPay.language
```

NimHunt reads that value before falling back to English. It deliberately does not
fall back to the desktop browser language: outside Nimiq Pay, English is the
predictable default.

Localisation is organised in:

- `static/localisation.js` — language normalisation, fallback and DOM helpers;
- `static/interface_text.js` — central English interface catalogues and future
  per-language overrides;
- `static/localise_page.js` — applies translations to marked static elements.

Static HTML is translated only when explicitly marked with attributes such as:

```html
<span data-i18n="common.cancel">Cancel</span>
<input data-i18n-placeholder="findSpots.answerPlaceholder" placeholder="Answer">
```

To add a language, add a partial override to `INTERFACE_TRANSLATIONS`:

```javascript
export const INTERFACE_TRANSLATIONS = {
    en: {},
    de: {
        static: {
            common: {
                cancel: 'Abbrechen',
            },
        },
        common: {
            notice: {
                ok: 'OK',
            },
        },
    },
};
```

Missing keys inherit the English catalogue automatically, so a translation can
be introduced gradually. Keep functions where English copy is parameterised
(for example counts or Spot amounts). Never mark user-generated titles,
descriptions, locations or display names with `data-i18n`.

## Testing and quality checks

Activate the virtual environment, then run:

```bash
python -m pytest -q
npm test --prefix helpers
ruff check .
python -m compileall -q *.py tests
```

`pyproject.toml` keeps the Python test path, warning policy and Ruff rules in one
place. GitHub Actions runs the same deterministic checks for every pull request
and every update to `main`.

Check every browser/helper module and shell script:

```bash
for file in static/*.js; do node --check --input-type=module < "$file"; done
for file in helpers/*.mjs; do node --check "$file"; done
for file in *.sh; do bash -n "$file"; done
```

Optional dependency checks:

```bash
python -m pip check
python -m pip_audit
npm audit --omit=dev --prefix helpers
```

## Main files

| File/directory | Responsibility |
|---|---|
| `main.py` | FastAPI app, deployment validation, health endpoint and service lifecycle |
| `public_html.py` | page routes and JSON API endpoints |
| `constants.py` | product and deployment configuration |
| `database.py` | schema creation and database connections |
| `db_access.py` | validated database reads and writes |
| `cache.py` | in-memory public/owner cache |
| `wallet.py` | address validation and signer command boundary |
| `trans_updater.py` | blockchain verification, creation-fee recovery and outgoing payments |
| `settlement_updater.py` | duration-claim and Prizedraw settlement |
| `helpers/` | official Nimiq JS helper and tests |
| `templates/` | Jinja page shells |
| `static/` | browser JavaScript, localisation, CSS and icons |
| `tests/` | Python regression and integration tests |
| `.github/workflows/ci.yml` | permanent pull-request and `main` verification |
| `pyproject.toml` | shared pytest and Ruff configuration |
| `requirements-dev.txt` | pinned test, lint and audit tooling |
| `spoof.py` | destructive development-only mock-data seed |

## Operational limitations

- User identity is device-based; there is no password recovery account system.
- A wrong-wallet Spot top-up requires manual recovery.
- SQLite and the in-process background loops assume one modest deployment rather
  than a horizontally scaled fleet of workers.
- Public RPC and map-tile services have no NimHunt-specific availability guarantee.
- Location evidence reduces casual misuse but cannot make phone GPS impossible to spoof.
- On-chain transfers are irreversible; use TestAlbatross and small values first.
- An ambiguous creation-fee send remains pending instead of being retried automatically;
  this may require manual reconciliation, but prevents charging the same Spot twice.

These trade-offs are intentional and proportionate to the project's competition
scope. They should be revisited before substantially increasing user counts or
funding values.

## Files not committed

The repository ignores local, generated and private files including:

- `venv/`
- `records.db` and SQLite sidecars
- `.env`
- `x-dob.txt`
- caches and logs
- installed `node_modules/`

Never commit mnemonics, passphrases, encrypted seed secrets or production database
copies.
