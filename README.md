# NimHunt

NimHunt is a simple geofaucet-style and prizedraw mini-app for NimPay.

Creators can create Nimiq-funded geographic "spots"; other users can search for these spots and claim a reward once they are within the spot's required radius. Spots can include optional rules and formats so creators can make simple public drops, password-gated rewards, timed location challenges, or Prizedraw-style entries.

## Features

- **Geographic spots**: creators place a funded spot at a real-world location and choose the claim radius.
- **Password-protected spots**: creators can require a password or claim code before a user can claim from a spot.
- **Stay duration**: creators can require users to remain within the spot radius for a set amount of time before claiming.
- **Prizedraws**: creators can create Prizedraw spots where eligible users enter the draw and one or more winners receive the funded rewards.
- **Start and end times**: spots can be scheduled to begin later and expire after a chosen duration.
- **Claim limits**: creators can control how many total claims or entries are available and how many times each user can participate.
- **Creator tools**: users can review their own spots, drafts, deposits, publishing status, and previous activity.
- **Claim history**: users can review claims they have made and check their status.

## Current status

NimHunt is at release-candidate stage for a small coding-competition launch.
The repository defaults remain deliberately development-friendly and use
TestAlbatross unless production mode is explicitly configured.

Before accepting real funds, deploy with the production settings below, use
persistent storage with backups, and complete one small-value MainAlbatross
funding, claim, Prizedraw, and cancellation test. NimHunt has extensive automated
coverage, but it has not received an independent security audit.

## Tech stack

- Python
- FastAPI
- Jinja templates
- SQLite
- aiosqlite
- Static JavaScript and CSS
- Nimiq helper scripts for address derivation and transaction signing

## Local setup

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the Python and Nimiq-helper dependencies:

```bash
pip install -r requirements.txt
npm ci --prefix helpers
```

The helper uses the pinned official `@nimiq/core` package to derive deposit
addresses and construct server-generated transactions.

## Run locally

Start the development server:

```bash
./nimhunt_start_dev.sh
```

Then open:

```text
http://127.0.0.1:8000/
```

## Reset mock data

This clears and recreates the local development database:

```bash
./nimhunt_reset_mock_data.sh
```

Restart the server afterwards so the in-memory cache reloads.

## Phone testing

To test on a phone through HTTPS, run the server first, then in a second terminal:

```bash
npx localtunnel --port 8000
```

Open the HTTPS URL shown by localtunnel.

## Files not committed

The repository deliberately ignores local, generated, and private files such as:

- `venv/`
- `records.db`
- `.env`
- `x-dob.txt`
- cache files
- logs

## Production configuration

Do not use either development shell script in production. They deliberately
refuse to run when `NIMHUNT_PRODUCTION=1`. Start the FastAPI application with
Uvicorn (or an equivalent process manager) after setting the production
environment. A minimal configuration looks like this:

```bash
export NIMHUNT_PRODUCTION=1
export NIMHUNT_NIMIQ_NETWORK=MainAlbatross
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.nimiqwatch.com
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq.com
export NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS='NQ.. production fee address ..'
export NIMHUNT_DB_PATH=/absolute/path/to/persistent/records.db
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND='node /absolute/path/to/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_SEND_COMMAND='node /absolute/path/to/helpers/nimiq_helper.mjs'
export NIMHUNT_NIMIQ_MNEMONIC='your private production mnemonic'

uvicorn main:app --host 0.0.0.0 --port 8000
```

Treat the mnemonic as a secret: supply it through the deployment platform's
secret store and never commit it. A custom signer may be used instead of the
bundled helper; the configured commands only need to honour the JSON contract
documented in `wallet.py`.

Production startup validates the network and network ID, requires mainnet RPC
and Hub endpoints, requires real derivation/send commands, rejects development
mnemonics and placeholder addresses, and performs an initial cache, settlement,
and transaction-reconciliation pass before accepting traffic. If those checks
fail, fix the configuration rather than bypassing them.

The public RPC URL above is a convenient default for a small deployment. It has
no application-specific availability guarantee, so `NIMHUNT_NIMIQ_RPC_URL` can
be pointed at a node or provider you trust. Ensure `NIMHUNT_DB_PATH` lives on
persistent storage and is included in backups.
