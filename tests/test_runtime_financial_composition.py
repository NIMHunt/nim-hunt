from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def test_production_runtime_installs_financial_guards_but_keeps_core_deposit_recorder():
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        env = {
            **os.environ,
            "NIMHUNT_DB_PATH": db_file.name,
            "NIMHUNT_DEPLOYMENT_MODE": "development",
            "NIMHUNT_PRODUCTION": "",
            "NIMHUNT_NIMIQ_NETWORK": "TestAlbatross",
            "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC": "1",
        }
        code = """
import main
import public_html
import trans_updater
assert trans_updater.record_spot_deposit_transaction.__module__ == 'trans_updater'
assert trans_updater.submit_spot_cancellation_transactions.__module__ == 'cancellation_safety'
assert trans_updater.submit_spot_creation_fee_transaction.__module__ == 'funding_fee_worker'
assert public_html._deposit_summary.__module__ == 'funding_status'
print('runtime-financial-composition-ok')
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime-financial-composition-ok" in result.stdout
