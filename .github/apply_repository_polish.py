from pathlib import Path


readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")

old_header = """# NimHunt

![CI](https://github.com/NIMHunt/nim-hunt/actions/workflows/ci.yml/badge.svg)

NimHunt is a small geofaucet-style and Prizedraw mini-app for Nimiq Pay.

Creators fund geographic **Spots** with NIM. Other users find those Spots on a
map and can claim or enter them only when they are within the configured radius.
NimHunt is designed for a coding competition and modest community use rather
than high-volume financial infrastructure.

## Features
"""

new_header = """# NimHunt

[![CI](https://github.com/NIMHunt/nim-hunt/actions/workflows/ci.yml/badge.svg)](https://github.com/NIMHunt/nim-hunt/actions/workflows/ci.yml)
[![Live site](https://img.shields.io/badge/live-nimhunt.app-21bca5)](https://nimhunt.app)
[![License: MIT](https://img.shields.io/badge/license-MIT-5c5ce0)](LICENSE)

**[Open NimHunt](https://nimhunt.app)** · **[Open Nimiq Pay](https://nimpay.app)** · [Architecture](#architecture) · [Testing](#testing-and-quality-checks) · [Deployment](#public-deployment)

[![NimHunt map preview](static/images/nimhunt-default-social-card.png)](https://nimhunt.app)

NimHunt is a mobile-first geofaucet and Prizedraw mini-app for Nimiq Pay.
Creators fund real-world geographic **Spots** with NIM; other users discover them
on a map and can claim a reward or enter a draw only from inside the configured
area.

The project is live on MainAlbatross and was built for a coding competition and
modest community use. It deliberately favours transparent rules, conservative
financial safeguards and a small, understandable stack over high-volume
infrastructure.

## At a glance

- **Real Nimiq integration** — Nimiq Pay handles creator funding and participant
  addresses; NimHunt independently verifies transactions through Nimiq RPC.
- **Two reward formats** — immediate Standard Spot rewards and randomly selected
  Prizedraw winners.
- **Location-aware participation** — radius checks, optional stay durations,
  scheduling, participant limits and one-time claim codes.
- **Durable financial workflows** — outgoing intents are recorded before
  broadcast, ambiguous sends are not blindly retried, and winner sets are stored
  before payouts begin.
- **Production separation** — development, public TestAlbatross and MainAlbatross
  modes have explicit safety boundaries and network-identity checks.
- **Automated quality gates** — Python and Node tests, linting, syntax checks,
  template compilation, fresh database seeding and dependency audits run in CI.

## Features
"""

if old_header not in readme:
    raise SystemExit("README opening block no longer matches the expected source")
readme = readme.replace(old_header, new_header, 1)

old_schema = """NimHunt currently follows a fresh-development-database policy rather than
maintaining a general migration framework. The creation-fee release raises the
schema to version `2`, so after pulling this release stop the server and run
this reset once before ordinary local testing. Never use the reset script on a
public deployment database; public TestAlbatross and MainAlbatross must use fresh
persistent databases for this release.
"""
new_schema = """NimHunt currently follows a fresh-development-database policy rather than
maintaining a general migration framework. The current release uses schema
version `3`, so after pulling a change that updates the schema, stop the server
and run this reset before ordinary local testing. Never use the reset script on
a public deployment database; public TestAlbatross and MainAlbatross must use
fresh persistent databases for this release.
"""
if old_schema not in readme:
    raise SystemExit("README schema note no longer matches the expected source")
readme = readme.replace(old_schema, new_schema, 1)
readme = readme.replace(
    "git clone git@github.com:NIMHunt/nim-hunt.git",
    "git clone https://github.com/NIMHunt/nim-hunt.git",
    1,
)
readme_path.write_text(readme, encoding="utf-8")

database_path = Path("database.py")
database = database_path.read_text(encoding="utf-8")
old_database_docstring = '''"""
─────────────────────────────────────────────

database.py

The back-end of the system. Handles the database.

─────────────────────────────────────────────
"""
'''
new_database_docstring = '''"""SQLite schema, connection management, and durable deployment identity.

This module defines NimHunt's tables, indexes, triggers, schema version, and
connection helpers. Validated application reads and writes belong in
``db_access.py``; route and financial workflows should use that boundary rather
than embedding ad-hoc SQL.
"""
'''
if old_database_docstring not in database:
    raise SystemExit("database.py opening docstring no longer matches")
database_path.write_text(
    database.replace(old_database_docstring, new_database_docstring, 1),
    encoding="utf-8",
)

cache_path = Path("cache.py")
cache_text = cache_path.read_text(encoding="utf-8")
old_cache_line = "This cache is deliberately simple, but now has two layers:"
new_cache_line = "This cache is deliberately simple, but has three layers:"
if old_cache_line not in cache_text:
    raise SystemExit("cache.py layer description no longer matches")
cache_path.write_text(
    cache_text.replace(old_cache_line, new_cache_line, 1),
    encoding="utf-8",
)

Path("CONTRIBUTING.md").write_text(
    """# Contributing to NimHunt

Thank you for helping improve NimHunt. The project is intentionally small and
conservative: clear changes with focused tests are preferred to broad rewrites.

## Development setup

1. Install Python 3.11 or newer, Node.js 20 or newer, and npm.
2. Create and activate a virtual environment.
3. Install the pinned dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
npm ci --prefix helpers
```

For ordinary local development, supply a dedicated TestAlbatross mnemonic and
run `./nimhunt_start_dev.sh`. Never use production signing material locally and
never commit a mnemonic, passphrase, database, `.env` file, or API credential.

## Change discipline

- Work on a separate branch and open a pull request against `main`.
- Keep each pull request narrow and explain its user-visible and operational
  impact.
- Reuse existing components and conventions before introducing new abstractions.
- Do not combine unrelated visual, behavioural, and financial changes.
- Prefer explicit validation and durable database state over timing assumptions.

## Financial and blockchain boundaries

Treat wallet, funding, payout, refund, fee, transaction-reconciliation,
Prizedraw settlement, and database-finality code as high risk.

A pull request touching those paths should explain:

- which invariant is being protected;
- how duplicate or ambiguous sends are prevented;
- what durable database state is written before broadcast;
- how retries and partial failures behave;
- which focused regression and concurrency tests cover the change.

Do not weaken a fail-closed path merely to make a test or local demonstration
more convenient.

## Verification

Run the complete deterministic checks before requesting review:

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

GitHub Actions repeats the full suite, compiles every Jinja template, seeds a
fresh development database, and audits Python and production Node dependencies.

## Pull request checklist

- Describe the problem and the smallest safe solution.
- State clearly whether financial, database, wallet, or blockchain behaviour
  changes.
- Add or update focused regression tests.
- Include manual checks for browser- or device-specific behaviour.
- Confirm no generated files, local databases, secrets, or temporary diagnostic
  helpers remain in the final diff.
- Prefer **Squash and merge** so exploratory branch history does not clutter
  `main`.
""",
    encoding="utf-8",
)

Path("SECURITY.md").write_text(
    """# Security Policy

NimHunt handles cryptocurrency transactions and location-based eligibility.
Security reports should therefore be treated carefully and privately.

## Supported version

The current `main` branch and the live deployment are the supported version.
Older commits and abandoned feature branches are not maintained releases.

## Reporting a vulnerability

Do not open a public issue when a report could expose:

- private signing material or credentials;
- a way to redirect, duplicate, suppress, or falsely confirm a payment;
- a method for bypassing claim, ownership, location, or Prizedraw rules;
- sensitive user or deployment information.

Use GitHub's private vulnerability-reporting option when it is available. If it
is not available, contact the repository owner privately through GitHub before
sharing technical details publicly.

Include the affected route or module, reproduction steps, expected and observed
behaviour, and whether any real funds or secrets may be at risk. Use testnet and
minimal values for reproduction whenever possible.

## Operational caution

Never attach mnemonics, passphrases, `.env` files, production databases, private
RPC credentials, or unredacted logs to an issue or pull request. On-chain
transfers are irreversible; ambiguous outgoing transactions must remain blocked
for reconciliation rather than being retried speculatively.

NimHunt has substantial automated regression coverage but has not received an
independent security audit. The repository documents its intended modest-use
scope and operational limitations in `README.md`.
""",
    encoding="utf-8",
)

Path(".github/pull_request_template.md").write_text(
    """## Summary

<!-- What problem does this solve, and what is the smallest safe change? -->

## Scope

<!-- List the files or product areas intentionally changed. -->

## Safety boundary

- [ ] This does not change wallet, transaction, payout, refund, fee, settlement,
      Prizedraw winner-selection, or database-finality behaviour.
- [ ] Or: the financial/blockchain impact and protected invariant are explained
      below.

<!-- Delete the line that does not apply and explain any high-risk change. -->

## Verification

- [ ] Focused regression tests added or updated
- [ ] Full Python test suite
- [ ] Node helper tests
- [ ] Ruff and Python compilation
- [ ] JavaScript, helper, and shell syntax checks
- [ ] Jinja template compilation
- [ ] Fresh development database seed
- [ ] Dependency checks and audits

## Manual checks

<!-- Include browser, phone, Nimiq Pay, TestAlbatross, or deployment checks. -->

## Repository hygiene

- [ ] No secrets, local databases, generated caches, logs, or temporary
      diagnostic files are included
- [ ] Documentation and comments match the final behaviour
""",
    encoding="utf-8",
)

for temporary_path in (
    Path(".github/apply_repository_polish.py"),
    Path(".github/workflows/apply-repository-polish.yml"),
    Path(".github/workflows/apply-repository-polish-pr.yml"),
):
    if temporary_path.exists():
        temporary_path.unlink()
