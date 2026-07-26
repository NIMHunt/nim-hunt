"""Preserve Nimiq Pay's user-facing return address for Spot refunds.

Nimiq Pay can spend through a temporary HTLC even though ``listAccounts()``
returns the user's ordinary account address.  The chain sender is authoritative
for proving that a deposit happened, but it is not a safe refund destination.
This hook stores the address shared by Nimiq Pay alongside each submitted
deposit hash and uses it for cancellation and end-of-Spot remainder refunds.

Claim payouts are intentionally unchanged: resolver calls without a source
transaction continue through the existing claim-address path.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import constants as const
import database as schema
import trans_updater
import wallet
from database import get_db

RowDict = dict[str, Any]
DepositRecorder = Callable[..., Awaitable[RowDict]]
PayoutResolver = Callable[..., Awaitable[str]]

_RETURN_ADDRESS_TABLE = "nimiq_pay_return_address"
_INSTALLED = False
_ORIGINAL_RECORD: DepositRecorder | None = None
_ORIGINAL_RESOLVE: PayoutResolver | None = None


async def _ensure_return_address_table(db) -> None:
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RETURN_ADDRESS_TABLE} (
            source_tx_hash TEXT PRIMARY KEY COLLATE NOCASE,
            spot_id INTEGER NOT NULL,
            return_address TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            CHECK (TRIM(source_tx_hash) != ''),
            CHECK (TRIM(return_address) != ''),
            FOREIGN KEY (spot_id)
                REFERENCES {schema.SPOT_TABLE_NAME}({schema.SPOT_ID})
                ON DELETE CASCADE
        );
        """
    )
    await db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_nimiq_pay_return_address_spot
        ON {_RETURN_ADDRESS_TABLE}(spot_id, created_at);
        """
    )


async def _spot_return_address(db, *, spot_id: int) -> str | None:
    cur = await db.execute(
        f"""
        SELECT return_address
        FROM {_RETURN_ADDRESS_TABLE}
        WHERE spot_id = ?
        ORDER BY created_at ASC, source_tx_hash ASC
        LIMIT 1;
        """,
        (int(spot_id),),
    )
    row = await cur.fetchone()
    return str(row["return_address"]) if row is not None else None


async def _remember_return_address(
    db,
    *,
    spot_id: int,
    source_tx_hash: str,
    return_address: str,
) -> None:
    await _ensure_return_address_table(db)

    existing_for_spot = await _spot_return_address(db, spot_id=int(spot_id))
    if existing_for_spot is not None:
        existing_for_spot = wallet.normalise_nimiq_address(
            existing_for_spot,
            field_name="stored Nimiq Pay return address",
            allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
        )
        if existing_for_spot != return_address:
            raise ValueError(
                "Additional deposits for this Spot must use its original Nimiq Pay account."
            )

    cur = await db.execute(
        f"""
        SELECT spot_id, return_address
        FROM {_RETURN_ADDRESS_TABLE}
        WHERE source_tx_hash = ?
        LIMIT 1;
        """,
        (source_tx_hash,),
    )
    existing_hash = await cur.fetchone()
    if existing_hash is not None:
        existing_address = wallet.normalise_nimiq_address(
            str(existing_hash["return_address"]),
            field_name="stored Nimiq Pay return address",
            allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
        )
        if int(existing_hash["spot_id"]) != int(spot_id) or existing_address != return_address:
            raise ValueError("this transaction hash is already attached to a different return address")
        return

    await db.execute(
        f"""
        INSERT INTO {_RETURN_ADDRESS_TABLE} (
            source_tx_hash,
            spot_id,
            return_address
        ) VALUES (?, ?, ?);
        """,
        (source_tx_hash, int(spot_id), return_address),
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
    """Record a deposit while preserving Nimiq Pay's return/top-up address."""
    original = _ORIGINAL_RECORD
    if original is None:
        raise RuntimeError("refund-address safety hook is not installed")

    return_address = wallet.normalise_nimiq_address(
        str(from_address or ""),
        field_name="Nimiq Pay return address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )
    clean_hash = str(tx_hash or "").strip().lower()
    if not trans_updater._NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash):
        raise ValueError("tx_hash must be a 64-character hexadecimal Nimiq transaction hash")

    result = await original(
        db,
        user_id=int(user_id),
        spot_id=int(spot_id),
        amount=int(amount),
        from_address=return_address,
        tx_hash=clean_hash,
        to_address=to_address,
    )
    await _remember_return_address(
        db,
        spot_id=int(spot_id),
        source_tx_hash=clean_hash,
        return_address=return_address,
    )
    return result


async def _mapped_return_address(
    *,
    source_tx_hash: str,
    chain_sender: str,
) -> str | None:
    clean_hash = str(source_tx_hash or "").strip().lower()
    if not trans_updater._NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash):
        raise RuntimeError("payout source transaction hash is invalid")

    clean_chain_sender = wallet.normalise_nimiq_address(
        chain_sender,
        field_name="confirmed deposit sender",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )

    async with get_db() as db:
        await _ensure_return_address_table(db)
        # This connection may have created the additive hook table lazily.
        await db.commit()
        cur = await db.execute(
            f"""
            SELECT
                r.return_address,
                r.spot_id,
                t.{schema.TRANS_FROM_ADDRESS} AS chain_sender
            FROM {_RETURN_ADDRESS_TABLE} r
            JOIN {schema.TRANS_TABLE_NAME} t
              ON LOWER(t.{schema.TRANS_TX_HASH}) = r.source_tx_hash
             AND t.{schema.TRANS_SPOT_ID} = r.spot_id
            WHERE r.source_tx_hash = ?
              AND t.{schema.TRANS_TYPE} = ?
              AND t.{schema.TRANS_STATUS} = ?
            LIMIT 1;
            """,
            (
                clean_hash,
                const.TRANS_TYPE_FILL_SPOT,
                const.TRANS_STATUS_CONFIRMED,
            ),
        )
        row = await cur.fetchone()

    if row is None:
        return None

    stored_chain_sender = wallet.normalise_nimiq_address(
        str(row["chain_sender"] or ""),
        field_name="stored confirmed deposit sender",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )
    if stored_chain_sender != clean_chain_sender:
        raise RuntimeError("refund source sender does not match the confirmed deposit record")

    return wallet.normalise_nimiq_address(
        str(row["return_address"] or ""),
        field_name="Nimiq Pay return address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )


async def resolve_nimiq_pay_payout_address(
    address: str,
    *,
    source_tx_hash: str | None = None,
    rpc_url: str = trans_updater.DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = trans_updater.DEFAULT_RPC_TIMEOUT_SECONDS,
    force_chain_resolution: bool | None = None,
) -> str:
    """Use the recorded Nimiq Pay account for refunds; leave claims unchanged."""
    original = _ORIGINAL_RESOLVE
    if original is None:
        raise RuntimeError("refund-address safety hook is not installed")

    if not source_tx_hash:
        return await original(
            address,
            source_tx_hash=None,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
            force_chain_resolution=force_chain_resolution,
        )

    mapped = await _mapped_return_address(
        source_tx_hash=source_tx_hash,
        chain_sender=str(address or ""),
    )
    if mapped is None:
        if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
            return await original(
                address,
                source_tx_hash=source_tx_hash,
                rpc_url=rpc_url,
                timeout_seconds=int(timeout_seconds),
                force_chain_resolution=force_chain_resolution,
            )
        raise RuntimeError(
            "the confirmed deposit has no recorded Nimiq Pay return address; manual reconciliation is required"
        )

    should_validate_chain = (
        bool(getattr(const, "PUBLIC_DEPLOYMENT", False))
        if force_chain_resolution is None
        else bool(force_chain_resolution)
    )
    if not should_validate_chain:
        return mapped

    account = await trans_updater.get_chain_account_by_address(
        mapped,
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )
    account_type = trans_updater._normalise_chain_account_type(account.get("type"))
    if account_type != "basic":
        raise RuntimeError(
            "the recorded Nimiq Pay return address is not a basic account; manual reconciliation is required"
        )
    return mapped


def install() -> None:
    """Install the deposit/return-address hooks once."""
    global _INSTALLED, _ORIGINAL_RECORD, _ORIGINAL_RESOLVE
    if _INSTALLED:
        return
    _ORIGINAL_RECORD = trans_updater.record_spot_deposit_transaction
    _ORIGINAL_RESOLVE = trans_updater.resolve_nimiq_pay_payout_address
    trans_updater.record_spot_deposit_transaction = record_spot_deposit_transaction
    trans_updater.resolve_nimiq_pay_payout_address = resolve_nimiq_pay_payout_address
    _INSTALLED = True


__all__ = [
    "install",
    "record_spot_deposit_transaction",
    "resolve_nimiq_pay_payout_address",
]
