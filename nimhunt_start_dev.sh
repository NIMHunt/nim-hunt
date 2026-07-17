#!/usr/bin/env bash
set -euo pipefail

case "${NIMHUNT_PRODUCTION:-}" in
    1|true|TRUE|yes|YES|on|ON)
        echo "Refusing to start the development server while NIMHUNT_PRODUCTION is enabled."
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

source venv/bin/activate

# Development/TestAlbatross settings.
# Replace the default-test-mnemonic setting with NIMHUNT_NIMIQ_MNEMONIC before using real funds.
export NIMHUNT_NIMIQ_NETWORK="${NIMHUNT_NIMIQ_NETWORK:-TestAlbatross}"
export NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC="${NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC:-1}"
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
