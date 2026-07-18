#!/usr/bin/env bash
set -euo pipefail

stage=${1:?verification stage is required}

case "$stage" in
  install)
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt pytest ruff pip-audit
    npm ci --prefix helpers
    ;;

  python-tests)
    PYTHONPATH=. python -W error::ResourceWarning -m pytest -q | tee /tmp/python-tests.txt
    tail -n 3 /tmp/python-tests.txt > /tmp/verification-summary.txt
    ;;

  node-tests)
    npm --prefix helpers test | tee /tmp/node-tests.txt
    printf '\nNode test summary:\n' >> /tmp/verification-summary.txt
    tail -n 8 /tmp/node-tests.txt >> /tmp/verification-summary.txt
    ;;

  static)
    python -m py_compile *.py tests/*.py
    ruff check .
    for file in static/*.js; do node --check --input-type=module < "$file"; done
    for file in helpers/*.mjs; do node --check "$file"; done
    for file in *.sh; do bash -n "$file"; done
    git diff --check
    ;;

  audits)
    python -m pip check
    python -m pip_audit
    npm audit --omit=dev --prefix helpers
    ;;

  templates)
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
    ;;

  mock-db)
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
    ;;

  development)
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
    ;;

  public-testnet)
    export NIMHUNT_NIMIQ_MNEMONIC="$(printf '%s' "$TEST_MNEMONIC_B64" | base64 -d)"
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
    ;;

  *)
    echo "Unknown verification stage: $stage" >&2
    exit 2
    ;;
esac
