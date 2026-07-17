# NimHunt

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
- **Creator tools** — inspect drafts, deposits, publishing state, claim codes and history.
- **Claim history** — users can review pending, successful and failed claims.
- **On-chain descriptions** — NimHunt-generated transactions include short Spot labels.
- **Production/test separation** — desktop shortcuts remain available locally but are
  disabled automatically when production mode is enabled.
- **Localisation-ready UI** — interface copy is centralised and selected from
  `window.nimiqPay.language`; English is currently the only bundled language.

## How NimHunt identifies users

Inside Nimiq Pay, the mini-app SDK provides a stable device identifier. NimHunt
hashes and stores that identifier as the account identity. It does not use a
traditional username/password login.

For local development outside Nimiq Pay, NimHunt can fall back to the seeded
**Desktop User**. This and the Find Spots **Test Location** control are development
features only. They are not rendered or accepted when `NIMHUNT_PRODUCTION=1`.

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
  to derive addresses, sign outgoing transactions and broadcast them.
- **Background services** refresh caches, settle completed Prizedraws and reconcile
  pending blockchain transactions.

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
2. The creator opens a Nimiq Pay payment request from the My Spots page.
3. Nimiq Pay signs and broadcasts the creator's funding transaction.
4. NimHunt records the returned hash and verifies it independently through RPC.
5. The first confirmed sender becomes that Spot's funding wallet. Later top-ups
   must come from the same wallet.
6. Once the required amount is confirmed, the draft can be published.

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

Cancelling a funded standard Spot creates up to two outgoing transactions:

- the remaining refundable balance to the original funding wallet;
- the configured cancellation fee to the platform fee address.

Already-confirmed claim payouts are deducted before the refundable balance is
calculated. The Spot is marked cancelled only after every required outgoing leg
has reached a final state.

### On-chain transaction descriptions

NimHunt includes short public transaction data:

- `Funding: [Spot name]`
- `Claim: [Spot name]`
- `Prizedraw: [Spot name]`
- `Cancelled Spot: [Spot name]`
- `Refund Fee: [Spot name]`

Descriptions are limited to 30 UTF-8 bytes and safely truncated. Because this
information is written to the blockchain, only the already-public Spot title is
included—never claim codes, device identifiers or private account information.

## Nimiq networks

NimHunt recognises the official Albatross network names and protocol IDs:

| Network | ID | Intended use |
|---|---:|---|
| `TestAlbatross` | `5` | normal development and test NIM |
| `MainAlbatross` | `24` | production and real NIM |
| `DevAlbatross` | `6` | advanced local/custom network development |

The repository defaults to `TestAlbatross`. Network-specific RPC and Hub defaults
are selected automatically:

| Network | Default RPC | Default Hub |
|---|---|---|
| TestAlbatross | `https://rpc.testnet.nimiqwatch.com/` | `https://hub.nimiq-testnet.com` |
| MainAlbatross | `https://rpc.nimiqwatch.com` | `https://hub.nimiq.com` |
| DevAlbatross | none; configure explicitly | testnet Hub default |

A deployment may replace the public RPC with its own node or a trusted provider.
Production startup refuses inconsistent network IDs, a testnet endpoint, missing
signing commands or other development-only settings.

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

Install Python and Nimiq helper dependencies:

```bash
pip install -r requirements.txt
npm ci --prefix helpers
```

`npm ci` uses `helpers/package-lock.json` and installs the exact pinned
`@nimiq/core` version rather than a moving latest release.

## Run locally

The development launcher configures TestAlbatross, the bundled helper and the
public test mnemonic unless you override them:

```bash
./nimhunt_start_dev.sh
```

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
| `NIMHUNT_HOST` | `0.0.0.0` | Uvicorn bind host |
| `NIMHUNT_PORT` | `8000` | Uvicorn port |

## Reset development data

Stop the server first, then run:

```bash
./nimhunt_reset_mock_data.sh
```

This deletes the selected development database and its SQLite sidecars, creates
the current schema and inserts mock users, Spots, claims and transactions. The
script and `spoof.py` refuse to run in production mode.

NimHunt currently follows a fresh-development-database policy rather than
maintaining a general migration framework. Never use the reset script on a
production database.

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
committed file, shell history or the repository.

### Public test mnemonic

For TestAlbatross only, the bundled helper can use its public deterministic test
mnemonic:

```bash
export NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC=1
```

This is enabled by the local development launcher. It is intentionally public and
must never protect real funds. Production startup rejects it.

### Derive and send commands

Python communicates with a signer through JSON on standard input/output. To use
the bundled helper:

```bash
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /absolute/path/to/nim-hunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /absolute/path/to/nim-hunt/helpers/nimiq_helper.mjs'
```

A custom local signer may replace either command as long as it honours the JSON
contract documented in `wallet.py`. This allows a deployment to keep key access
inside a separate process or hardware-backed service.

When no explicit command is supplied outside production, `trans_updater.py` can
find the bundled helper automatically if a permitted test mnemonic is available.
Production nevertheless requires explicit derive and send commands so the
operator's intent is unambiguous.

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
`NIMHUNT_DEV_MASTER_SEED` and placeholder-address behaviour are disabled in
production. For the bundled helper, use `NIMHUNT_NIMIQ_MNEMONIC` instead.

## Cancellation fee configuration

The cancellation fee has two separate settings:

### Fee amount

Set the fee as a human-readable NIM amount:

```bash
export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=1
```

Decimals up to five places are supported because one NIM contains 100,000 Luna:

```bash
export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=0.25
```

The default is `1 NIM`. A value of `0` disables the platform fee while preserving
the refund flow. Values below one Luna or negative values are rejected at startup.

### Fee destination

Set the checksummed Nimiq address that receives cancellation fees:

```bash
export NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS='NQ45 ... real address ...'
```

The repository default is an obvious development placeholder. Production startup
requires a real, checksum-valid address.

## Environment variable reference

### Application and storage

| Variable | Default | Description |
|---|---|---|
| `NIMHUNT_PRODUCTION` | false | set to `1`/`true` to enable production safeguards |
| `NIMHUNT_DB_PATH` | `records.db` | SQLite file; use an absolute persistent path in production |
| `NIMHUNT_SPOT_CANCELLATION_FEE_NIM` | `1` | cancellation fee amount in NIM |
| `NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS` | development placeholder | fee recipient |

### Nimiq network and RPC

| Variable | Default | Description |
|---|---|---|
| `NIMHUNT_NIMIQ_NETWORK` | `TestAlbatross` | `TestAlbatross`, `MainAlbatross` or `DevAlbatross` |
| `NIMHUNT_NIMIQ_RPC_URL` | selected by network | RPC used to verify transactions |
| `NIMHUNT_NIMIQ_HUB_URL` | selected by network | Hub used for creator funding requests |
| `NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS` | `12` | RPC/helper timeout base |
| `NIMHUNT_NIMIQ_ADDRESS_TX_LOOKUP_LIMIT` | `500` | recent address transactions inspected during fallback verification |
| `NIMHUNT_NIMIQ_TRANSACTION_FEE` | `0` | outgoing network fee in Luna |

`NIMHUNT_NIMIQ_TRANSACTION_FEE` is measured in **Luna**, unlike the cancellation
fee variable. It is passed to the Nimiq transaction builder for each outgoing
server-generated transaction.

### Signer and mnemonic

| Variable | Default | Description |
|---|---|---|
| `NIMHUNT_NIMIQ_MNEMONIC` | none | mnemonic used by bundled helper |
| `NIMHUNT_NIMIQ_MNEMONIC_PASSWORD` | none | optional BIP39 passphrase |
| `NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC` | `0` | permit public test mnemonic outside production |
| `NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND` | auto only in development | JSON address-derivation command |
| `NIMHUNT_NIMIQ_SEND_COMMAND` | auto only in development | JSON signing/broadcast command |
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
- minimum standard and Prizedraw payout amounts;
- report and display-name limits;
- background settlement and cache batch sizes.

Change these in `constants.py` only when intentionally changing product behaviour,
then run the complete test suite. Environment variables are reserved for values
that genuinely differ between deployments, secrets or operational tuning.

NimHunt does not automatically parse a `.env` file. Export variables in the
shell, configure them in the process manager/hosting platform, or deliberately
add a deployment tool that loads `.env`. The file is ignored by Git so local
secrets are not committed accidentally.

## Production deployment

The development scripts refuse to run in production. Configure the environment,
then start FastAPI through Uvicorn or a process manager.

Minimal bundled-helper example:

```bash
export NIMHUNT_PRODUCTION=1
export NIMHUNT_DB_PATH=/srv/nimhunt/records.db

export NIMHUNT_NIMIQ_NETWORK=MainAlbatross
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.nimiqwatch.com
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq.com
export NIMHUNT_NIMIQ_MNEMONIC='private production mnemonic'
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /srv/nimhunt/helpers/nimiq_helper.mjs'

export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=1
export NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS='NQ45 ... real fee address ...'

cd /srv/nimhunt
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000
```

Place an HTTPS reverse proxy in front of Uvicorn. The app itself does not manage
TLS certificates.

Production startup validates configuration and performs initial cache, settlement
and transaction-reconciliation passes before accepting traffic. If startup fails,
correct the configuration or chain access rather than bypassing the guard.

### Production storage

SQLite is appropriate for NimHunt's intended small audience, but the database is
the durable record of:

- which derived addresses belong to which Spots;
- submitted and confirmed payments;
- claim outcomes;
- Prizedraw winners;
- cancellation and payout intent.

Use persistent storage, restrict file permissions and back up the database and
its active SQLite sidecars consistently. Test restoration before launch. Never
replace a live database with mock data.

### Production checklist

Before accepting real funds:

1. Install from the lock files (`pip install -r requirements.txt`, `npm ci --prefix helpers`).
2. Store the mnemonic and optional passphrase in the host's secret manager.
3. Back up the mnemonic separately and verify recovery.
4. Configure `MainAlbatross`, mainnet RPC and mainnet Hub.
5. Configure a real cancellation-fee address and desired fee amount.
6. Put `NIMHUNT_DB_PATH` on persistent backed-up storage.
7. Serve NimHunt over HTTPS.
8. Start once and confirm all initial background checks succeed.
9. Complete one deliberately small-value live cycle:
   - create and fund a Spot;
   - make a standard claim;
   - settle a Prizedraw;
   - cancel another funded Spot and inspect refund/fee transactions.
10. Stop and restart the service without replacing the database, then verify
    transactions and Spot state remain correct.

NimHunt has substantial automated coverage but has not received an independent
security audit. Start with modest funding limits appropriate to the competition.

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
PYTHONPATH=. python -W error::ResourceWarning -m pytest -q
npm --prefix helpers test
python -m py_compile *.py tests/*.py
ruff check .
```

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
| `main.py` | FastAPI app, production validation and service lifecycle |
| `public_html.py` | page routes and JSON API endpoints |
| `constants.py` | product and deployment configuration |
| `database.py` | schema creation and database connections |
| `db_access.py` | validated database reads and writes |
| `cache.py` | in-memory public/owner cache |
| `wallet.py` | address validation and signer command boundary |
| `trans_updater.py` | blockchain verification and outgoing payments |
| `settlement_updater.py` | duration-claim and Prizedraw settlement |
| `helpers/` | official Nimiq JS helper and tests |
| `templates/` | Jinja page shells |
| `static/` | browser JavaScript, localisation, CSS and icons |
| `tests/` | Python regression and integration tests |
| `spoof.py` | destructive development-only mock-data seed |

## Operational limitations

- User identity is device-based; there is no password recovery account system.
- A wrong-wallet Spot top-up requires manual recovery.
- SQLite and the in-process background loops assume one modest deployment rather
  than a horizontally scaled fleet of workers.
- Public RPC and map-tile services have no NimHunt-specific availability guarantee.
- Location evidence reduces casual misuse but cannot make phone GPS impossible to spoof.
- On-chain transfers are irreversible; use TestAlbatross and small values first.

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
