# Contributing to NimHunt

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
git diff --check
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
