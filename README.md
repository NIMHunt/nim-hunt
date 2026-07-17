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

This repository is mainly preserved for ongoing bug fixes and development.

The app currently uses local development settings and test/mock data. Do not use this with real funds until the production wallet, deployment, secret handling, and safety settings have been reviewed.

## Tech stack

- Python
- FastAPI
- Jinja templates
- SQLite
- aiosqlite
- Static JavaScript and CSS
- Nimiq helper scripts for test/development wallet integration

## Local setup

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

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

## Production warning

Before any public deployment, review the wallet settings, secret handling, database persistence, and Nimiq network settings. Set `NIMHUNT_PRODUCTION=1` in production; this automatically disables NimHunt's desktop test user, Test Location control, placeholder deposit addresses, and unencrypted development seed. Startup also refuses unsafe development settings. Production deployments must set `NIMIQ_NETWORK` to `MainAlbatross`, use a non-testnet `NIMIQ_HUB_URL`, configure private signing material, and replace the development `SPOT_CANCELLATION_FEE_ADDRESS` with a production address.
