"""
trans_updater.py

NimHunt transaction outbox and chain-reconciliation service.

This module records durable outgoing-payment intents before broadcast, checks
pending transactions through Nimiq RPC, finalises verified deposits/payouts,
and coordinates creation fees, refunds and cancellation fees.

Typical scheduled use:

    import trans_updater

    result = await trans_updater.check_pending_transactions()

The cache remains a convenience layer. The database is still the source of
truth, and every final status change is written to SQLite before the cache is
updated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cache
import constants as const
import database as schema
import db_access
import wallet
from database import get_db
from transaction_descriptions import build_transaction_description

RowDict = dict[str, Any]

logger = logging.getLogger(__name__)
TransOutcome = Literal["pending", "confirmed", "failed", "unknown"]

# Public RPC servers are useful for development, but a production deployment
# should point this at your own node or a service you trust.
DEFAULT_NIMIQ_RPC_URL = str(getattr(const, "NIMIQ_RPC_URL", "")).strip()
DEFAULT_RPC_TIMEOUT_SECONDS = int(getattr(const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12))

# If a transaction hash cannot be found for this long, we treat it as failed.
# This is deliberately conservative. Adjust once you know the usual Nimiq Pay
# broadcast/confirmation timing in real use.
DEFAULT_FAIL_AFTER_SECONDS = int(os.getenv("NIMHUNT_TRANS_FAIL_AFTER_SECONDS", str(90 * 60)))

# Keep each updater pass small. If there are more pending transactions than
# this, the next scheduled call will pick up the next batch.
DEFAULT_MAX_CHECKS_PER_RUN = int(os.getenv("NIMHUNT_TRANS_MAX_CHECKS_PER_RUN", "100"))

# Background polling interval. constants.py defines this for normal app use;
# the environment variable is still useful when you want quicker local testing.
DEFAULT_TRANSACTION_CHECK_INTERVAL_SECONDS = int(os.getenv(
    "NIMHUNT_TRANSACTION_CHECK_INTERVAL_SECONDS",
    str(getattr(const, "TRANSACTION_CHECK_INTERVAL_SECONDS", 60)),
))

# Server-initiated sends are now recorded before they are broadcast. The
# temporary tx_hash keeps SQLite's existing NOT NULL + UNIQUE constraint happy.
# If the helper broadcasts but the later DB update fails, this local intent row
# prevents automatic retries and therefore avoids accidental double payment.
LOCAL_TRANSACTION_INTENT_PREFIX = "NIMHUNT_INTENT:"
_NIMIQ_TRANSACTION_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
DEFAULT_USER_DEPOSIT_STALE_AFTER_SECONDS = int(getattr(const, "USER_DEPOSIT_STALE_AFTER_SECONDS", 30 * 60))


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_TRANS_CHECK_TASK: asyncio.Task | None = None
_TRANS_CHECK_STOP_EVENT: asyncio.Event | None = None
_TRANS_CHECK_LAST_RESULT: RowDict | None = None
_TRANS_CHECK_LAST_ERROR: str | None = None
_CHAIN_HEAD_HEIGHT: int | None = None
_CHAIN_HEAD_UPDATED_AT: float | None = None
_CHAIN_HEAD_LAST_ERROR: str | None = None


@dataclass(slots=True)
class ChainTransactionStatus:
    """Result of checking one transaction hash against Nimiq RPC."""

    status: TransOutcome
    tx_hash: str
    block_number: int | None = None
    raw: Any | None = None
    reason: str | None = None


@dataclass(slots=True)
class SubmittedChainTransaction:
    """Result returned by the local Nimiq helper after a broadcast send."""

    tx_hash: str
    from_address: str
    to_address: str
    amount: int
    raw: Any | None = None


def _helper_seed_configured() -> bool:
    mnemonic_env = getattr(
        const, "NIMHUNT_NIMIQ_MNEMONIC_ENV", "NIMHUNT_NIMIQ_MNEMONIC"
    )
    return bool(os.getenv(mnemonic_env))


def _default_helper_command() -> list[str] | None:
    if not _helper_seed_configured():
        return None

    helper_path = os.getenv(getattr(const, "NIMHUNT_NIMIQ_HELPER_PATH_ENV", "NIMHUNT_NIMIQ_HELPER_PATH"))
    if helper_path:
        helper = Path(helper_path).expanduser()
    else:
        helper = Path(__file__).resolve().parent / "helpers" / "nimiq_helper.mjs"

    if not helper.exists():
        return None

    node_binary = os.getenv(getattr(const, "NIMHUNT_NIMIQ_NODE_BINARY_ENV", "NIMHUNT_NIMIQ_NODE_BINARY"), "node")
    return [node_binary, str(helper)]


def _helper_command_from_env(env_name: str) -> list[str] | None:
    value = os.getenv(env_name)
    if value and value.strip():
        return shlex.split(value)
    return _default_helper_command()


def _run_json_command_sync(command: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=int(getattr(const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12)) + 90,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Nimiq helper command was not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Nimiq helper command timed out") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        raise RuntimeError(
            "Nimiq helper command failed: "
            f"{wallet.redact_secret_values(stderr or stdout or completed.returncode)}"
        )

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Nimiq helper command returned invalid JSON: "
            f"{wallet.redact_secret_values((completed.stdout or '').strip()[:500])}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError("Nimiq helper command returned non-object JSON")
    if data.get("ok") is False:
        raise RuntimeError(
            wallet.redact_secret_values(
                data.get("message") or data.get("error") or "Nimiq helper failed"
            )
        )
    return data


def _integration_payload_base() -> dict[str, Any]:
    return {
        "app": getattr(const, "APP_NAME", "NimHunt"),
        "network": getattr(const, "NIMIQ_NETWORK", "TestAlbatross"),
        "network_id": int(getattr(const, "NIMIQ_NETWORK_ID", 5)),
        "rpc_url": getattr(const, "NIMIQ_RPC_URL", None),
        "fee": int(getattr(const, "NIMIQ_TRANSACTION_FEE", 0)),
    }


def _validate_nimiq_address(value: str, *, field_name: str = "address") -> str:
    """Return a checksum-valid Nimiq address in canonical display form.

    Outgoing sends should only accept NimHunt's fake development addresses when
    fake wallet sends are explicitly enabled. Merely allowing placeholder
    deposit-address generation for UI tests is not enough.
    """
    return wallet.normalise_nimiq_address(
        value,
        field_name=field_name,
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_SENDS", False)),
    )


def _make_local_intent_hash(*, kind: str, primary_id: int) -> str:
    """Return a unique local placeholder for a not-yet-broadcast send."""
    safe_kind = "".join(ch for ch in str(kind).lower() if ch.isalnum() or ch in {"_", "-"}) or "tx"
    return f"{LOCAL_TRANSACTION_INTENT_PREFIX}{safe_kind}:{int(primary_id)}:{secrets.token_urlsafe(18)}"


def _is_local_intent_hash(tx_hash: str) -> bool:
    return str(tx_hash or "").startswith(LOCAL_TRANSACTION_INTENT_PREFIX)


_SERVER_INITIATED_TRANSACTION_TYPES = frozenset({
    const.TRANS_TYPE_CANCEL_SPOT,
    const.TRANS_TYPE_CLAIM,
    const.TRANS_TYPE_PLAT_FEE,
    const.TRANS_TYPE_CREATION_FEE,
    const.TRANS_TYPE_REMAINDER_REFUND,
})


def _is_server_initiated_transaction(trans: RowDict) -> bool:
    """Return True when retrying this row could send NimHunt funds twice."""
    return int(trans.get(schema.TRANS_TYPE) or -1) in _SERVER_INITIATED_TRANSACTION_TYPES


def _normalise_address_for_compare(value: Any) -> str | None:
    """Return a comparable canonical address, or None if value is unusable."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return wallet.normalise_nimiq_address(
            raw,
            allow_dev_placeholder=bool(
                getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)
                or getattr(const, "ALLOW_DEV_WALLET_SENDS", False)
            ),
        )
    except ValueError:
        return None


def _validate_submitted_chain_send(
    result: SubmittedChainTransaction,
    *,
    expected_from_address: str,
    expected_to_address: str,
    expected_amount: int,
) -> SubmittedChainTransaction:
    """Require the helper result to match the payment NimHunt intended.

    The durable outbox row is created from NimHunt's own expected values. A
    helper is allowed to return canonical formatting, but it must not be able to
    replace the recipient or amount before later blockchain verification.
    """
    tx_hash = str(result.tx_hash or "").strip().lower()

    expected_from = _validate_nimiq_address(expected_from_address, field_name="expected from_address")
    expected_to = _validate_nimiq_address(expected_to_address, field_name="expected to_address")
    actual_from = _validate_nimiq_address(result.from_address, field_name="helper from_address")
    actual_to = _validate_nimiq_address(result.to_address, field_name="helper to_address")
    actual_amount = int(result.amount)
    expected_amount = int(expected_amount)

    if actual_from != expected_from:
        raise RuntimeError("Nimiq helper returned a sender that does not match the intended payment")
    if actual_to != expected_to:
        raise RuntimeError("Nimiq helper returned a recipient that does not match the intended payment")
    if actual_amount != expected_amount:
        raise RuntimeError("Nimiq helper returned an amount that does not match the intended payment")
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        raise RuntimeError("Nimiq helper returned an invalid transaction hash")

    return SubmittedChainTransaction(
        tx_hash=tx_hash,
        from_address=actual_from,
        to_address=actual_to,
        amount=actual_amount,
        raw=result.raw,
    )


def _walk_json(value: Any):
    """Yield every nested JSON-ish value breadth-first with its key path."""
    queue: list[tuple[tuple[str, ...], Any]] = [((), value)]
    index = 0
    while index < len(queue):
        path, item = queue[index]
        index += 1
        yield path, item
        if isinstance(item, dict):
            for key, child in item.items():
                queue.append(((*path, str(key)), child))
        elif isinstance(item, list):
            for idx, child in enumerate(item):
                queue.append(((*path, str(idx)), child))


def _first_address_for_keys(value: Any, keys: set[str]) -> str | None:
    keys_lc = {key.lower() for key in keys}
    for path, item in _walk_json(value):
        if not path or isinstance(item, (dict, list)):
            continue
        if path[-1].lower() not in keys_lc:
            continue
        normalised = _normalise_address_for_compare(item)
        if normalised:
            return normalised
    return None


def _first_positive_int_for_keys(value: Any, keys: set[str]) -> int | None:
    keys_lc = [key.lower() for key in keys]
    candidates: list[tuple[int, int, int]] = []
    for path, item in _walk_json(value):
        if not path or isinstance(item, (dict, list)):
            continue
        key = path[-1].lower()
        if key not in keys_lc:
            continue
        try:
            amount = int(str(item), 0) if isinstance(item, str) else int(item)
        except (TypeError, ValueError):
            continue
        if amount >= 0:
            # Prefer shallower fields, and prefer value over generic amount.
            priority = 0 if key == "value" else 1
            candidates.append((priority, len(path), amount))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _extract_chain_from_address(raw: Any) -> str | None:
    return _first_address_for_keys(
        raw,
        {
            "sender",
            "senderAddress",
            "sender_address",
            "from",
            "fromAddress",
            "from_address",
        },
    )


def _extract_chain_to_address(raw: Any) -> str | None:
    return _first_address_for_keys(
        raw,
        {
            "recipient",
            "recipientAddress",
            "recipient_address",
            "to",
            "toAddress",
            "to_address",
        },
    )


def _extract_chain_amount(raw: Any) -> int | None:
    return _first_positive_int_for_keys(raw, {"value", "amount"})


@dataclass(slots=True)
class VerifiedChainDetails:
    """On-chain details extracted from RPC and matched against one DB row."""

    ok: bool
    reason: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    amount: int | None = None


def _verify_chain_details_for_record(trans: RowDict, status: ChainTransactionStatus) -> VerifiedChainDetails:
    """Check that a confirmed chain transaction matches the stored TRANSACTION.

    This is intentionally strict for deposits: the transaction that unlocks a
    draft must be the transaction paying that SPOT's deposit address, for at
    least the amount NimHunt recorded as due. The same comparison is also useful
    for server-initiated claim/refund/fee sends.
    """
    raw = status.raw
    if _execution_result_is_failure(raw):
        return VerifiedChainDetails(
            ok=False,
            reason="confirmed transaction explicitly reported a failed execution result",
        )

    chain_from = _extract_chain_from_address(raw)
    chain_to = _extract_chain_to_address(raw)
    chain_amount = _extract_chain_amount(raw)

    expected_from = _normalise_address_for_compare(trans.get(schema.TRANS_FROM_ADDRESS))
    expected_to = _normalise_address_for_compare(trans.get(schema.TRANS_TO_ADDRESS))
    expected_amount = int(trans.get(schema.TRANS_AMOUNT) or 0)

    if chain_from is None:
        return VerifiedChainDetails(ok=False, reason="confirmed transaction did not expose a sender/from address")
    if chain_to is None:
        return VerifiedChainDetails(ok=False, reason="confirmed transaction did not expose a recipient/to address")
    if chain_amount is None:
        return VerifiedChainDetails(ok=False, reason="confirmed transaction did not expose an amount/value")

    # For user deposits, the client-supplied from_address is not trusted. The
    # chain sender replaces it so cancellation refunds go back to the real payer.
    if int(trans.get(schema.TRANS_TYPE) or -1) != const.TRANS_TYPE_FILL_SPOT:
        if expected_from is None:
            return VerifiedChainDetails(ok=False, reason="stored transaction sender/from address is invalid")
        if chain_from != expected_from:
            return VerifiedChainDetails(ok=False, reason="confirmed transaction sender does not match stored sender")

    if expected_to is None:
        return VerifiedChainDetails(ok=False, reason="stored transaction recipient/to address is invalid")
    if chain_to != expected_to:
        return VerifiedChainDetails(ok=False, reason="confirmed transaction recipient does not match expected recipient")

    trans_type = int(trans.get(schema.TRANS_TYPE) or -1)
    if trans_type == const.TRANS_TYPE_FILL_SPOT:
        # A creator may deliberately overfund a Spot. The confirmed chain amount
        # becomes the authoritative deposited value after verification.
        if int(chain_amount) < expected_amount:
            return VerifiedChainDetails(ok=False, reason="confirmed transaction amount is lower than recorded amount")
    elif int(chain_amount) != expected_amount:
        # Outgoing claim/refund/fee sends must match the exact recorded intent.
        # Accepting an overpayment would make a bad helper result look legitimate.
        return VerifiedChainDetails(ok=False, reason="confirmed outgoing transaction amount does not match recorded amount")

    return VerifiedChainDetails(
        ok=True,
        from_address=chain_from,
        to_address=chain_to,
        amount=int(chain_amount),
    )


async def submit_chain_send_from_spot_deposit(
    *,
    spot: RowDict,
    to_address: str,
    amount: int,
    memo: str | None = None,
) -> SubmittedChainTransaction:
    """Ask the local Nimiq helper to sign and broadcast a send from a SPOT deposit.

    This is deliberately in trans_updater.py rather than db_access.py or the
    page routes, because outgoing chain sends are transaction-management work.
    wallet.py remains responsible for deriving/storing deposit-address metadata.
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    from_address = _validate_nimiq_address(str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""), field_name="from_address")
    to_address = _validate_nimiq_address(to_address, field_name="to_address")

    key_index = spot.get(schema.SPOT_DEPOSIT_KEY_INDEX)
    if key_index is None:
        raise RuntimeError("spot has no deposit_key_index; cannot derive signing key")
    key_version = int(spot.get(schema.SPOT_DEPOSIT_KEY_VERSION) or getattr(const, "SPOT_DEPOSIT_KEY_VERSION", 1))
    key_path = str(spot.get(schema.SPOT_DEPOSIT_KEY_PATH) or wallet.spot_deposit_key_path(int(key_index), key_version=key_version))

    # Sanity check: address derivation belongs to wallet.py. Before sending,
    # require the stored address to still match the configured derivation path.
    derived = wallet.derive_spot_deposit_address(int(key_index), key_version=key_version)
    if derived.address != from_address:
        raise RuntimeError("stored deposit address does not match derived deposit address")

    command = _helper_command_from_env(getattr(const, "NIMHUNT_NIMIQ_SEND_COMMAND_ENV", "NIMHUNT_NIMIQ_SEND_COMMAND"))
    if command:
        data = await asyncio.to_thread(
            _run_json_command_sync,
            command,
            {
                **_integration_payload_base(),
                "action": "send_luna_from_spot_deposit",
                "from_address": from_address,
                "to_address": to_address,
                "amount": amount,
                "memo": memo,
                "deposit_key_index": int(key_index),
                "deposit_key_path": key_path,
                "deposit_key_version": key_version,
            },
        )
        tx_hash = str(data.get("tx_hash") or data.get("hash") or "").strip()
        if not tx_hash:
            raise RuntimeError("Nimiq helper did not return tx_hash/hash")
        return SubmittedChainTransaction(
            tx_hash=tx_hash,
            from_address=str(data.get("from_address") or from_address),
            to_address=str(data.get("to_address") or to_address),
            amount=int(data.get("amount") or amount),
            raw=data,
        )

    if not getattr(const, "ALLOW_DEV_WALLET_SENDS", False):
        raise RuntimeError(
            "Real Nimiq transaction signing is not connected. Install helpers/nimiq_helper.mjs "
            "and set NIMHUNT_NIMIQ_MNEMONIC, or set ALLOW_DEV_WALLET_SENDS=True "
            "only for local fake-hash tests."
        )

    # Local fake-send fallback, intentionally gated by ALLOW_DEV_WALLET_SENDS.
    result = await wallet.send_luna_from_spot_deposit(
        spot=spot,
        to_address=to_address,
        amount=amount,
        memo=memo,
    )
    return SubmittedChainTransaction(
        tx_hash=result.tx_hash,
        from_address=result.from_address,
        to_address=result.to_address,
        amount=result.amount,
        raw=result.raw,
    )



async def _submit_recorded_chain_send(
    db,
    *,
    spot: RowDict,
    to_address: str,
    amount: int,
    memo: str,
    intent_kind: str,
    intent_primary_id: int,
    create_transaction,
    create_transaction_kwargs: dict[str, Any],
    serialize_intent: bool = False,
) -> RowDict:
    """Create a durable pending TRANSACTION row, then broadcast the send.

    This is the lightweight outbox pattern used by NimHunt. The database row is
    committed before the helper is asked to broadcast. If the broadcast succeeds
    but the DB update with the real tx_hash fails, the local intent row remains
    pending and prevents an automatic duplicate send.
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    from_address = _validate_nimiq_address(str(spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""), field_name="from_address")
    clean_to_address = _validate_nimiq_address(to_address, field_name="to_address")
    intent_hash = _make_local_intent_hash(kind=intent_kind, primary_id=intent_primary_id)

    async with db_access.transaction(db, immediate=serialize_intent):
        trans_id = await create_transaction(
            db,
            **create_transaction_kwargs,
            amount=amount,
            from_address=from_address,
            to_address=clean_to_address,
            tx_hash=intent_hash,
        )

    try:
        result = await submit_chain_send_from_spot_deposit(
            spot=spot,
            to_address=clean_to_address,
            amount=amount,
            memo=memo,
        )
        result = _validate_submitted_chain_send(
            result,
            expected_from_address=from_address,
            expected_to_address=clean_to_address,
            expected_amount=amount,
        )
    except Exception as exc:
        # Do not mark the intent failed automatically. With real chain sends, a
        # timeout/crash can happen after broadcast but before the helper returns
        # a tx_hash. Leaving the local intent pending is annoying but safer than
        # retrying and possibly double-paying. A future admin recovery screen can
        # either attach the real tx_hash or mark the intent failed manually.
        await cache.notify_transaction_changed(
            db,
            trans_id=int(trans_id),
            spot_id=create_transaction_kwargs.get("spot_id") or spot.get(schema.SPOT_ID),
            user_id=create_transaction_kwargs.get("user_id"),
        )
        raise RuntimeError(
            f"Chain send did not return a usable transaction hash; local intent {trans_id} was left pending for safety: {exc}"
        ) from exc

    async with db_access.transaction(db):
        await db_access.update_transaction_chain_details(
            db,
            trans_id=int(trans_id),
            tx_hash=result.tx_hash,
            from_address=result.from_address,
            to_address=result.to_address,
            amount=result.amount,
        )

    await cache.notify_transaction_changed(
        db,
        trans_id=int(trans_id),
        spot_id=create_transaction_kwargs.get("spot_id") or spot.get(schema.SPOT_ID),
        user_id=create_transaction_kwargs.get("user_id"),
    )

    return {
        "ok": True,
        "trans_id": int(trans_id),
        "amount": int(result.amount),
        "to_address": result.to_address,
        "from_address": result.from_address,
        "tx_hash": result.tx_hash,
        "intent_hash": intent_hash,
    }

def _transaction_id(row: RowDict) -> int:
    return int(row[schema.TRANS_ID])


def _transaction_age_seconds(row: RowDict, *, now: int | None = None) -> int:
    now = int(time.time()) if now is None else int(now)
    return max(0, now - int(row[schema.TRANS_CREATED_AT]))


def _extract_block_number(value: Any) -> int | None:
    """Try the common block-number field spellings used by RPC responses."""
    if not isinstance(value, dict):
        return None

    for key in (
        "blockNumber",
        "block_number",
        "blockHeight",
        "block_height",
        "block",
    ):
        candidate = value.get(key)
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue

    return None


def _unwrap_rpc_result(result: Any) -> tuple[Any, Any | None]:
    """Return (data, metadata) for both old and PoS-shaped RPC responses."""
    if isinstance(result, dict) and "data" in result and "metadata" in result:
        return result.get("data"), result.get("metadata")
    return result, None


def _execution_result_is_failure(value: Any) -> bool:
    """Return True when any RPC layer explicitly reports failed execution."""
    for path, item in _walk_json(value):
        if not path:
            continue
        key = path[-1].replace("_", "").lower()
        if key == "executionresult" and item is False:
            return True
    return False


def _is_not_found_error(error: Any) -> bool:
    """Return True when an RPC error probably means the hash is unknown."""
    if not isinstance(error, dict):
        return False

    message = str(error.get("message") or "").lower()
    return any(
        phrase in message
        for phrase in (
            "not found",
            "unknown transaction",
            "transaction not found",
            "could not find",
        )
    )


def _json_rpc_post_sync(
    *,
    rpc_url: str,
    method: str,
    params: list[Any],
    timeout_seconds: int,
) -> Any:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
        body = response.read().decode("utf-8")

    data = json.loads(body)
    if not isinstance(data, dict):
        raise RuntimeError("Nimiq RPC returned a non-object JSON response")

    if data.get("error") is not None:
        error = data["error"]
        if _is_not_found_error(error):
            return None
        raise RuntimeError(f"Nimiq RPC error: {error!r}")

    return data.get("result")


def _extract_rpc_network_id(result: Any) -> int:
    """Extract a numeric network ID from common Nimiq RPC result shapes."""
    data, _metadata = _unwrap_rpc_result(result)
    if isinstance(data, bool):
        raise RuntimeError("Nimiq RPC getNetworkId returned a boolean")
    if isinstance(data, (int, str)):
        try:
            return int(data)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Nimiq RPC getNetworkId returned an invalid value") from exc
    if isinstance(data, dict):
        for key in ("networkId", "network_id", "id"):
            if key in data:
                try:
                    return int(data[key])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Nimiq RPC getNetworkId returned an invalid value") from exc
    raise RuntimeError("Nimiq RPC getNetworkId did not expose a network ID")


async def get_configured_rpc_network_id(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Ask the configured RPC which Nimiq network it actually serves."""
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method="getNetworkId",
        params=[],
        timeout_seconds=int(timeout_seconds),
    )
    return _extract_rpc_network_id(result)


async def verify_configured_rpc_network(
    *,
    expected_network_id: int | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Fail startup when the configured RPC serves a different Nimiq network."""
    expected = int(
        expected_network_id
        if expected_network_id is not None
        else getattr(const, "NIMIQ_NETWORK_ID", 0)
    )
    actual = await get_configured_rpc_network_id(
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
    )
    if actual != expected:
        raise RuntimeError(
            f"Configured Nimiq RPC serves network ID {actual}, expected {expected}"
        )
    return actual


async def get_chain_head_height(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Return the configured RPC's current block height.

    getBlockNumber is the smallest standard request for this purpose. It avoids
    downloading a full block for every refresh and consumes less of a public
    RPC provider's rate-limit budget.
    """
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method="getBlockNumber",
        params=[],
        timeout_seconds=int(timeout_seconds),
    )
    data, _metadata = _unwrap_rpc_result(result)
    if isinstance(data, bool):
        raise RuntimeError("Nimiq RPC getBlockNumber returned a boolean")
    if isinstance(data, (int, str)):
        try:
            height = int(data)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Nimiq RPC returned an invalid block height") from exc
    else:
        height = _extract_block_number(data)
    if height is None or int(height) < 0:
        raise RuntimeError("Nimiq RPC getBlockNumber did not expose a block height")
    return int(height)


def remember_chain_head_height(height: int) -> int:
    """Store one successfully read chain height for deposit preflight checks."""
    global _CHAIN_HEAD_HEIGHT, _CHAIN_HEAD_UPDATED_AT, _CHAIN_HEAD_LAST_ERROR
    height = int(height)
    if height < 0:
        raise ValueError("chain height must be non-negative")
    _CHAIN_HEAD_HEIGHT = height
    _CHAIN_HEAD_UPDATED_AT = time.monotonic()
    _CHAIN_HEAD_LAST_ERROR = None
    return height


def get_cached_chain_head_height(*, max_age_seconds: int | None = None) -> int | None:
    """Return the recent validated height, or None when missing/stale."""
    if _CHAIN_HEAD_HEIGHT is None or _CHAIN_HEAD_UPDATED_AT is None:
        return None
    max_age = int(
        max_age_seconds
        if max_age_seconds is not None
        else getattr(const, "NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS", 5 * 60)
    )
    if max_age < 0:
        return None
    if time.monotonic() - _CHAIN_HEAD_UPDATED_AT > max_age:
        return None
    return int(_CHAIN_HEAD_HEIGHT)


async def refresh_chain_head_height(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Refresh the shared height cache from the configured RPC."""
    global _CHAIN_HEAD_LAST_ERROR
    try:
        height = await get_chain_head_height(
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
    except Exception as exc:
        _CHAIN_HEAD_LAST_ERROR = wallet.redact_secret_values(exc)
        raise
    return remember_chain_head_height(height)


async def get_chain_head_height_for_deposit(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    max_age_seconds: int | None = None,
) -> int:
    """Return a recent validated height without an RPC request on every click."""
    cached = get_cached_chain_head_height(max_age_seconds=max_age_seconds)
    if cached is not None:
        return cached
    return await refresh_chain_head_height(
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )


def chain_head_cache_status() -> RowDict:
    """Return non-sensitive diagnostics for logs and future status pages."""
    age_seconds = None
    if _CHAIN_HEAD_UPDATED_AT is not None:
        age_seconds = max(0.0, time.monotonic() - _CHAIN_HEAD_UPDATED_AT)
    return {
        "height": _CHAIN_HEAD_HEIGHT,
        "age_seconds": age_seconds,
        "last_error": _CHAIN_HEAD_LAST_ERROR,
    }


def _normalise_chain_hash(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_chain_hash(raw: Any) -> str | None:
    for path, item in _walk_json(raw):
        if not path or isinstance(item, (dict, list)):
            continue
        key = path[-1].lower()
        if key in {"hash", "tx_hash", "txhash", "transactionhash", "transaction_hash"}:
            candidate = str(item or "").strip()
            if candidate:
                return candidate
    return None


def _transaction_matches_hash(raw: Any, tx_hash: str) -> bool:
    extracted = _extract_chain_hash(raw)
    if extracted is None:
        return False
    return _normalise_chain_hash(extracted) == _normalise_chain_hash(tx_hash)


def _iter_candidate_transactions(result: Any):
    """Yield likely transaction objects from Nimiq RPC address-list responses."""
    data, _metadata = _unwrap_rpc_result(result)
    queue = [data]
    index = 0
    while index < len(queue):
        item = queue[index]
        index += 1
        if isinstance(item, list):
            queue.extend(item)
            continue
        if not isinstance(item, dict):
            continue

        if _extract_chain_hash(item) is not None:
            yield item
            continue

        for key in ("transactions", "items", "data", "results", "result"):
            child = item.get(key)
            if isinstance(child, (list, dict)):
                queue.append(child)


def _find_transaction_by_hash(result: Any, tx_hash: str) -> Any | None:
    for tx in _iter_candidate_transactions(result):
        if _transaction_matches_hash(tx, tx_hash):
            return tx
    return None


def _verification_failed_because_unstructured(reason: str | None) -> bool:
    text = str(reason or "").lower()
    return any(
        phrase in text
        for phrase in (
            "did not expose",
            "stored transaction sender/from address is invalid",
            "stored transaction recipient/to address is invalid",
        )
    )


def _verification_address_for_record(trans: RowDict) -> str | None:
    """Pick the best address for getTransactionsByAddress verification."""
    trans_type = int(trans.get(schema.TRANS_TYPE) or -1)
    if trans_type == const.TRANS_TYPE_FILL_SPOT:
        return _normalise_address_for_compare(trans.get(schema.TRANS_TO_ADDRESS))
    return (
        _normalise_address_for_compare(trans.get(schema.TRANS_FROM_ADDRESS))
        or _normalise_address_for_compare(trans.get(schema.TRANS_TO_ADDRESS))
    )


async def get_chain_transactions_by_address(
    address: str,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    max_transactions: int = 500,
    start_at: str | None = None,
) -> Any:
    """Return recent transactions for one address via Nimiq RPC.

    ``startAt`` is a transaction-hash cursor.  The currently deployed
    TestAlbatross RPC requires the third positional parameter even when no
    cursor is used, but rejects an empty string.  JSON ``null`` represents the
    unused cursor without pretending that an empty value is a transaction hash.
    """
    params: list[Any] = [address, int(max_transactions), None]
    clean_start_at = str(start_at or "").strip()
    if clean_start_at:
        if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_start_at):
            raise ValueError("start_at must be a 64-character hexadecimal Nimiq transaction hash")
        params[2] = clean_start_at

    return await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=rpc_url,
        method="getTransactionsByAddress",
        params=params,
        timeout_seconds=int(timeout_seconds),
    )


def _normalise_chain_account_type(value: Any) -> str | None:
    """Return a stable name for account-type values exposed by Nimiq RPC."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return {0: "basic", 1: "vesting", 2: "htlc", 3: "staking"}.get(value)

    text = re.sub(r"[^a-z0-9]", "", str(value).strip().lower())
    aliases = {
        "0": "basic",
        "basic": "basic",
        "basicaccount": "basic",
        "1": "vesting",
        "vesting": "vesting",
        "vestingcontract": "vesting",
        "2": "htlc",
        "htlc": "htlc",
        "hashedtimelockedcontract": "htlc",
        "3": "staking",
        "staking": "staking",
        "stakingcontract": "staking",
    }
    return aliases.get(text)


def _first_chain_scalar_for_keys(value: Any, keys: set[str]) -> Any | None:
    keys_lc = {key.lower() for key in keys}
    for path, item in _walk_json(value):
        if not path or isinstance(item, (dict, list)):
            continue
        if path[-1].lower() in keys_lc:
            return item
    return None


def _normalise_chain_timestamp_milliseconds(value: Any) -> int | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    # Nimiq RPC normally returns milliseconds.  Accept seconds defensively.
    return timestamp * 1000 if timestamp < 10_000_000_000 else timestamp


async def get_chain_account_by_address(
    address: str,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> RowDict:
    """Return one account object from the configured Nimiq RPC."""
    clean_address = _validate_nimiq_address(address, field_name="account address")
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method="getAccountByAddress",
        params=[clean_address],
        timeout_seconds=int(timeout_seconds),
    )
    data, _metadata = _unwrap_rpc_result(result)
    if not isinstance(data, dict):
        raise RuntimeError("Nimiq RPC getAccountByAddress returned no account object")
    return dict(data)


async def resolve_nimiq_pay_payout_address(
    address: str,
    *,
    source_tx_hash: str | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    force_chain_resolution: bool | None = None,
) -> str:
    """Resolve a Nimiq Pay account to a safe basic-account payout address.

    Nimiq Pay may expose an HTLC contract through ``listAccounts()``.  Existing
    HTLCs reject ordinary incoming transfers, so sending a refund or reward back
    to the contract address creates an on-chain failed transaction.  For a
    refund, the original deposit transaction tells us which HTLC party
    authorised the payment: the recipient before/equal to timeout, otherwise
    the sender.  For a reward with no source transaction, the contract recipient
    is the beneficiary selected by Nimiq Pay.

    Unsupported contract types and incomplete RPC data fail closed before a
    durable outgoing intent is created or any transaction is broadcast.
    """
    raw_address = str(address or "").strip()
    if not raw_address:
        raise ValueError("payout address must be non-empty")

    should_resolve = (
        bool(getattr(const, "PUBLIC_DEPLOYMENT", False))
        if force_chain_resolution is None
        else bool(force_chain_resolution)
    )
    if not should_resolve:
        # Local development intentionally uses placeholder addresses and must not
        # contact a public chain merely to exercise fake wallet sends.
        return raw_address

    clean_address = _validate_nimiq_address(raw_address, field_name="payout address")

    source_status: ChainTransactionStatus | None = None
    source_type: str | None = None
    if source_tx_hash:
        clean_hash = str(source_tx_hash).strip()
        if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash):
            raise RuntimeError("payout source transaction hash is invalid")
        source_status = await get_chain_transaction_status(
            clean_hash,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
        if source_status.status != "confirmed":
            raise RuntimeError(
                "payout source transaction could not be re-verified as confirmed"
            )
        source_type = _normalise_chain_account_type(
            _first_chain_scalar_for_keys(
                source_status.raw,
                {"fromType", "senderType", "from_type", "sender_type"},
            )
        )
        if source_type == "basic":
            return clean_address
        if source_type not in {None, "htlc"}:
            raise RuntimeError(
                f"payout source account type {source_type!r} is not supported"
            )

    account = await get_chain_account_by_address(
        clean_address,
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )
    account_type = _normalise_chain_account_type(account.get("type"))
    if account_type == "basic":
        if source_type == "htlc":
            # The contract has been pruned since the payment and its HTLC party
            # metadata is no longer available.  Never pay the now-empty contract
            # address as though it were a newly-created basic account.
            raise RuntimeError("the source HTLC was pruned before its beneficiary could be resolved")
        return clean_address
    if account_type != "htlc":
        raise RuntimeError(
            f"payout address account type {account_type or account.get('type')!r} is not supported"
        )

    target_field = "recipient"
    if source_status is not None:
        transaction_time = _normalise_chain_timestamp_milliseconds(
            _first_chain_scalar_for_keys(source_status.raw, {"timestamp", "time"})
        )
        timeout = _normalise_chain_timestamp_milliseconds(account.get("timeout"))
        if transaction_time is None or timeout is None:
            raise RuntimeError("HTLC payout party could not be resolved from transaction time and timeout")
        # Regular HTLC transfers are authorised by the recipient through the
        # timeout; after it, only the original sender can resolve the contract.
        target_field = "recipient" if transaction_time <= timeout else "sender"

    target = _validate_nimiq_address(
        str(account.get(target_field) or ""),
        field_name=f"HTLC {target_field} payout address",
    )
    target_account = await get_chain_account_by_address(
        target,
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )
    target_type = _normalise_chain_account_type(target_account.get("type"))
    if target_type != "basic":
        raise RuntimeError(
            f"resolved HTLC {target_field} is not a basic account"
        )
    return target


async def verify_chain_details_for_record(
    trans: RowDict,
    status: ChainTransactionStatus,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> VerifiedChainDetails:
    """Verify a confirmed tx hash against the DB row, with an address-list fallback.

    getTransactionByHash is the cheap first check. Some RPC responses are not
    shaped consistently enough for a generic parser, so when the direct response
    is unstructured we ask getTransactionsByAddress for the expected address and
    match the exact hash there before accepting the transaction as real.
    """
    direct = _verify_chain_details_for_record(trans, status)
    if direct.ok:
        return direct

    tx_hash = str(status.tx_hash or trans.get(schema.TRANS_TX_HASH) or "").strip()
    address = _verification_address_for_record(trans)
    if not tx_hash or not address:
        return direct

    # The cheap hash lookup response can be unstructured or shaped differently
    # from address-list transactions. Fall back to the expected address history
    # and accept only an exact hash match that also passes the same from/to/amount
    # checks. A wrong transaction will not appear under the expected address.
    try:
        address_result = await get_chain_transactions_by_address(
            address,
            rpc_url=rpc_url,
            timeout_seconds=timeout_seconds,
            max_transactions=int(getattr(const, "NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)),
        )
    except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
        return VerifiedChainDetails(
            ok=False,
            reason=f"transaction hash was found, but address-list proof failed: {exc!r}",
        )

    matched = _find_transaction_by_hash(address_result, tx_hash)
    if matched is None:
        return VerifiedChainDetails(
            ok=False,
            reason="transaction hash was not found in expected address transaction history",
        )

    fallback_status = ChainTransactionStatus(
        status="confirmed",
        tx_hash=tx_hash,
        block_number=status.block_number,
        raw=matched,
        reason="verified via getTransactionsByAddress",
    )
    fallback = _verify_chain_details_for_record(trans, fallback_status)
    if fallback.ok:
        return fallback
    return VerifiedChainDetails(
        ok=False,
        reason=fallback.reason or direct.reason or "confirmed transaction did not match stored transaction",
    )


async def get_chain_transaction_status(
    tx_hash: str,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> ChainTransactionStatus:
    """Ask Nimiq RPC for one transaction hash.

    A returned transaction with a block number is treated as confirmed. A null
    or not-found result is treated as still pending here; the age check happens
    in check_pending_transaction().
    """
    tx_hash = str(tx_hash).strip()
    if not tx_hash:
        return ChainTransactionStatus(status="failed", tx_hash=tx_hash, reason="empty tx_hash")

    try:
        result = await asyncio.to_thread(
            _json_rpc_post_sync,
            rpc_url=rpc_url,
            method="getTransactionByHash",
            params=[tx_hash],
            timeout_seconds=int(timeout_seconds),
        )
    except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as e:
        return ChainTransactionStatus(status="unknown", tx_hash=tx_hash, reason=repr(e))

    if result is None:
        return ChainTransactionStatus(status="pending", tx_hash=tx_hash, raw=result, reason="hash not found yet")

    data, metadata = _unwrap_rpc_result(result)
    block_number = _extract_block_number(data) or _extract_block_number(metadata)

    if _execution_result_is_failure(data):
        return ChainTransactionStatus(
            status="failed",
            tx_hash=tx_hash,
            block_number=block_number,
            raw=result,
            reason="executionResult was false",
        )

    # getTransactionByHash does not discover mempool transactions on Nimiq PoS;
    # a non-null result means the transaction was found on-chain even if the
    # response does not expose the exact block number.
    return ChainTransactionStatus(
        status="confirmed",
        tx_hash=tx_hash,
        block_number=block_number,
        raw=result,
    )


async def _recover_server_transaction_from_address_history(
    trans: RowDict,
    chain_status: ChainTransactionStatus,
    *,
    rpc_url: str,
    timeout_seconds: int,
) -> ChainTransactionStatus | None:
    """Recover an existing outgoing hash when direct RPC lookup misses it.

    Nimiq public RPCs can return no result for ``getTransactionByHash`` even
    though the same confirmed transaction is present in the sender address's
    history.  Server-initiated rows must never be resent merely because of that
    indexing gap.  This fallback accepts only the exact hash already stored in
    SQLite; the normal strict sender/recipient/amount verifier still runs before
    the row is marked confirmed.
    """
    if not _is_server_initiated_transaction(trans):
        return None

    tx_hash = str(trans.get(schema.TRANS_TX_HASH) or chain_status.tx_hash or "").strip()
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        return None

    address = _verification_address_for_record(trans)
    if address is None:
        return None

    try:
        history = await get_chain_transactions_by_address(
            address,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
            max_transactions=int(getattr(const, "NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)),
        )
    except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, json.JSONDecodeError):
        # Address history is an additional proof source, not permission to turn
        # an ambiguous outgoing payment into a failure or a retry.
        return None

    matched = _find_transaction_by_hash(history, tx_hash)
    if matched is None:
        return None

    block_number = _extract_block_number(matched)
    if _execution_result_is_failure(matched):
        return ChainTransactionStatus(
            status="failed",
            tx_hash=tx_hash,
            block_number=block_number,
            raw=matched,
            reason="exact hash was found in expected-address history with a failed execution result",
        )

    return ChainTransactionStatus(
        status="confirmed",
        tx_hash=tx_hash,
        block_number=block_number,
        raw=matched,
        reason="exact hash recovered through expected-address transaction history",
    )


async def check_pending_transaction(
    trans: RowDict,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
    user_deposit_stale_after_seconds: int = DEFAULT_USER_DEPOSIT_STALE_AFTER_SECONDS,
) -> ChainTransactionStatus:
    """Check one cached pending transaction and return its current outcome."""
    tx_hash = str(trans.get(schema.TRANS_TX_HASH) or "").strip()

    if _is_local_intent_hash(tx_hash):
        return ChainTransactionStatus(
            status="pending",
            tx_hash=tx_hash,
            reason="local outbox intent has no chain hash yet; leaving pending to avoid duplicate broadcast",
        )

    chain_status = await get_chain_transaction_status(
        tx_hash,
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
    )

    if chain_status.status in {"pending", "unknown"}:
        recovered = await _recover_server_transaction_from_address_history(
            trans,
            chain_status,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
        if recovered is not None:
            return recovered

    if chain_status.status != "pending":
        return chain_status

    age_seconds = _transaction_age_seconds(trans)
    if (
        int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and age_seconds >= int(user_deposit_stale_after_seconds)
    ):
        address = _verification_address_for_record(trans)
        if address is None:
            return ChainTransactionStatus(
                status="unknown",
                tx_hash=tx_hash,
                reason="stale deposit cannot be checked because its recipient address is invalid",
            )
        try:
            history = await get_chain_transactions_by_address(
                address,
                rpc_url=rpc_url,
                timeout_seconds=int(timeout_seconds),
                max_transactions=int(getattr(const, "NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)),
            )
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
            return ChainTransactionStatus(
                status="unknown",
                tx_hash=tx_hash,
                reason=f"stale deposit address-history check failed: {exc!r}",
            )
        matched = _find_transaction_by_hash(history, tx_hash)
        if matched is not None:
            if _execution_result_is_failure(matched):
                return ChainTransactionStatus(
                    status="failed",
                    tx_hash=tx_hash,
                    block_number=_extract_block_number(matched),
                    raw=matched,
                    reason="deposit hash was found in address history with a failed execution result",
                )
            return ChainTransactionStatus(
                status="confirmed",
                tx_hash=tx_hash,
                block_number=_extract_block_number(matched),
                raw=matched,
                reason="found through deposit-address history",
            )
        return ChainTransactionStatus(
            status="failed",
            tx_hash=tx_hash,
            reason=(
                f"deposit hash was not found by hash or recipient history after {age_seconds} seconds; "
                "the Nimiq transaction validity window has elapsed"
            ),
        )

    if age_seconds >= int(fail_after_seconds):
        # A missing RPC result is not proof that a broadcast transaction failed.
        # Marking the database row FAILED would release the uniqueness guard and
        # could permit a second payment/deposit while the first later confirms.
        # Keep the row pending and surface it for manual reconciliation instead.
        return ChainTransactionStatus(
            status="unknown",
            tx_hash=tx_hash,
            raw=chain_status.raw,
            reason=(
                f"hash is still not visible after {int(fail_after_seconds)} seconds; "
                "transaction remains pending until failure is proven or an administrator reconciles it"
            ),
        )

    return chain_status


async def _claim_transaction_is_completed_prizedraw_payout(db, trans: RowDict) -> bool:
    """Return True when a TRANSACTION row is a completed-Prizedraw winner payout."""
    if int(trans.get(schema.TRANS_TYPE) or -1) != const.TRANS_TYPE_CLAIM:
        return False
    claim_id = trans.get(schema.TRANS_CLAIM_ID)
    if claim_id is None:
        return False

    claim = await db_access.get_claim(db, claim_id=int(claim_id))
    if claim is None:
        return False

    spot = await db_access.get_spot(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
    if spot is None:
        return False
    if int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_COMPLETED:
        return False

    return await db_access.is_prizedraw(db, spot_id=int(spot[schema.SPOT_ID]))


async def _finalize_cancelled_spot_if_ready(db, *, spot_id: int | None) -> bool:
    """Mark a cancellable Spot cancelled only after refund/fee sends are final."""
    if spot_id is None:
        return False

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        return False
    spot_status_value = spot.get(schema.SPOT_STATUS)
    if spot_status_value is None:
        return False
    spot_status = int(spot_status_value)
    if spot_status not in {const.SPOT_STATUS_DRAFT, const.SPOT_STATUS_PUBLISHED}:
        return False
    if spot_status == const.SPOT_STATUS_PUBLISHED and await db_access.is_prizedraw(
        db,
        spot_id=int(spot_id),
    ):
        return False

    transactions = await db_access.get_transactions_by_spot(db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT)
    outgoing_types = {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
    has_cancellation_intent = any(int(row.get(schema.TRANS_TYPE) or -1) in outgoing_types for row in transactions)
    if not has_cancellation_intent:
        return False
    if any(
        int(row.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1) == const.TRANS_STATUS_PENDING
        for row in transactions
    ):
        return False

    confirmed_deposit_total = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    confirmed_claim_total = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CLAIM
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    confirmed_creation_fee_total = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CREATION_FEE
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    remaining_cancellable_total = max(
        0,
        confirmed_deposit_total - confirmed_claim_total - confirmed_creation_fee_total,
    )
    confirmed_cancellation_total = sum(
        int(row.get(schema.TRANS_AMOUNT) or 0)
        for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )
    if confirmed_cancellation_total < remaining_cancellable_total:
        return False

    await db_access.set_spot_status_to_cancelled(db, spot_id=int(spot_id))
    return True


async def mark_trans_as_confirmed(
    db,
    trans: RowDict,
    *,
    block_number: int | None = None,
    verified_details: VerifiedChainDetails | None = None,
) -> RowDict:
    """Mark a TRANSACTION confirmed without permitting mixed-wallet deposits.

    A BEGIN IMMEDIATE lock serialises confirmation of competing deposits. The
    first confirmed on-chain sender becomes the Spot's funding wallet; a later
    deposit from another sender is retained for audit but marked FAILED so it
    cannot fund claims or be included in the creator's cancellation refund.
    """
    trans_id = _transaction_id(trans)
    completed_prizedraw_payout = await _claim_transaction_is_completed_prizedraw_payout(db, trans)
    claim_id = trans.get(schema.TRANS_CLAIM_ID)
    trans_type = int(trans.get(schema.TRANS_TYPE) or -1)
    spot_id = trans.get(schema.TRANS_SPOT_ID)
    funding_mismatch_reason: str | None = None
    cancelled_finalized = False

    try:
        await db.execute("BEGIN IMMEDIATE;")

        if verified_details is not None and verified_details.ok:
            if trans_type == const.TRANS_TYPE_FILL_SPOT and spot_id is not None:
                funding_address = await db_access.get_confirmed_spot_funding_address(
                    db,
                    spot_id=int(spot_id),
                )
                if funding_address is not None:
                    established_sender = _normalise_address_for_compare(funding_address)
                    confirmed_sender = _normalise_address_for_compare(verified_details.from_address)
                    if established_sender is None or confirmed_sender != established_sender:
                        funding_mismatch_reason = (
                            "confirmed deposit used a different wallet than the Spot's original funding wallet"
                        )

            # Keep the actual chain facts even when the funding-policy check
            # rejects this deposit. That leaves a complete audit trail for any
            # manual recovery of funds sent from the wrong wallet.
            await db_access.update_transaction_chain_details(
                db,
                trans_id=trans_id,
                from_address=verified_details.from_address,
                to_address=verified_details.to_address,
                amount=verified_details.amount,
                block_number=block_number,
            )

        if funding_mismatch_reason is not None:
            await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
        else:
            if block_number is None:
                await db_access.modify_transaction_status(
                    db,
                    trans_id=trans_id,
                    status=const.TRANS_STATUS_CONFIRMED,
                )
            else:
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=trans_id,
                    block_number=int(block_number),
                )

            # In the revised Prizedraw model, selected winners stay PENDING until
            # their payout transaction confirms. Losers are already SUCCESS.
            if completed_prizedraw_payout and claim_id is not None:
                await db_access.set_claim_status_to_success(db, claim_id=int(claim_id))
            cancelled_finalized = await _finalize_cancelled_spot_if_ready(
                db, spot_id=spot_id
            )

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=spot_id,
        user_id=trans.get(schema.TRANS_USER_ID),
    )
    if completed_prizedraw_payout and claim_id is not None and funding_mismatch_reason is None:
        await cache.notify_claim_changed(
            db,
            spot_id=spot_id,
            user_id=trans.get(schema.TRANS_USER_ID),
        )
    if cancelled_finalized or trans_type == const.TRANS_TYPE_CREATION_FEE:
        await cache.notify_spot_changed(db, spot_id=spot_id)

    if funding_mismatch_reason is not None:
        return {
            "trans_id": trans_id,
            "status": "failed",
            "reason": funding_mismatch_reason,
        }
    return {"trans_id": trans_id, "status": "confirmed", "block_number": block_number}


async def mark_trans_as_failed(db, trans: RowDict, *, reason: str | None = None) -> RowDict:
    """Mark a TRANSACTION failed in the DB, then remove it from pending cache."""
    trans_id = _transaction_id(trans)
    completed_prizedraw_payout = await _claim_transaction_is_completed_prizedraw_payout(db, trans)

    async with db_access.transaction(db):
        await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
        # Failed Prizedraw payout attempts deliberately leave the selected
        # winner CLAIM as PENDING. settlement_updater.py will retry it.
        cancelled_finalized = await _finalize_cancelled_spot_if_ready(
            db, spot_id=trans.get(schema.TRANS_SPOT_ID)
        )

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=trans.get(schema.TRANS_SPOT_ID),
        user_id=trans.get(schema.TRANS_USER_ID),
    )
    if completed_prizedraw_payout:
        await cache.notify_claim_changed(
            db,
            spot_id=trans.get(schema.TRANS_SPOT_ID),
            user_id=trans.get(schema.TRANS_USER_ID),
        )
    if cancelled_finalized or int(trans.get(schema.TRANS_TYPE) or -1) in {
        const.TRANS_TYPE_CANCEL_SPOT,
        const.TRANS_TYPE_PLAT_FEE,
        const.TRANS_TYPE_CREATION_FEE,
        const.TRANS_TYPE_REMAINDER_REFUND,
    }:
        await cache.notify_spot_changed(db, spot_id=trans.get(schema.TRANS_SPOT_ID))

    return {"trans_id": trans_id, "status": "failed", "reason": reason}


async def check_pending_transactions(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
    max_checks: int = DEFAULT_MAX_CHECKS_PER_RUN,
) -> RowDict:
    """Check cached pending transactions and finalise any resolved rows.

    This is the function to call from a future scheduler/background task. It is
    intentionally safe on RPC outages: request errors are reported as "unknown"
    and do not cause transactions to be failed.
    """
    checked: list[RowDict] = []
    finalised: list[RowDict] = []
    still_pending: list[RowDict] = []
    unknown: list[RowDict] = []
    chain_head_height: int | None = None
    chain_head_error: str | None = None

    try:
        chain_head_height = await refresh_chain_head_height(
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
    except Exception as exc:
        chain_head_error = wallet.redact_secret_values(exc)
        logger.warning("Nimiq chain-head refresh failed: %s", chain_head_error)

    async with get_db() as db:
        pending = await cache.get_pending_transactions(db, limit=int(max_checks))

        for trans in pending:
            status = await check_pending_transaction(
                trans,
                rpc_url=rpc_url,
                timeout_seconds=int(timeout_seconds),
                fail_after_seconds=int(fail_after_seconds),
            )
            checked.append(
                {
                    "trans_id": _transaction_id(trans),
                    "tx_hash": status.tx_hash,
                    "status": status.status,
                    "reason": status.reason,
                    "block_number": status.block_number,
                }
            )

            if status.status == "confirmed":
                verified = await verify_chain_details_for_record(
                    trans,
                    status,
                    rpc_url=rpc_url,
                    timeout_seconds=int(timeout_seconds),
                )
                if not verified.ok:
                    reason = verified.reason or "confirmed transaction details could not be verified"
                    uncertain_proof = (
                        _verification_failed_because_unstructured(reason)
                        or "proof failed" in reason.lower()
                        or "not found in expected address transaction history" in reason.lower()
                    )
                    if _is_server_initiated_transaction(trans) or uncertain_proof:
                        # The hash was found on-chain. Never release an outgoing
                        # payment's uniqueness guard merely because an RPC shape
                        # or helper detail is unexpected; that could double-pay.
                        checked[-1]["status"] = "unknown"
                        checked[-1]["reason"] = reason
                        unknown.append({"trans_id": _transaction_id(trans), "reason": reason})
                    else:
                        # A definitively wrong user deposit does not fund the
                        # Spot and may safely be rejected.
                        finalised.append(await mark_trans_as_failed(db, trans, reason=reason))
                    continue

                finalised.append(
                    await mark_trans_as_confirmed(
                        db,
                        trans,
                        block_number=status.block_number,
                        verified_details=verified,
                    )
                )
            elif status.status == "failed":
                finalised.append(await mark_trans_as_failed(db, trans, reason=status.reason))
            elif status.status == "unknown":
                unknown.append({"trans_id": _transaction_id(trans), "reason": status.reason})
            else:
                still_pending.append({"trans_id": _transaction_id(trans), "reason": status.reason})

        creation_fees = await submit_ready_spot_creation_fees(db)

    return {
        "ok": bool(creation_fees.get("ok", True)),
        "checked_count": len(checked),
        "finalised_count": len(finalised),
        "still_pending_count": len(still_pending),
        "unknown_count": len(unknown),
        "checked": checked,
        "finalised": finalised,
        "still_pending": still_pending,
        "unknown": unknown,
        "creation_fees": creation_fees,
        "chain_head_height": chain_head_height,
        "chain_head_error": chain_head_error,
    }


async def _transaction_check_loop(interval_seconds: int) -> None:
    """Background loop that keeps pending TRANSACTION rows moving."""
    global _TRANS_CHECK_LAST_RESULT, _TRANS_CHECK_LAST_ERROR

    stop_event = _TRANS_CHECK_STOP_EVENT
    if stop_event is None:  # Defensive: the loop is normally created only by start_transaction_refresher().
        return
    while not stop_event.is_set():
        try:
            _TRANS_CHECK_LAST_RESULT = await check_pending_transactions()
            if bool(_TRANS_CHECK_LAST_RESULT.get("ok", True)):
                _TRANS_CHECK_LAST_ERROR = None
            else:
                _TRANS_CHECK_LAST_ERROR = repr(_TRANS_CHECK_LAST_RESULT)
                logger.error(
                    "Background transaction check reported failure: %s",
                    _TRANS_CHECK_LAST_RESULT,
                )
        except Exception as exc:  # pragma: no cover - defensive loop guard
            _TRANS_CHECK_LAST_ERROR = repr(exc)
            logger.exception("Background transaction check failed")

        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=max(1, int(interval_seconds)),
            )


async def start_transaction_refresher(
    *,
    run_immediately: bool = False,
    interval_seconds: int = DEFAULT_TRANSACTION_CHECK_INTERVAL_SECONDS,
    fail_on_initial_error: bool = False,
) -> None:
    """Start the background transaction-status loop once.

    Development may continue after a failed initial check so local UI work is
    still possible without chain access. Production sets
    ``fail_on_initial_error`` so the app cannot quietly serve while payment
    reconciliation is unavailable.
    """
    global _TRANS_CHECK_TASK, _TRANS_CHECK_STOP_EVENT, _TRANS_CHECK_LAST_RESULT, _TRANS_CHECK_LAST_ERROR

    if _TRANS_CHECK_TASK is not None and not _TRANS_CHECK_TASK.done():
        return

    _TRANS_CHECK_STOP_EVENT = asyncio.Event()

    if run_immediately:
        try:
            _TRANS_CHECK_LAST_RESULT = await check_pending_transactions()
            if not bool(_TRANS_CHECK_LAST_RESULT.get("ok", True)):
                _TRANS_CHECK_LAST_ERROR = repr(_TRANS_CHECK_LAST_RESULT)
                if fail_on_initial_error:
                    raise RuntimeError("Initial transaction check reported failure")
                logger.error("Initial transaction check reported failure: %s", _TRANS_CHECK_LAST_RESULT)
            else:
                _TRANS_CHECK_LAST_ERROR = None
        except Exception as exc:
            _TRANS_CHECK_LAST_ERROR = repr(exc)
            logger.exception("Initial background transaction check failed")
            if fail_on_initial_error:
                raise

    _TRANS_CHECK_TASK = asyncio.create_task(_transaction_check_loop(int(interval_seconds)))


async def stop_transaction_refresher() -> None:
    """Stop the background transaction-status loop if it is running."""
    global _TRANS_CHECK_TASK, _TRANS_CHECK_STOP_EVENT

    if _TRANS_CHECK_STOP_EVENT is not None:
        _TRANS_CHECK_STOP_EVENT.set()

    if _TRANS_CHECK_TASK is not None:
        _TRANS_CHECK_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await _TRANS_CHECK_TASK

    _TRANS_CHECK_TASK = None
    _TRANS_CHECK_STOP_EVENT = None


def transaction_refresher_status() -> RowDict:
    """Return a small debug snapshot for future admin/status pages."""
    return {
        "running": _TRANS_CHECK_TASK is not None and not _TRANS_CHECK_TASK.done(),
        "last_result": _TRANS_CHECK_LAST_RESULT,
        "last_error": _TRANS_CHECK_LAST_ERROR,
    }


async def _transaction_by_hash(db, *, tx_hash: str) -> RowDict | None:
    cur = await db.execute(
        f"SELECT * FROM {schema.TRANS_TABLE_NAME} WHERE LOWER({schema.TRANS_TX_HASH}) = ? LIMIT 1;",
        (str(tx_hash).strip().lower(),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


def _same_recorded_deposit(existing: RowDict, *, user_id: int, spot_id: int) -> bool:
    return (
        int(existing.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(existing.get(schema.TRANS_USER_ID) or -1) == int(user_id)
        and int(existing.get(schema.TRANS_SPOT_ID) or -1) == int(spot_id)
    )


async def record_spot_deposit_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    tx_hash: str,
    to_address: str | None = None,
) -> RowDict:
    """Record one strictly shaped, idempotent Nimiq Pay deposit response."""
    clean_hash = str(tx_hash or "").strip().lower()
    hash_is_valid = bool(_NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash))
    if hash_is_valid:
        existing = await _transaction_by_hash(db, tx_hash=clean_hash)
        if existing is not None:
            if not _same_recorded_deposit(existing, user_id=user_id, spot_id=spot_id):
                raise ValueError("this transaction hash is already attached to a different record")
            return {
                "ok": True,
                "already_recorded": True,
                "trans_id": int(existing[schema.TRANS_ID]),
                "spot_id": int(spot_id),
                "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
            }

    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("only draft spots can receive creator deposits")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("this draft is being cancelled and cannot receive another deposit")

    clean_to_address = wallet.normalise_nimiq_address(
        str(to_address or spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="deposit to_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )
    clean_from_address = wallet.normalise_nimiq_address(
        str(from_address or ""),
        field_name="deposit from_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )

    funding_address = await db_access.get_confirmed_spot_funding_address(
        db,
        spot_id=int(spot_id),
    )
    if funding_address is not None:
        established_sender = _normalise_address_for_compare(funding_address)
        submitted_sender = _normalise_address_for_compare(clean_from_address)
        if established_sender is None or submitted_sender != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    totals = await db_access.get_spot_deposit_totals(db, spot_id=int(spot_id))
    if int(totals.get("pending_amount") or 0) > 0:
        raise ValueError("this draft already has a pending deposit")
    required = int(db_access.spot_required_deposit_amount(spot))
    amount_due = max(0, required - int(totals.get("confirmed_amount") or 0))
    if amount_due <= 0:
        raise ValueError("this draft is already fully funded")
    amount = min(amount, amount_due)

    if not hash_is_valid:
        raise ValueError("tx_hash must be a 64-character hexadecimal Nimiq transaction hash")

    try:
        trans_id = await db_access.create_spot_deposit_transaction(
            db,
            user_id=int(user_id),
            spot_id=int(spot_id),
            amount=amount,
            from_address=clean_from_address,
            to_address=clean_to_address,
            tx_hash=clean_hash,
        )
    except sqlite3.IntegrityError:
        existing = await _transaction_by_hash(db, tx_hash=clean_hash)
        if existing is None or not _same_recorded_deposit(existing, user_id=user_id, spot_id=spot_id):
            raise
        return {
            "ok": True,
            "already_recorded": True,
            "trans_id": int(existing[schema.TRANS_ID]),
            "spot_id": int(spot_id),
            "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
        }

    return {
        "ok": True,
        "already_recorded": False,
        "trans_id": int(trans_id),
        "spot_id": int(spot_id),
        "amount": amount,
    }


async def submit_platform_fee_transaction(
    db,
    *,
    spot_id: int,
    amount: int,
    fee_address: str | None = None,
) -> RowDict:
    """Send and record a platform/cancellation fee from a SPOT deposit."""
    amount = int(amount)
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "zero_amount", "trans_id": None}

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    clean_fee_address = str(fee_address or getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")).strip()
    if not clean_fee_address:
        raise ValueError("SPOT_CANCELLATION_FEE_ADDRESS is not configured")

    result = await _submit_recorded_chain_send(
        db,
        spot=spot,
        to_address=clean_fee_address,
        amount=amount,
        memo=build_transaction_description("Refund Fee", spot.get(schema.SPOT_TITLE)),
        intent_kind="platform_fee",
        intent_primary_id=int(spot_id),
        create_transaction=db_access.create_platform_fee_transaction,
        create_transaction_kwargs={
            "user_id": int(spot[schema.SPOT_CREATED_BY]),
            "spot_id": int(spot_id),
        },
    )
    return {**result, "spot_id": int(spot_id)}


async def submit_spot_creation_fee_transaction(
    db,
    *,
    spot_id: int,
) -> RowDict:
    """Send one fully funded Spot's snapshotted creation fee.

    The function is intentionally idempotent. A pending or confirmed fee leg
    causes an immediate no-op; a definitively failed leg may be retried. The
    unique partial index in database.py closes the remaining cross-process race.
    """
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    spot_status_value = spot.get(schema.SPOT_STATUS)
    allowed_statuses = {
        const.SPOT_STATUS_DRAFT,
        const.SPOT_STATUS_PUBLISHED,
        const.SPOT_STATUS_COMPLETED,
    }
    if spot_status_value is None or int(spot_status_value) not in allowed_statuses:
        raise ValueError("creation fees can only be submitted for funded, non-cancelled spots")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "skipped": True,
            "reason": "cancellation_started",
            "trans_id": None,
        }

    amount = db_access.spot_creation_fee_amount(spot)
    if amount <= 0:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "skipped": True,
            "reason": "zero_amount",
            "trans_id": None,
        }

    confirmed_deposit_total = await db_access.get_confirmed_spot_deposit_total(
        db,
        spot_id=int(spot_id),
    )
    required_total = db_access.spot_required_deposit_amount(spot)
    if confirmed_deposit_total < required_total:
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "skipped": True,
            "reason": "not_fully_funded",
            "confirmed_deposit_total": confirmed_deposit_total,
            "required_total": required_total,
            "trans_id": None,
        }

    if await db_access.has_nonfailed_spot_creation_fee_transaction(
        db,
        spot_id=int(spot_id),
    ):
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "already_exists": True,
            "trans_id": None,
        }

    fee_address = str(spot.get(schema.SPOT_CREATION_FEE_ADDRESS) or "").strip()
    if not fee_address:
        raise ValueError("spot creation fee address is missing")

    try:
        result = await _submit_recorded_chain_send(
            db,
            spot=spot,
            to_address=fee_address,
            amount=amount,
            memo=build_transaction_description("Creation Fee", spot.get(schema.SPOT_TITLE)),
            intent_kind="creation_fee",
            intent_primary_id=int(spot_id),
            create_transaction=db_access.create_spot_creation_fee_transaction,
            create_transaction_kwargs={
                "user_id": int(spot[schema.SPOT_CREATED_BY]),
                "spot_id": int(spot_id),
            },
            serialize_intent=True,
        )
    except ValueError:
        # A cancellation may acquire the write lock after the scheduler selected
        # this Spot but before the fee intent is inserted. That is a normal,
        # safe race outcome rather than a reconciliation failure.
        current = await db_access.get_spot(db, spot_id=int(spot_id))
        if current is not None and current.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
            return {
                "ok": True,
                "spot_id": int(spot_id),
                "skipped": True,
                "reason": "cancellation_started",
                "trans_id": None,
            }
        raise
    except (sqlite3.IntegrityError, RuntimeError):
        if await db_access.has_nonfailed_spot_creation_fee_transaction(
            db,
            spot_id=int(spot_id),
        ):
            return {
                "ok": True,
                "spot_id": int(spot_id),
                "already_exists": True,
                "trans_id": None,
            }
        raise

    return {**result, "spot_id": int(spot_id)}


async def submit_ready_spot_creation_fees(
    db,
    *,
    limit: int = 50,
) -> RowDict:
    """Submit missing creation fees for fully funded Spots.

    This recovery pass runs even when no funding transaction was confirmed in
    the current process. It therefore covers restarts after a deposit confirmed
    but before NimHunt managed to create the fee intent.
    """
    spot_ids = await db_access.get_spot_ids_ready_for_creation_fee(
        db,
        limit=int(limit),
    )
    submitted: list[RowDict] = []
    skipped: list[RowDict] = []
    errors: list[RowDict] = []
    for spot_id in spot_ids:
        try:
            result = await submit_spot_creation_fee_transaction(
                db,
                spot_id=int(spot_id),
            )
            if result.get("skipped") or result.get("already_exists"):
                skipped.append(result)
            else:
                submitted.append(result)
        except Exception as exc:
            errors.append(
                {
                    "spot_id": int(spot_id),
                    "error": wallet.redact_secret_values(exc),
                }
            )

    return {
        "ok": not errors,
        "eligible_count": len(spot_ids),
        "submitted_count": len(submitted),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "submitted": submitted,
        "skipped": skipped,
        "errors": errors,
    }


async def submit_spot_refund_transaction(
    db,
    *,
    spot_id: int,
    to_address: str,
    amount: int,
) -> RowDict:
    """Send and record a creator refund from a SPOT deposit."""
    amount = int(amount)
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "zero_amount", "trans_id": None}

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    result = await _submit_recorded_chain_send(
        db,
        spot=spot,
        to_address=to_address,
        amount=amount,
        memo=build_transaction_description("Cancelled Spot", spot.get(schema.SPOT_TITLE)),
        intent_kind="spot_refund",
        intent_primary_id=int(spot_id),
        create_transaction=db_access.create_spot_refund_transaction,
        create_transaction_kwargs={
            "user_id": int(spot[schema.SPOT_CREATED_BY]),
            "spot_id": int(spot_id),
        },
    )
    return {**result, "spot_id": int(spot_id)}


async def submit_spot_remainder_refund_transaction(
    db,
    *,
    spot_id: int,
    to_address: str,
    amount: int,
) -> RowDict:
    """Return confirmed unspent funds after a Spot has become terminal."""
    amount = int(amount)
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "zero_amount", "trans_id": None}

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_COMPLETED:
        raise ValueError("remainder refunds require a completed Spot")

    result = await _submit_recorded_chain_send(
        db,
        spot=spot,
        to_address=to_address,
        amount=amount,
        memo=build_transaction_description("Unused Spot Funds", spot.get(schema.SPOT_TITLE)),
        intent_kind="spot_remainder_refund",
        intent_primary_id=int(spot_id),
        create_transaction=db_access.create_spot_remainder_refund_transaction,
        create_transaction_kwargs={
            "user_id": int(spot[schema.SPOT_CREATED_BY]),
            "spot_id": int(spot_id),
        },
        serialize_intent=True,
    )
    return {**result, "spot_id": int(spot_id)}


async def _published_standard_spot_is_complete(
    db,
    *,
    spot_id: int,
    spot: RowDict | None = None,
) -> bool:
    candidate = spot or {}
    if int(candidate.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_PUBLISHED:
        return False

    # The normal SPOT row already tells us whether capacity is finite. Avoid
    # an unnecessary summary query for unlimited or incomplete test rows.
    max_total = int(candidate.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
    if max_total <= 0:
        return False

    summary = await db_access.get_spot_owner_summary(db, spot_id=int(spot_id))
    if summary is None:
        return False
    if summary.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None:
        return False
    successful = int(summary.get("success_claim_count") or 0)
    return successful >= max_total


async def submit_spot_cancellation_transactions(
    db,
    *,
    spot_id: int,
    cancellation_fee: int | None = None,
    fee_address: str | None = None,
) -> RowDict:
    """Request cancellation and submit its refund as soon as it is safe.

    The cancellation marker is durable and immediately removes the Spot from
    public claiming. Existing deposit/fee/reward transactions are allowed to
    settle first; a background settlement pass retries this function until the
    refund and cancellation fee can be submitted without double-spending.
    """
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    spot_status = int(spot[schema.SPOT_STATUS])
    if spot_status not in {const.SPOT_STATUS_DRAFT, const.SPOT_STATUS_PUBLISHED}:
        raise ValueError("only funded drafts or published spots can be cancelled")
    if spot_status == const.SPOT_STATUS_PUBLISHED and await db_access.is_prizedraw(
        db, spot_id=int(spot_id)
    ):
        raise ValueError("Prizedraw spots cannot be cancelled through this standard cancellation flow")
    if spot_status == const.SPOT_STATUS_PUBLISHED and await _published_standard_spot_is_complete(
        db, spot_id=int(spot_id), spot=spot
    ):
        raise ValueError("completed spots cannot be cancelled")

    async def notify_cancellation_change() -> None:
        try:
            await cache.notify_spot_changed(db, spot_id=int(spot_id))
            await cache.notify_user_changed(
                db,
                user_id=int(spot[schema.SPOT_CREATED_BY]),
            )
        except Exception as exc:
            # Once the cancellation marker or terminal status is committed,
            # a cache refresh failure must not turn an accepted cancellation
            # into a misleading HTTP error. The periodic cache refresh remains
            # able to repair the stale entry.
            logger.warning(
                "Cancellation cache notification failed: spot_id=%s error=%s",
                int(spot_id),
                wallet.redact_secret_values(exc),
            )

    async def deferred_result(*, reason: str, message: str) -> RowDict:
        await db.commit()
        await notify_cancellation_change()
        return {
            "ok": True,
            "spot_id": int(spot_id),
            "cancelled": False,
            "cancellation_pending": True,
            "deferred": True,
            "reason": reason,
            "message": message,
        }

    try:
        await db.execute("BEGIN IMMEDIATE;")
        spot = await db_access.get_spot(db, spot_id=int(spot_id))
        if spot is None:
            raise ValueError(f"spot id={spot_id} does not exist")
        spot_status = int(spot[schema.SPOT_STATUS])
        if spot_status not in {const.SPOT_STATUS_DRAFT, const.SPOT_STATUS_PUBLISHED}:
            raise ValueError("only funded drafts or published spots can be cancelled")
        if spot_status == const.SPOT_STATUS_PUBLISHED and await db_access.is_prizedraw(
            db, spot_id=int(spot_id)
        ):
            raise ValueError("Prizedraw spots cannot be cancelled through this standard cancellation flow")
        if spot_status == const.SPOT_STATUS_PUBLISHED and await _published_standard_spot_is_complete(
            db, spot_id=int(spot_id), spot=spot
        ):
            raise ValueError("completed spots cannot be cancelled")

        # Once this marker commits, claim insertion is rejected and the Spot is
        # removed from public results even when its refund must wait.
        await db_access.mark_spot_cancellation_started(db, spot_id=int(spot_id))

        if spot_status == const.SPOT_STATUS_PUBLISHED:
            unpaid_claim_ids = await db_access.get_unpaid_successful_standard_claim_ids(
                db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT
            )
            if unpaid_claim_ids:
                return await deferred_result(
                    reason="claim_payouts_pending",
                    message="Cancellation is queued while existing successful claims are paid.",
                )

        transactions = await db_access.get_transactions_by_spot(
            db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT
        )
        deposit_transactions = [
            trans for trans in transactions
            if int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        ]
        confirmed_deposits = [
            trans for trans in deposit_transactions
            if int(trans.get(schema.TRANS_STATUS) if trans.get(schema.TRANS_STATUS) is not None else -1)
            == const.TRANS_STATUS_CONFIRMED
        ]
        failed_deposits = [
            trans for trans in deposit_transactions
            if int(trans.get(schema.TRANS_STATUS) if trans.get(schema.TRANS_STATUS) is not None else -1)
            == const.TRANS_STATUS_FAILED
        ]
        confirmed_deposits.sort(key=lambda row: int(row.get(schema.TRANS_CREATED_AT) or 0))
        confirmed_deposit_total = sum(int(row.get(schema.TRANS_AMOUNT) or 0) for row in confirmed_deposits)
        failed_deposit_total = sum(int(row.get(schema.TRANS_AMOUNT) or 0) for row in failed_deposits)

        if spot_status == const.SPOT_STATUS_DRAFT:
            if not deposit_transactions:
                raise ValueError("unfunded drafts should be deleted rather than cancelled")
            if any(
                int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
                == const.TRANS_STATUS_PENDING
                for row in deposit_transactions
            ):
                return await deferred_result(
                    reason="deposit_pending",
                    message="Cancellation is queued until the deposit transaction is resolved.",
                )

        outgoing_types = {
            const.TRANS_TYPE_CLAIM,
            const.TRANS_TYPE_CANCEL_SPOT,
            const.TRANS_TYPE_PLAT_FEE,
            const.TRANS_TYPE_CREATION_FEE,
        }
        pending_outgoing = [
            row for row in transactions
            if int(row.get(schema.TRANS_TYPE) or -1) in outgoing_types
            and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
            == const.TRANS_STATUS_PENDING
        ]
        if pending_outgoing:
            return await deferred_result(
                reason="outgoing_transaction_pending",
                message="Cancellation is queued until an existing fee, reward, or refund transaction is resolved.",
            )

        if spot_status == const.SPOT_STATUS_DRAFT and confirmed_deposit_total <= 0:
            await db_access.set_spot_status_to_cancelled(db, spot_id=int(spot_id))
            await db.commit()
            await notify_cancellation_change()
            return {
                "ok": True, "spot_id": int(spot_id), "cancelled": True,
                "cancellation_pending": False, "confirmed_deposit_total": 0,
                "failed_deposit_count": len(failed_deposits),
                "failed_deposit_total": failed_deposit_total,
                "manual_review_required": bool(failed_deposits),
                "confirmed_outgoing_total": 0,
                "confirmed_creation_fee_total": 0,
                "remaining_cancellable_total": 0,
                "desired_fee_total": 0,
                "desired_refund_total": 0,
                "confirmed_fee_total": 0,
                "confirmed_refund_total": 0,
                "remaining_amount": 0,
                "fee_amount": 0,
                "refund_amount": 0,
                "refund_address": None,
                "fee_address": fee_address or getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
            }

        confirmed_claim_total = sum(
            int(row.get(schema.TRANS_AMOUNT) or 0) for row in transactions
            if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CLAIM
            and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
        )
        confirmed_creation_fee_total = sum(
            int(row.get(schema.TRANS_AMOUNT) or 0) for row in transactions
            if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CREATION_FEE
            and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
        )
        remaining_cancellable_total = max(
            0, confirmed_deposit_total - confirmed_claim_total - confirmed_creation_fee_total
        )
        desired_fee_total = min(
            max(0, int(getattr(const, "SPOT_CANCELLATION_FEE", 0) if cancellation_fee is None else cancellation_fee)),
            remaining_cancellable_total,
        )
        desired_refund_total = max(0, remaining_cancellable_total - desired_fee_total)
        confirmed_fee_total = sum(
            int(row.get(schema.TRANS_AMOUNT) or 0) for row in transactions
            if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_PLAT_FEE
            and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
        )
        confirmed_refund_total = sum(
            int(row.get(schema.TRANS_AMOUNT) or 0) for row in transactions
            if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CANCEL_SPOT
            and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
        )
        fee_amount = max(0, desired_fee_total - confirmed_fee_total)
        refund_amount = max(0, desired_refund_total - confirmed_refund_total)
        remaining_amount = fee_amount + refund_amount
        confirmed_outgoing_total = (
            confirmed_claim_total + confirmed_creation_fee_total
            + confirmed_fee_total + confirmed_refund_total
        )

        refund_source = next((
            row
            for row in confirmed_deposits
            if str(row.get(schema.TRANS_FROM_ADDRESS) or "").strip()
        ), None)
        refund_address = (
            str(refund_source.get(schema.TRANS_FROM_ADDRESS) or "").strip()
            if refund_source is not None
            else None
        )
        refund_source_tx_hash = (
            str(refund_source.get(schema.TRANS_TX_HASH) or "").strip()
            if refund_source is not None
            else None
        )
        if refund_amount > 0 and not refund_address:
            raise ValueError("cannot refund this spot because no original deposit sender address is recorded")

        result_base = {
            "ok": True, "spot_id": int(spot_id),
            "confirmed_deposit_total": confirmed_deposit_total,
            "failed_deposit_count": len(failed_deposits),
            "failed_deposit_total": failed_deposit_total,
            "manual_review_required": bool(failed_deposits),
            "confirmed_outgoing_total": confirmed_outgoing_total,
            "confirmed_creation_fee_total": confirmed_creation_fee_total,
            "remaining_cancellable_total": remaining_cancellable_total,
            "desired_fee_total": desired_fee_total,
            "desired_refund_total": desired_refund_total,
            "confirmed_fee_total": confirmed_fee_total,
            "confirmed_refund_total": confirmed_refund_total,
            "remaining_amount": remaining_amount,
            "fee_amount": fee_amount,
            "refund_amount": refund_amount,
            "refund_address": refund_address,
            "fee_address": fee_address or getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
        }

        if remaining_amount <= 0:
            await db_access.set_spot_status_to_cancelled(db, spot_id=int(spot_id))
            await db.commit()
            await notify_cancellation_change()
            return {
                **result_base, "cancelled": True, "cancellation_pending": False,
                "fee": {"ok": True, "skipped": True, "reason": "no_fee_due", "trans_id": None},
                "refund": {"ok": True, "skipped": True, "reason": "no_refund_due", "trans_id": None},
            }

        await db.commit()
    except Exception:
        try:
            await db.rollback()
        finally:
            raise

    resolved_refund_address = refund_address
    if refund_amount > 0:
        try:
            resolved_refund_address = await resolve_nimiq_pay_payout_address(
                str(refund_address),
                source_tx_hash=refund_source_tx_hash,
            )
        except Exception as exc:
            logger.warning(
                "Cancellation refund address resolution deferred: spot_id=%s error=%s",
                int(spot_id),
                wallet.redact_secret_values(exc),
            )
            await notify_cancellation_change()
            return {
                **result_base,
                "cancelled": False,
                "cancellation_pending": True,
                "deferred": True,
                "reason": "refund_address_resolution_pending",
                "message": "Cancellation is queued while the original Nimiq Pay funding account is resolved safely.",
            }
        result_base["refund_address"] = resolved_refund_address

    try:
        fee_result = (
            await submit_platform_fee_transaction(
                db, spot_id=int(spot_id), amount=fee_amount, fee_address=fee_address
            )
            if fee_amount > 0
            else {"ok": True, "skipped": True, "reason": "no_fee", "trans_id": None}
        )
        refund_result = (
            await submit_spot_refund_transaction(
                db, spot_id=int(spot_id), to_address=str(resolved_refund_address), amount=refund_amount
            )
            if refund_amount > 0
            else {"ok": True, "skipped": True, "reason": "no_refund", "trans_id": None}
        )
    except sqlite3.IntegrityError as exc:
        message = str(exc).lower()
        if "trans.spot_id" in message and "trans.type" in message:
            return {
                **result_base, "cancelled": False, "cancellation_pending": True,
                "deferred": True, "reason": "concurrent_cancellation_transaction",
                "message": "Cancellation is already being processed.",
            }
        raise
    except Exception as exc:
        logger.error(
            "Cancellation send deferred after helper failure: spot_id=%s error=%s",
            int(spot_id),
            wallet.redact_secret_values(exc),
        )
        return {
            **result_base,
            "cancelled": False,
            "cancellation_pending": True,
            "deferred": True,
            "reason": "send_retry_pending",
            "message": "Cancellation was accepted and its transactions will retry automatically.",
        }

    await notify_cancellation_change()
    return {
        **result_base, "cancelled": False, "cancellation_pending": True,
        "fee": fee_result, "refund": refund_result,
    }


async def submit_claim_reward_transaction(
    db,
    *,
    claim_id: int,
    amount: int,
    to_address: str | None = None,
) -> RowDict:
    """Submit and record one CLAIM reward transaction.

    The TRANSACTION row is created and committed before the chain send. That
    gives each payout a durable local intent and prevents settlement retries
    from broadcasting the same reward twice if the process crashes after send.
    """
    amount = int(amount)
    if amount <= 0:
        return {"ok": True, "claim_id": int(claim_id), "skipped": True, "reason": "zero_amount", "trans_id": None}

    claim = await db_access.get_claim(db, claim_id=int(claim_id))
    if claim is None:
        raise ValueError(f"claim id={claim_id} does not exist")

    if await db_access.has_nonfailed_claim_payout_transaction(db, claim_id=int(claim_id)):
        return {"ok": True, "claim_id": int(claim_id), "already_exists": True, "trans_id": None}

    spot = await db_access.get_spot(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
    if spot is None:
        raise ValueError(f"spot for claim id={claim_id} does not exist")

    clean_to_address = str(
        to_address
        or claim.get(getattr(schema, "CLAIM_PAYOUT_ADDRESS", "payout_address"))
        or ""
    ).strip()

    if not clean_to_address and getattr(const, "ALLOW_DEV_WALLET_SENDS", False):
        template = getattr(
            const,
            "DEV_PRIZEDRAW_PAYOUT_ADDRESS_TEMPLATE",
            "NQ00 NIMHUNT DEV CLAIM PAYOUT USER {user_id}",
        )
        clean_to_address = str(template).format(
            user_id=int(claim[schema.CLAIM_RECIPIENT]),
            claim_id=int(claim_id),
            spot_id=int(claim[schema.CLAIM_SPOT_ID]),
        )

    if not clean_to_address:
        raise ValueError("claim has no payout_address; ask the user to enter through Nimiq Pay again")

    clean_to_address = await resolve_nimiq_pay_payout_address(clean_to_address)

    transaction_kind = (
        "Prizedraw"
        if await db_access.is_prizedraw(db, spot_id=int(claim[schema.CLAIM_SPOT_ID]))
        else "Claim"
    )

    result = await _submit_recorded_chain_send(
        db,
        spot=spot,
        to_address=clean_to_address,
        amount=amount,
        memo=build_transaction_description(transaction_kind, spot.get(schema.SPOT_TITLE)),
        intent_kind="claim_reward",
        intent_primary_id=int(claim_id),
        create_transaction=db_access.create_claim_transaction,
        create_transaction_kwargs={
            "user_id": int(claim[schema.CLAIM_RECIPIENT]),
            "claim_id": int(claim_id),
        },
    )

    return {
        **result,
        "claim_id": int(claim_id),
        "already_exists": False,
    }


async def run_once() -> RowDict:
    """Convenience wrapper for simple manual tests."""
    return await check_pending_transactions()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), indent=2))
