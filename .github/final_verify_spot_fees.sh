#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest ruff pip-audit
npm ci --prefix helpers

PYTHONPATH=. python -W error::ResourceWarning -m pytest -q | tee /tmp/python-tests.txt
tail -n 3 /tmp/python-tests.txt > /tmp/verification-summary.txt

npm --prefix helpers test | tee /tmp/node-tests.txt
printf '\nNode test summary:\n' >> /tmp/verification-summary.txt
tail -n 8 /tmp/node-tests.txt >> /tmp/verification-summary.txt

python -m py_compile *.py tests/*.py
ruff check .
for file in static/*.js; do node --check --input-type=module < "$file"; done
for file in helpers/*.mjs; do node --check "$file"; done
for file in *.sh; do bash -n "$file"; done
python -m pip check
python -m pip_audit
npm audit --omit=dev --prefix helpers
git diff --check

python - <<'PY'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
root = Path('templates')
env = Environment(loader=FileSystemLoader(str(root)))
templates = sorted(path.relative_to(root).as_posix() for path in root.rglob('*.html'))
for template in templates:
    env.get_template(template)
print(f'Compiled {len(templates)} templates')
PY

export NIMHUNT_DB_PATH=/tmp/nimhunt-final-fee-mock.db
rm -f "$NIMHUNT_DB_PATH" "$NIMHUNT_DB_PATH-wal" "$NIMHUNT_DB_PATH-shm"
python - <<'PY'
import asyncio
import sqlite3
import spoof
import constants as const
import database as schema

result = asyncio.run(spoof.seed_mock_data())
assert result['published_spot_count'] == 8, result
with sqlite3.connect(schema.DB_PATH) as db:
    assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    columns = {row[1] for row in db.execute('PRAGMA table_info(spot)')}
    assert {'creation_fee', 'creation_fee_address'} <= columns
    missing = db.execute(
        '''
        SELECT COUNT(*)
        FROM spot s
        WHERE s.s_status = ?
          AND s.creation_fee > 0
          AND NOT EXISTS (
              SELECT 1 FROM trans t
              WHERE t.spot_id = s.id
                AND t.type = ?
                AND t.t_status = ?
                AND t.user_id = s.created_by
                AND t.amount = s.creation_fee
                AND UPPER(REPLACE(t.from_address, ' ', '')) = UPPER(REPLACE(s.deposit_address, ' ', ''))
                AND UPPER(REPLACE(t.to_address, ' ', '')) = UPPER(REPLACE(s.creation_fee_address, ' ', ''))
          )
        ''',
        (const.SPOT_STATUS_PUBLISHED, const.TRANS_TYPE_CREATION_FEE, const.TRANS_STATUS_CONFIRMED),
    ).fetchone()[0]
    assert missing == 0
print('Fresh database and exact creation-fee lifecycle verified')
PY

export NIMHUNT_DB_PATH=/tmp/nimhunt-final-fee-development.db
rm -f "$NIMHUNT_DB_PATH" "$NIMHUNT_DB_PATH-wal" "$NIMHUNT_DB_PATH-shm"
python - <<'PY'
import asyncio
import main
async def exercise():
    await main.startup()
    await main.shutdown()
asyncio.run(exercise())
print('Development startup/shutdown passed')
PY

export NIMHUNT_DEPLOYMENT_MODE=public-testnet
export NIMHUNT_DB_PATH=/tmp/nimhunt-final-fee-public-testnet.db
export NIMHUNT_NIMIQ_NETWORK=TestAlbatross
export NIMHUNT_NIMIQ_NETWORK_ID=5
export NIMHUNT_NIMIQ_RPC_URL=https://rpc.testnet.nimiqwatch.com/
export NIMHUNT_NIMIQ_HUB_URL=https://hub.nimiq-testnet.com
export NIMHUNT_NIMIQ_MNEMONIC="$(printf '%s' 'bGVnYWwgd2lubmVyIHRoYW5rIHllYXIgd2F2ZSBzYXVzYWdlIHdvcnRoIHVzZWZ1bCBsZWdhbCB3aW5uZXIgdGhhbmsgeWVsbG93' | base64 -d)"
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND="node $GITHUB_WORKSPACE/helpers/nimiq_helper.mjs"
export NIMHUNT_NIMIQ_SEND_COMMAND="node $GITHUB_WORKSPACE/helpers/nimiq_helper.mjs"
export NIMHUNT_STANDARD_SPOT_CREATION_FEE_NIM=1
export NIMHUNT_PRIZEDRAW_SPOT_CREATION_FEE_NIM=2
export NIMHUNT_SPOT_CANCELLATION_FEE_NIM=1
export NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS='NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF'
export NIMHUNT_NIMIQ_TRANSACTION_FEE=0

for attempt in 1 2 3; do
    rm -f "$NIMHUNT_DB_PATH" "$NIMHUNT_DB_PATH-wal" "$NIMHUNT_DB_PATH-shm"
    if python - <<'PY'
import asyncio
import sqlite3
import database
import main
async def exercise():
    await main.startup()
    response = await main.healthz()
    assert response.status_code == 200
    await main.shutdown()
asyncio.run(exercise())
with sqlite3.connect(database.DB_PATH) as db:
    metadata = dict(db.execute('SELECT key, value FROM app_metadata'))
    assert metadata['nimiq_network'] == 'TestAlbatross'
    assert metadata['nimiq_network_id'] == '5'
    assert metadata['deployment_mode'] == 'public-testnet'
    assert db.execute('PRAGMA user_version').fetchone()[0] == 2
print('Public TestAlbatross startup passed without broadcasting')
PY
    then
        exit 0
    fi
    echo "Public TestAlbatross dependency unavailable on attempt $attempt; retrying..."
    sleep 10
done
exit 1
