"""
trans_updater.py

Small transaction-status updater for NimHunt.

This module checks the pending TRANSACTION cache, asks a Nimiq JSON-RPC server
about each transaction hash, and finalises any transaction that is no longer
pending.

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
import os
import shlex
import subprocess
import time
import secrets
import urllib.error
from pathlib import Path
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

import constants as const
import database as schema
from database import get_db

import cache
import db_access
import wallet


RowDict = dict[str, Any]
TransOutcome = Literal["pending", "confirmed", "failed", "unknown"]

# Public RPC servers are useful for development, but a production deployment
# should point this at your own node or a service you trust.
DEFAULT_NIMIQ_RPC_URL = os.getenv("NIMHUNT_NIMIQ_RPC_URL", getattr(const, "NIMIQ_RPC_URL", "https://rpc.nimiqwatch.com"))
DEFAULT_RPC_TIMEOUT_SECONDS = int(os.getenv("NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS", str(getattr(const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12))))

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


_TRANS_CHECK_TASK: asyncio.Task | None = None
_TRANS_CHECK_STOP_EVENT: asyncio.Event | None = None
_TRANS_CHECK_LAST_RESULT: RowDict | None = None
_TRANS_CHECK_LAST_ERROR: str | None = None


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
    mnemonic_env = getattr(const, "NIMHUNT_NIMIQ_MNEMONIC_ENV", "NIMHUNT_NIMIQ_MNEMONIC")
    default_test_env = getattr(
        const,
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV",
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
    )
    if os.getenv(mnemonic_env):
        return True
    if os.getenv(default_test_env) == "1" and str(getattr(const, "NIMIQ_NETWORK", "TestAlbatross")) != "MainAlbatross":
        return True
    return False


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
        raise RuntimeError(f"Nimiq helper command failed: {stderr or stdout or completed.returncode}")

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Nimiq helper command returned invalid JSON: {(completed.stdout or '').strip()[:500]}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Nimiq helper command returned non-object JSON")
    if data.get("ok") is False:
        raise RuntimeError(str(data.get("message") or data.get("error") or "Nimiq helper failed"))
    return data


def _integration_payload_base() -> dict[str, Any]:
    return {
        "app": getattr(const, "APP_NAME", "NimHunt"),
        "network": getattr(const, "NIMIQ_NETWORK", "TestAlbatross"),
        "network_id": int(getattr(const, "NIMIQ_NETWORK_ID", 6)),
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

    if int(chain_amount) < expected_amount:
        return VerifiedChainDetails(ok=False, reason="confirmed transaction amount is lower than recorded amount")

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

    async with db_access.transaction(db):
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
    """Return True when the returned transaction explicitly failed execution."""
    return isinstance(value, dict) and value.get("executionResult") is False


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
    start_at: str = "",
) -> Any:
    """Return recent transactions for one address via Nimiq RPC."""
    return await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=rpc_url,
        method="getTransactionsByAddress",
        params=[address, int(max_transactions), str(start_at or "")],
        timeout_seconds=int(timeout_seconds),
    )


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


async def check_pending_transaction(
    trans: RowDict,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
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

    if chain_status.status != "pending":
        return chain_status

    if _transaction_age_seconds(trans) >= int(fail_after_seconds):
        return ChainTransactionStatus(
            status="failed",
            tx_hash=tx_hash,
            raw=chain_status.raw,
            reason=f"hash not confirmed after {int(fail_after_seconds)} seconds",
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
    """Mark a standard Spot cancelled only after refund/fee sends are final."""
    if spot_id is None:
        return False

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None or int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_PUBLISHED:
        return False
    if await db_access.is_prizedraw(db, spot_id=int(spot_id)):
        return False

    transactions = await db_access.get_transactions_by_spot(db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT)
    outgoing_types = {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
    has_cancellation_intent = any(int(row.get(schema.TRANS_TYPE) or -1) in outgoing_types for row in transactions)
    if not has_cancellation_intent:
        return False
    if any(
        int(row.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(row.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_PENDING
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
    remaining_cancellable_total = max(0, confirmed_deposit_total - confirmed_claim_total)
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
    """Mark a TRANSACTION confirmed in the DB, then remove it from pending cache."""
    trans_id = _transaction_id(trans)
    completed_prizedraw_payout = await _claim_transaction_is_completed_prizedraw_payout(db, trans)
    claim_id = trans.get(schema.TRANS_CLAIM_ID)

    async with db_access.transaction(db):
        if verified_details is not None and verified_details.ok:
            await db_access.update_transaction_chain_details(
                db,
                trans_id=trans_id,
                from_address=verified_details.from_address,
                to_address=verified_details.to_address,
                amount=verified_details.amount,
            )

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
            db, spot_id=trans.get(schema.TRANS_SPOT_ID)
        )

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=trans.get(schema.TRANS_SPOT_ID),
        user_id=trans.get(schema.TRANS_USER_ID),
    )
    if completed_prizedraw_payout and claim_id is not None:
        await cache.notify_claim_changed(
            db,
            spot_id=trans.get(schema.TRANS_SPOT_ID),
            user_id=trans.get(schema.TRANS_USER_ID),
        )
    if cancelled_finalized:
        await cache.notify_spot_changed(db, spot_id=trans.get(schema.TRANS_SPOT_ID))

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
    if cancelled_finalized or int(trans.get(schema.TRANS_TYPE) or -1) in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}:
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
                    finalised.append(await mark_trans_as_failed(db, trans, reason=verified.reason))
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

    return {
        "ok": True,
        "checked_count": len(checked),
        "finalised_count": len(finalised),
        "still_pending_count": len(still_pending),
        "unknown_count": len(unknown),
        "checked": checked,
        "finalised": finalised,
        "still_pending": still_pending,
        "unknown": unknown,
    }


async def _transaction_check_loop(interval_seconds: int) -> None:
    """Background loop that keeps pending TRANSACTION rows moving."""
    global _TRANS_CHECK_LAST_RESULT, _TRANS_CHECK_LAST_ERROR

    assert _TRANS_CHECK_STOP_EVENT is not None
    while not _TRANS_CHECK_STOP_EVENT.is_set():
        try:
            _TRANS_CHECK_LAST_RESULT = await check_pending_transactions()
            _TRANS_CHECK_LAST_ERROR = None
        except Exception as exc:  # pragma: no cover - defensive loop guard
            _TRANS_CHECK_LAST_ERROR = repr(exc)

        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                _TRANS_CHECK_STOP_EVENT.wait(),
                timeout=max(1, int(interval_seconds)),
            )


async def start_transaction_refresher(
    *,
    run_immediately: bool = False,
    interval_seconds: int = DEFAULT_TRANSACTION_CHECK_INTERVAL_SECONDS,
) -> None:
    """Start the lightweight background transaction-status loop once."""
    global _TRANS_CHECK_TASK, _TRANS_CHECK_STOP_EVENT, _TRANS_CHECK_LAST_RESULT, _TRANS_CHECK_LAST_ERROR

    if _TRANS_CHECK_TASK is not None and not _TRANS_CHECK_TASK.done():
        return

    _TRANS_CHECK_STOP_EVENT = asyncio.Event()

    if run_immediately:
        try:
            _TRANS_CHECK_LAST_RESULT = await check_pending_transactions()
            _TRANS_CHECK_LAST_ERROR = None
        except Exception as exc:  # pragma: no cover - startup should not fail app boot
            _TRANS_CHECK_LAST_ERROR = repr(exc)

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
    """Record a user-initiated SPOT deposit returned by Nimiq Pay.

    The user signs/sends this transaction in the Pay webview. NimHunt only
    records the returned hash and later confirms it through check_pending_*().
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")

    clean_to_address = wallet.normalise_nimiq_address(
        str(to_address or spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="deposit to_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )

    try:
        clean_from_address = wallet.normalise_nimiq_address(
            str(from_address or ""),
            field_name="deposit from_address",
            allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
        )
    except ValueError:
        if not getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False):
            raise
        clean_from_address = str(from_address or "Nimiq Pay").strip() or "Nimiq Pay"

    trans_id = await db_access.create_spot_deposit_transaction(
        db,
        user_id=int(user_id),
        spot_id=int(spot_id),
        amount=amount,
        from_address=clean_from_address,
        to_address=clean_to_address,
        tx_hash=str(tx_hash).strip(),
    )

    return {"ok": True, "trans_id": int(trans_id), "spot_id": int(spot_id), "amount": amount}


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
        memo=f"NimHunt platform fee spot {int(spot_id)}",
        intent_kind="platform_fee",
        intent_primary_id=int(spot_id),
        create_transaction=db_access.create_platform_fee_transaction,
        create_transaction_kwargs={
            "user_id": int(spot[schema.SPOT_CREATED_BY]),
            "spot_id": int(spot_id),
        },
    )
    return {**result, "spot_id": int(spot_id)}


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
        memo=f"NimHunt spot refund {int(spot_id)}",
        intent_kind="spot_refund",
        intent_primary_id=int(spot_id),
        create_transaction=db_access.create_spot_refund_transaction,
        create_transaction_kwargs={
            "user_id": int(spot[schema.SPOT_CREATED_BY]),
            "spot_id": int(spot_id),
        },
    )
    return {**result, "spot_id": int(spot_id)}


async def submit_spot_cancellation_transactions(
    db,
    *,
    spot_id: int,
    cancellation_fee: int | None = None,
    fee_address: str | None = None,
) -> RowDict:
    """Cancel a published standard Spot and submit fee/refund transactions.

    The refund address is taken from the original confirmed SPOT deposit
    transaction's from_address. This means a malicious caller cannot choose a
    new refund address at cancellation time.
    """
    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_PUBLISHED:
        raise ValueError("only published spots can be cancelled")
    if await db_access.is_prizedraw(db, spot_id=int(spot_id)):
        raise ValueError("Prizedraw spots cannot be cancelled through this standard cancellation flow")

    transactions = await db_access.get_transactions_by_spot(db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT)
    confirmed_deposits = [
        trans for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    ]
    confirmed_deposits.sort(key=lambda row: int(row.get(schema.TRANS_CREATED_AT) or 0))
    confirmed_deposit_total = sum(int(trans.get(schema.TRANS_AMOUNT) or 0) for trans in confirmed_deposits)

    outgoing_types = {const.TRANS_TYPE_CLAIM, const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
    pending_outgoing = [
        trans for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_PENDING
    ]
    if pending_outgoing:
        raise ValueError(
            "This spot already has a pending cancellation, refund, fee, or reward transaction. "
            "Wait for it to confirm or fail before cancelling again."
        )

    confirmed_outgoing_total = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(trans.get(schema.TRANS_STATUS) or -1) == const.TRANS_STATUS_CONFIRMED
    )

    remaining_amount = max(0, confirmed_deposit_total - confirmed_outgoing_total)
    fee_amount = min(
        max(0, int(getattr(const, "SPOT_CANCELLATION_FEE", 0) if cancellation_fee is None else cancellation_fee)),
        remaining_amount,
    )
    refund_amount = max(0, remaining_amount - fee_amount)

    refund_address = None
    for trans in confirmed_deposits:
        candidate = str(trans.get(schema.TRANS_FROM_ADDRESS) or "").strip()
        if candidate:
            refund_address = candidate
            break
    if refund_amount > 0 and not refund_address:
        raise ValueError("cannot refund this spot because no original deposit sender address is recorded")

    fee_result = await submit_platform_fee_transaction(
        db,
        spot_id=int(spot_id),
        amount=fee_amount,
        fee_address=fee_address,
    ) if fee_amount > 0 else {"ok": True, "skipped": True, "reason": "no_fee", "trans_id": None}

    refund_result = await submit_spot_refund_transaction(
        db,
        spot_id=int(spot_id),
        to_address=str(refund_address),
        amount=refund_amount,
    ) if refund_amount > 0 else {"ok": True, "skipped": True, "reason": "no_refund", "trans_id": None}

    await cache.notify_spot_changed(db, spot_id=int(spot_id))
    await cache.notify_user_changed(db, user_id=int(spot[schema.SPOT_CREATED_BY]))

    return {
        "ok": True,
        "spot_id": int(spot_id),
        "cancelled": False,
        "cancellation_pending": True,
        "confirmed_deposit_total": confirmed_deposit_total,
        "confirmed_outgoing_total": confirmed_outgoing_total,
        "pending_outgoing_count": 0,
        "remaining_amount": remaining_amount,
        "fee_amount": fee_amount,
        "refund_amount": refund_amount,
        "fee": fee_result,
        "refund": refund_result,
        "refund_address": refund_address,
        "fee_address": fee_address or getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
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

    result = await _submit_recorded_chain_send(
        db,
        spot=spot,
        to_address=clean_to_address,
        amount=amount,
        memo=f"NimHunt claim {int(claim_id)}",
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
