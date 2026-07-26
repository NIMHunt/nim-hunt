#!/usr/bin/env bash
set -euo pipefail

DEPLOYMENT_MODE="${NIMHUNT_DEPLOYMENT_MODE:-}"
LEGACY_PRODUCTION="${NIMHUNT_PRODUCTION:-}"

case "$DEPLOYMENT_MODE" in
    public-testnet|production)
        echo "Refusing to start the development server in public deployment mode: $DEPLOYMENT_MODE."
        exit 1
        ;;
    development|"")
        ;;
    *)
        echo "Refusing to start the development server: unknown NIMHUNT_DEPLOYMENT_MODE=$DEPLOYMENT_MODE."
        exit 1
        ;;
esac

case "$LEGACY_PRODUCTION" in
    1|true|TRUE|yes|YES|on|ON)
        echo "Refusing to start the development server while legacy NIMHUNT_PRODUCTION is enabled."
        exit 1
        ;;
esac

# NimHunt development launcher
# Starts the FastAPI server with the Nimiq TestAlbatross helper enabled.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${NIMHUNT_PROJECT_DIR:-$SCRIPT_DIR}"
HOST="${NIMHUNT_HOST:-0.0.0.0}"
PORT="${NIMHUNT_PORT:-8000}"
HELPER_PATH="${PROJECT_DIR}/helpers/nimiq_helper.mjs"
NIMIQ_CORE_PATH="${PROJECT_DIR}/helpers/node_modules/@nimiq/core"

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    echo "Could not find venv at: ${PROJECT_DIR}/venv"
    echo "Create/restore the virtual environment before running this launcher."
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [ ! -f "$HELPER_PATH" ]; then
    echo "Could not find the Nimiq helper at: ${HELPER_PATH}"
    echo "Expected helper file: helpers/nimiq_helper.mjs"
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [ ! -d "$NIMIQ_CORE_PATH" ]; then
    echo "Could not find the Nimiq helper dependencies."
    echo "Run: npm ci --prefix helpers"
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [ -z "${NIMHUNT_NIMIQ_MNEMONIC:-}" ]; then
    echo "NIMHUNT_NIMIQ_MNEMONIC is not set."
    echo "Export a dedicated TestAlbatross mnemonic before starting NimHunt."
    echo "The repository no longer contains a built-in development mnemonic."
    read -r -p "Press Enter to close..." _
    exit 1
fi

source venv/bin/activate

export NIMHUNT_DEPLOYMENT_MODE="${NIMHUNT_DEPLOYMENT_MODE:-development}"

# Development/TestAlbatross settings. The signing mnemonic must be supplied
# explicitly through NIMHUNT_NIMIQ_MNEMONIC and must never be committed.
export NIMHUNT_NIMIQ_NETWORK="${NIMHUNT_NIMIQ_NETWORK:-TestAlbatross}"
export NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND="${NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND:-node \"${HELPER_PATH}\"}"
export NIMHUNT_NIMIQ_SEND_COMMAND="${NIMHUNT_NIMIQ_SEND_COMMAND:-node \"${HELPER_PATH}\"}"

# Optional: set a real TestAlbatross fee address in your shell before launching.
# export NIMHUNT_SPOT_CANCELLATION_FEE_ADDRESS="NQ.. your fee address .."

echo "Starting NimHunt"
echo "Project: ${PROJECT_DIR}"
echo "Network: ${NIMHUNT_NIMIQ_NETWORK}"
echo "Helper:  ${HELPER_PATH}"
echo "URL:     http://127.0.0.1:${PORT}"
echo
echo "Press Ctrl+C to stop the server."
echo

uvicorn main:app --host "$HOST" --port "$PORT"
