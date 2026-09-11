# NimHunt

[![CI](https://github.com/NIMHunt/nim-hunt/actions/workflows/ci.yml/badge.svg)](https://github.com/NIMHunt/nim-hunt/actions/workflows/ci.yml)
[![Live site](https://img.shields.io/badge/live-nimhunt.app-21bca5)](https://nimhunt.app)
[![License: MIT](https://img.shields.io/badge/license-MIT-5c5ce0)](LICENSE)

**[Try NimHunt](https://nimhunt.app)** · **[Get Nimiq Pay](https://nimpay.app)** ·
[Security](SECURITY.md) · [Administration](ADMIN.md) ·
[Contributing](CONTRIBUTING.md) · [Configuration](docs/configuration.md)

[![NimHunt map preview](static/images/nimhunt-default-social-card.png)](https://nimhunt.app)

NimHunt is a mobile-first geofaucet and Prizedraw mini-app for
[Nimiq Pay](https://nimpay.app). Creators fund geographic **Spots** with NIM;
participants discover them on a map and, from inside the configured area, claim
a Standard Spot reward or enter a Prizedraw.

The public deployment runs on Nimiq MainAlbatross. NimHunt was built for modest
community use and deliberately favours understandable rules, durable financial
state and conservative failure handling over high-volume infrastructure.

## Features

- **Two reward formats:** immediate Standard Spot rewards and randomly selected
  Prizedraw winners.
- **Location-aware participation:** configurable radii, schedules, stay
  durations, participant limits and optional single-use claim codes.
- **Nimiq payments:** Nimiq Pay handles participant interaction; the server
  independently verifies deposits through Nimiq RPC and signs payouts from
  per-Spot deposit addresses.
- **Creator workflow:** drafts, deposit progress, publishing, claim-code access,
  duplication, cancellation and Spot history.
- **Durable settlement:** outgoing intents and Prizedraw winners are persisted
  before broadcast; ambiguous transactions are reconciled instead of retried
  blindly.
- **Operator tools:** an unlinked administrator panel supports reports, user
  moderation, audit history and a guarded severe Spot-ban workflow.
- **Deployment separation:** development, public TestAlbatross and production
  MainAlbatross modes have explicit startup checks.
- **Optional announcements:** a production-only worker can announce newly active
  Spots through a configured X account. It remains off unless
  `NIMHUNT_X_AUTO_POST_ENABLED`, `NIMHUNT_X_ACCOUNT_HANDLE` and the required
  credentials (including `NIMHUNT_X_ACCESS_TOKEN_SECRET`) are set.

## How NimHunt works

1. A creator makes a draft and chooses its location, rules and NIM reward pool.
2. NimHunt assigns the draft a unique deposit address. The creator funds the
   reward pool and snapshotted creation fee with Nimiq Pay.
3. The server verifies the transfer through its configured RPC. After the
   creation fee confirms, a complete draft can be published.
4. Participants find the Spot on the map. Standard Spots pay eligible claims;
   Prizedraws record eligible entries and persist their winners when the draw
   settles.
5. Normal expiry returns safely accounted unused funds to the original funding
   wallet after all obligations settle. Creator cancellation follows a separate
   fee and refund workflow.

Transaction intent, finality, cancellation and settlement rules are deliberately
more detailed than this overview. See [Security](SECURITY.md), the
[security architecture map](docs/security-architecture.md), and the source
owners listed under [Project map](#project-map) before changing those paths.

## Identity, location and trust

NimHunt uses several facts and signals for different purposes; none should be
described as stronger than it is:

- The **Nimiq Pay device identifier** is hashed and used to find a durable USER.
  It provides browser/device continuity for limits and for existing creator/self
  routes. It does **not** cryptographically authenticate a wallet, a person or a
  unique human.
- New public Standard claims and Prizedraw entries use an **authenticated wallet
  session**. A valid Nimiq signature proves control of the corresponding key,
  not personhood.
- Each initial public claim path also uses a short-lived, single-use,
  **claim-specific signed authorization**. It binds the action, Spot, device,
  wallet, submitted location, deployment/network and server nonce.
- The server derives the canonical signer address and stores it as the claim's
  immutable **payout address**. A browser-supplied payout destination has no
  financial authority.
- Browser GPS and accuracy are user-controlled assertions used for radius rules
  and limited behavioural evidence. Signing those coordinates proves that the
  wallet approved the assertion; it does **not** prove physical presence.
- Creator and self-service routes have not yet migrated to wallet sessions and
  continue to use the device-continuity ownership model documented in the
  [security architecture](docs/security-architecture.md#creatorprivate-route-session-migration-design-deferred).

NimHunt combines these boundaries with durable capacity checks, temporary
restrictions, explicit manual-review holds and automatic payout exposure limits.
It cannot prevent sophisticated GPS spoofing, independent-wallet creation,
VPN/network rotation, device farms, relays or collusion. It has not received an
independent security audit; use appropriately modest values.

## Local development

### Requirements

- Python 3.11 or newer
- Node.js 20 or newer and npm
- a dedicated **TestAlbatross** mnemonic for the bundled Nimiq helper

Never use production signing material for local development.

### Setup

```bash
git clone https://github.com/NIMHunt/nim-hunt.git
cd nim-hunt
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
npm ci --prefix helpers
```

Export a private TestAlbatross mnemonic, then use the development launcher:

```bash
export NIMHUNT_NIMIQ_MNEMONIC='your private TestAlbatross mnemonic'
./nimhunt_start_dev.sh
```

Open <http://127.0.0.1:8000>. After the sample data is seeded, development mode
supplies its Desktop User outside Nimiq Pay and exposes the Find Spots **Test
Location** control. Neither shortcut is rendered or accepted in a public
deployment.

The app creates `records.db` and the current schema on first start. To add the
development sample data, stop the server and run `python spoof.py`. The
interactive `./nimhunt_reset_mock_data.sh` helper deletes the selected local
database before reseeding it; it refuses to run in either public mode.

NimHunt does not automatically load `.env` files. Export settings in the shell or
configure them in the process manager. See the complete
[configuration reference](docs/configuration.md).

## Testing and quality checks

Run the deterministic suite used by CI:

```bash
python -m pytest -q
npm test --prefix helpers
ruff check .
python -m compileall -q *.py tests
for file in static/*.js; do node --check --input-type=module < "$file"; done
for file in helpers/*.mjs; do node --check "$file"; done
for file in *.sh; do bash -n "$file"; done
python -m pip check
```

CI also compiles every Jinja template, seeds a fresh development database and
runs Python and production Node dependency audits. The exact workflow is in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). Contributor expectations
and review guidance are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Deployment overview

Public deployments must use exactly one application worker/replica with a
persistent SQLite database. Startup validates the deployment mode, Nimiq network,
live RPC network identity, signer access, fee destination and other public-safety
requirements. Chain-dependent actions fail closed when the RPC is unavailable;
the Railway entry point can start in a degraded mode and verifies the network
before chain work resumes.

The repository includes `railway.json`, `railway_start.py` and `mise.toml` for a
single-worker Railway deployment. Attach persistent storage, keep Railway as the
only public ingress for the supplied trusted-proxy setting, configure and test
backups, and do not enable sleeping while background settlement and reconciliation
must continue.

Use the [configuration and deployment reference](docs/configuration.md) for
mode-specific variables, Railway examples, network cutover and launch checks.
Use [`ADMIN.md`](ADMIN.md) for administrator setup and moderation operations.

## Project map

NimHunt intentionally retains a flat Python layout. The main groups are:

| Area | Primary files |
|---|---|
| Application and routes | `main.py`, `public_html.py`, `templates/`, `static/` |
| Persistence and public cache | `database.py`, `db_access.py`, `cache.py` |
| Draft creation and funding | `draft_creation.py`, `funding_flow.py`, `funding_monitor.py`, `funding_fee_worker.py` |
| Wallet and transaction lifecycle | `wallet.py`, `trans_updater.py`, `transaction_descriptions.py`, `refund_address_safety.py`, `cancellation_safety.py` |
| Claim identity and authorization | `claim_identity.py`, `claim_authorization.py`, `claim_security.py`, `claim_http.py` |
| Claim evidence and exposure guards | `fresh_claim_guard.py`, `claim_location_guard.py`, `location_behavior_guard.py`, `wallet_cluster_guard.py`, `claim_payout_throttle.py` |
| Settlement | `settlement_updater.py`, `claim_settlement_security.py` |
| Administration | `admin_panel.py`, `admin_auth.py`, `admin_store.py`, `admin_moderation.py` |
| Nimiq helper | `helpers/nimiq_helper.mjs` and its Node tests |
| Verification | `tests/`, `helpers/*.test.mjs`, `static/*.test.mjs` |

The security modules are composed in a deliberate order; consult the
[security architecture and audit map](docs/security-architecture.md) before
changing their wrappers or transaction boundaries.

## Documentation

- [`SECURITY.md`](SECURITY.md) — disclosure process, trust model, claim controls
  and operational security assumptions.
- [`docs/security-architecture.md`](docs/security-architecture.md) — detailed
  security ownership, end-to-end flows, persistence and concurrency boundaries.
- [`docs/configuration.md`](docs/configuration.md) — environment variables,
  deployment modes, Railway setup and launch checks.
- [`ADMIN.md`](ADMIN.md) — administrator authentication, moderation and Spot-ban
  safety.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development workflow and required checks.
- [`AGENTS.md`](AGENTS.md) — repository-specific guidance for coding agents.

## License

NimHunt is available under the [MIT License](LICENSE).
