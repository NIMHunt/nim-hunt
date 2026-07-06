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
import urllib.error
from pathlib import Path
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

import constants as const
import database as schema
from database import get_db

import cache
import db_access
import wallet


RowDict = dict[str, Any]
TransOutcome = Literal["pending", "confirmed", "failed", "cancelled", "unknown"]

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
    address = str(value or "").strip()
    if not address:
        raise ValueError(f"{field_name} must be non-empty")
    if not address.upper().startswith("NQ"):
        raise ValueError(f"{field_name} must be a Nimiq user-friendly address")
    return address


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


async def mark_trans_as_confirmed(
    db,
    trans: RowDict,
    *,
    block_number: int | None = None,
) -> RowDict:
    """Mark a TRANSACTION confirmed in the DB, then remove it from pending cache."""
    trans_id = _transaction_id(trans)

    async with db_access.transaction(db):
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

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=trans.get(schema.TRANS_SPOT_ID),
        user_id=trans.get(schema.TRANS_USER_ID),
    )

    return {"trans_id": trans_id, "status": "confirmed", "block_number": block_number}


async def mark_trans_as_failed(db, trans: RowDict, *, reason: str | None = None) -> RowDict:
    """Mark a TRANSACTION failed in the DB, then remove it from pending cache."""
    trans_id = _transaction_id(trans)

    async with db_access.transaction(db):
        await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=trans.get(schema.TRANS_SPOT_ID),
        user_id=trans.get(schema.TRANS_USER_ID),
    )

    return {"trans_id": trans_id, "status": "failed", "reason": reason}


async def mark_trans_as_cancelled(db, trans: RowDict, *, reason: str | None = None) -> RowDict:
    """Mark a TRANSACTION cancelled in the DB, then remove it from pending cache.

    Nimiq itself usually cannot tell us that a basic transfer was "cancelled";
    this function is here for app-level cancellations, e.g. when NimHunt decides
    to abandon a pending transaction before it is confirmed.
    """
    trans_id = _transaction_id(trans)

    async with db_access.transaction(db):
        await db_access.set_transaction_status_to_cancelled(db, trans_id=trans_id)

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=trans.get(schema.TRANS_SPOT_ID),
        user_id=trans.get(schema.TRANS_USER_ID),
    )

    return {"trans_id": trans_id, "status": "cancelled", "reason": reason}


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
                finalised.append(
                    await mark_trans_as_confirmed(
                        db,
                        trans,
                        block_number=status.block_number,
                    )
                )
            elif status.status == "failed":
                finalised.append(await mark_trans_as_failed(db, trans, reason=status.reason))
            elif status.status == "cancelled":
                finalised.append(await mark_trans_as_cancelled(db, trans, reason=status.reason))
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

    clean_to_address = str(to_address or spot.get(schema.SPOT_DEPOSIT_ADDRESS) or "").strip()
    if not clean_to_address:
        raise ValueError("spot has no deposit address")

    trans_id = await db_access.create_spot_deposit_transaction(
        db,
        user_id=int(user_id),
        spot_id=int(spot_id),
        amount=amount,
        from_address=str(from_address or "Nimiq Pay").strip() or "Nimiq Pay",
        to_address=clean_to_address,
        tx_hash=str(tx_hash).strip(),
    )

    await cache.notify_transaction_changed(
        db,
        trans_id=int(trans_id),
        spot_id=int(spot_id),
        user_id=int(user_id),
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

    result = await submit_chain_send_from_spot_deposit(
        spot=spot,
        to_address=clean_fee_address,
        amount=amount,
        memo=f"NimHunt platform fee spot {int(spot_id)}",
    )
    trans_id = await db_access.create_platform_fee_transaction(
        db,
        user_id=int(spot[schema.SPOT_CREATED_BY]),
        spot_id=int(spot_id),
        amount=amount,
        from_address=result.from_address,
        to_address=result.to_address,
        tx_hash=result.tx_hash,
    )
    await cache.notify_transaction_changed(
        db,
        trans_id=int(trans_id),
        spot_id=int(spot_id),
        user_id=int(spot[schema.SPOT_CREATED_BY]),
    )
    return {"ok": True, "trans_id": int(trans_id), "amount": amount, "to_address": result.to_address}


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

    result = await submit_chain_send_from_spot_deposit(
        spot=spot,
        to_address=to_address,
        amount=amount,
        memo=f"NimHunt spot refund {int(spot_id)}",
    )
    trans_id = await db_access.create_spot_refund_transaction(
        db,
        user_id=int(spot[schema.SPOT_CREATED_BY]),
        spot_id=int(spot_id),
        amount=amount,
        from_address=result.from_address,
        to_address=result.to_address,
        tx_hash=result.tx_hash,
    )
    await cache.notify_transaction_changed(
        db,
        trans_id=int(trans_id),
        spot_id=int(spot_id),
        user_id=int(spot[schema.SPOT_CREATED_BY]),
    )
    return {"ok": True, "trans_id": int(trans_id), "amount": amount, "to_address": result.to_address}


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
    nonfailed_outgoing_total = sum(
        int(trans.get(schema.TRANS_AMOUNT) or 0)
        for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) in outgoing_types
        and int(trans.get(schema.TRANS_STATUS) or -1) != const.TRANS_STATUS_FAILED
    )

    remaining_amount = max(0, confirmed_deposit_total - nonfailed_outgoing_total)
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

    await db_access.modify_spot_status(db, spot_id=int(spot_id), status=const.SPOT_STATUS_CANCELLED)
    await cache.notify_spot_changed(db, spot_id=int(spot_id))
    await cache.notify_user_changed(db, user_id=int(spot[schema.SPOT_CREATED_BY]))

    return {
        "ok": True,
        "spot_id": int(spot_id),
        "cancelled": True,
        "confirmed_deposit_total": confirmed_deposit_total,
        "nonfailed_outgoing_total": nonfailed_outgoing_total,
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

    Settlement code should call this rather than talking to wallet.py directly.
    A zero amount is a valid no-op for losing Prizedraw entries.
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

    result = await submit_chain_send_from_spot_deposit(
        spot=spot,
        to_address=clean_to_address,
        amount=amount,
        memo=f"NimHunt claim {int(claim_id)}",
    )

    trans_id = await db_access.create_claim_transaction(
        db,
        user_id=int(claim[schema.CLAIM_RECIPIENT]),
        claim_id=int(claim_id),
        amount=amount,
        from_address=result.from_address,
        to_address=result.to_address,
        tx_hash=result.tx_hash,
    )

    await cache.notify_transaction_changed(
        db,
        trans_id=int(trans_id),
        spot_id=int(claim[schema.CLAIM_SPOT_ID]),
        user_id=int(claim[schema.CLAIM_RECIPIENT]),
    )

    return {
        "ok": True,
        "claim_id": int(claim_id),
        "trans_id": int(trans_id),
        "amount": amount,
        "to_address": result.to_address,
        "already_exists": False,
    }


async def run_once() -> RowDict:
    """Convenience wrapper for simple manual tests."""
    return await check_pending_transactions()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), indent=2))
