#!/usr/bin/env bash
set -euo pipefail

DEPLOYMENT_MODE="${NIMHUNT_DEPLOYMENT_MODE:-}"
LEGACY_PRODUCTION="${NIMHUNT_PRODUCTION:-}"

case "$DEPLOYMENT_MODE" in
    public-testnet|production)
        echo "Refusing to reset mock data in public deployment mode: $DEPLOYMENT_MODE."
        exit 1
        ;;
    development|"")
        ;;
    *)
        echo "Refusing to reset mock data: unknown NIMHUNT_DEPLOYMENT_MODE=$DEPLOYMENT_MODE."
        exit 1
        ;;
esac

case "$LEGACY_PRODUCTION" in
    1|true|TRUE|yes|YES|on|ON)
        echo "Refusing to reset mock data while legacy NIMHUNT_PRODUCTION is enabled."
        exit 1
        ;;
esac

# NimHunt fresh-database and mock-data helper
# Stop the FastAPI server before using this: the old local database is deleted.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${NIMHUNT_PROJECT_DIR:-$SCRIPT_DIR}"
HELPER_PATH="${PROJECT_DIR}/helpers/nimiq_helper.mjs"
NIMIQ_CORE_PATH="${PROJECT_DIR}/helpers/node_modules/@nimiq/core"

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    echo "Could not find venv at: ${PROJECT_DIR}/venv"
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [ ! -f "$HELPER_PATH" ] || [ ! -d "$NIMIQ_CORE_PATH" ]; then
    echo "Could not find the Nimiq helper or its dependencies."
    echo "Run: npm ci --prefix helpers"
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [ -z "${NIMHUNT_NIMIQ_MNEMONIC:-}" ]; then
    echo "NIMHUNT_NIMIQ_MNEMONIC is not set."
    echo "Export a dedicated TestAlbatross mnemonic before resetting mock data."
    echo "The repository no longer contains a built-in development mnemonic."
    read -r -p "Press Enter to close..." _
    exit 1
fi

source venv/bin/activate

export NIMHUNT_DEPLOYMENT_MODE="${NIMHUNT_DEPLOYMENT_MODE:-development}"

export NIMHUNT_NIMIQ_NETWORK="${NIMHUNT_NIMIQ_NETWORK:-TestAlbatross}"
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND="${NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND:-node \"${HELPER_PATH}\"}"
export NIMHUNT_NIMIQ_SEND_COMMAND="${NIMHUNT_NIMIQ_SEND_COMMAND:-node \"${HELPER_PATH}\"}"

DATABASE_PATH="${NIMHUNT_DB_PATH:-records.db}"

echo "Recreating NimHunt development data in: ${PROJECT_DIR}"
echo "The FastAPI server must be stopped before continuing."
echo "This will delete ${DATABASE_PATH} (and SQLite sidecars), create the current schema, and add mock data."
echo
read -r -p "Continue? [y/N] " answer
case "$answer" in
    y|Y|yes|YES)
        python spoof.py
        echo
        echo "Done. A fresh ${DATABASE_PATH} and mock dataset were created."
        echo "You can now start the FastAPI server."
        ;;
    *)
        echo "Cancelled."
        ;;
esac

read -r -p "Press Enter to close..." _
