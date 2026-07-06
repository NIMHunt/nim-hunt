cat > README.md <<'EOF'
# NimHunt

NimHunt is a simple geofaucet-style web app for the Nimiq cryptocurrency.

Creators can create funded geographic "spots"; users can find nearby spots and make claims from inside the required radius. The app is currently a local development project built with FastAPI, Jinja templates, SQLite, and static JavaScript/CSS.

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

pip install -r requirements.txt
Run locally

Start the development server:

./nimhunt_start_dev.sh

Then open:

http://127.0.0.1:8000/
Reset mock data

This clears and recreates the local development database:

./nimhunt_reset_mock_data.sh

Restart the server afterwards so the in-memory cache reloads.

Phone testing

To test on a phone through HTTPS, run the server first, then in a second terminal:

npx localtunnel --port 8000
