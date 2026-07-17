#!/usr/bin/env bash
set -euo pipefail

case "${NIMHUNT_PRODUCTION:-}" in
    1|true|TRUE|yes|YES|on|ON)
        echo "Refusing to reset mock data while NIMHUNT_PRODUCTION is enabled."
        exit 1
        ;;
esac

# NimHunt fresh-database and mock-data helper
# Stop the FastAPI server before using this: the old local database is deleted.

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

echo "Recreating NimHunt development data in: ${PROJECT_DIR}"
echo "The FastAPI server must be stopped before continuing."
echo "This will delete records.db (and SQLite sidecars), create the current schema, and add mock data."
echo
read -r -p "Continue? [y/N] " answer
case "$answer" in
    y|Y|yes|YES)
        python spoof.py
        echo
        echo "Done. A fresh records.db and mock dataset were created."
        echo "You can now start the FastAPI server."
        ;;
    *)
        echo "Cancelled."
        ;;
esac

read -r -p "Press Enter to close..." _
