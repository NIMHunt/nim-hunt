#!/usr/bin/env bash
set -euo pipefail

# NimHunt mock-data reset helper
# Use this only when you want to wipe and recreate the local development database.

PROJECT_DIR="${NIMHUNT_PROJECT_DIR:-/home/jakorah/nim-hunt}"
HELPER_PATH="${PROJECT_DIR}/helpers/nimiq_helper.mjs"

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    echo "Could not find venv at: ${PROJECT_DIR}/venv"
    read -r -p "Press Enter to close..." _
    exit 1
fi

source venv/bin/activate

export NIMHUNT_NIMIQ_NETWORK="${NIMHUNT_NIMIQ_NETWORK:-TestAlbatross}"
export NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC="${NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC:-1}"
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND="${NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND:-node ${HELPER_PATH}}"
export NIMHUNT_NIMIQ_SEND_COMMAND="${NIMHUNT_NIMIQ_SEND_COMMAND:-node ${HELPER_PATH}}"

echo "Resetting NimHunt mock data in: ${PROJECT_DIR}"
echo "This will clear and recreate the local records.db test data."
echo
read -r -p "Continue? [y/N] " answer
case "$answer" in
    y|Y|yes|YES)
        python spoof.py
        echo
        echo "Done. Restart the FastAPI server so the cache reloads."
        ;;
    *)
        echo "Cancelled."
        ;;
esac

read -r -p "Press Enter to close..." _
