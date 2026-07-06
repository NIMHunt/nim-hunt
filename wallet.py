"""
wallet.py

Wallet/key helpers for NimHunt.

This module deliberately keeps private-key material out of SQLite. SPOT rows
store only public deposit addresses and immutable derivation metadata. The
server can later recover the signing key by decrypting one master seed and
re-deriving the child key at SPOT.deposit_key_index / SPOT.deposit_key_path.

Important: wallet.py owns address/key management. Transaction submission is
orchestrated by trans_updater.py, which calls the narrow helpers here when it
needs a SPOT deposit address or a signed/broadcasted send from a SPOT deposit.

For real Nimiq integration, set NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND and
NIMHUNT_NIMIQ_SEND_COMMAND. Each command receives JSON on stdin and returns JSON
on stdout. This keeps the Python app independent from the exact official Nimiq
JS/Rust signing stack while preserving one clear integration seam.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

import constants as const


class WalletConfigError(RuntimeError):
    """Raised when wallet/key configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class DerivedSpotAddress:
    """Public deposit-address data safe to store on SPOT."""

    address: str
    key_index: int
    key_path: str
    key_version: int


@dataclass(frozen=True, slots=True)
class WalletSendResult:
    """Result returned after a send is submitted/broadcast."""

    tx_hash: str
    from_address: str
    to_address: str
    amount: int
    raw: Any | None = None


# ---------------------------------------------------------------------------
# Master-seed encryption helpers
# ---------------------------------------------------------------------------

def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _urlsafe_b64decode(data: str) -> bytes:
    data = str(data).strip()
    data += "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data.encode("ascii"))


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
    except Exception as exc:  # pragma: no cover - depends on venv
        raise WalletConfigError(
            "cryptography is required for encrypted master-seed handling. "
            "Install it in the venv or avoid calling wallet encryption helpers."
        ) from exc

    return AESGCM, PBKDF2HMAC, hashes


def _derive_encryption_key(secret: str, salt: bytes) -> bytes:
    AESGCM, PBKDF2HMAC, hashes = _require_cryptography()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390_000,
    )
    return kdf.derive(str(secret).encode("utf-8"))


def encrypt_master_seed_for_env(master_seed: str | bytes, secret: str) -> str:
    """Return an encrypted string suitable for NIMHUNT_MASTER_SEED_ENC.

    This is a helper for setup scripts/admin use. It is not called by normal
    request handling.
    """
    AESGCM, _, _ = _require_cryptography()
    seed_bytes = master_seed if isinstance(master_seed, bytes) else str(master_seed).encode("utf-8")
    if not seed_bytes:
        raise ValueError("master_seed must be non-empty")
    if not secret:
        raise ValueError("secret must be non-empty")

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_encryption_key(secret, salt)
    ciphertext = AESGCM(key).encrypt(nonce, seed_bytes, b"nimhunt-master-seed-v1")
    return "v1:" + _urlsafe_b64encode(salt + nonce + ciphertext)


def decrypt_master_seed_from_env_value(encrypted_value: str, secret: str) -> bytes:
    """Decrypt a value produced by encrypt_master_seed_for_env()."""
    AESGCM, _, _ = _require_cryptography()
    value = str(encrypted_value or "").strip()
    if not value.startswith("v1:"):
        raise WalletConfigError("Unsupported encrypted master-seed format")
    if not secret:
        raise WalletConfigError("Missing master-seed secret")

    payload = _urlsafe_b64decode(value[3:])
    if len(payload) < 16 + 12 + 16:
        raise WalletConfigError("Encrypted master-seed payload is too short")
    salt = payload[:16]
    nonce = payload[16:28]
    ciphertext = payload[28:]
    key = _derive_encryption_key(secret, salt)
    return AESGCM(key).decrypt(nonce, ciphertext, b"nimhunt-master-seed-v1")


def get_master_seed(*, required: bool = False) -> bytes | None:
    """Load the encrypted master seed, or a dev fallback if explicitly allowed."""
    encrypted = os.getenv(getattr(const, "NIMHUNT_MASTER_SEED_ENV", "NIMHUNT_MASTER_SEED_ENC"))
    secret = os.getenv(getattr(const, "NIMHUNT_MASTER_SEED_SECRET_ENV", "NIMHUNT_MASTER_SEED_SECRET"))

    if encrypted:
        return decrypt_master_seed_from_env_value(encrypted, secret or "")

    dev_seed = os.getenv(getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED"))
    if dev_seed:
        return dev_seed.encode("utf-8")

    if getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False):
        # Deterministic development-only seed. This is intentionally not secret
        # and must never secure real funds.
        return f"{const.APP_NAME}:development-placeholder-master-seed".encode("utf-8")

    if required:
        raise WalletConfigError(
            "No NimHunt master seed configured. Set NIMHUNT_MASTER_SEED_ENC and "
            "NIMHUNT_MASTER_SEED_SECRET, or set NIMHUNT_DEV_MASTER_SEED in development."
        )
    return None


# ---------------------------------------------------------------------------
# External Nimiq bridge helpers
# ---------------------------------------------------------------------------

def _command_from_env(env_name: str) -> list[str] | None:
    value = os.getenv(env_name)
    if not value or not value.strip():
        return None
    return shlex.split(value)


def _run_json_command(command: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    """Run a configured Nimiq helper command with JSON stdin/stdout.

    This command is the only intended seam between the Python app and an
    implementation built with the official Nimiq JS/Rust tooling. Keep private
    key material out of the JSON payload unless the helper is deliberately
    designed to receive it via a protected local pipe. Prefer having the helper
    read encrypted seed config from its own environment.
    """
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=int(getattr(const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12)),
        )
    except FileNotFoundError as exc:
        raise WalletConfigError(f"Nimiq helper command was not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise WalletConfigError("Nimiq helper command timed out") from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    parsed_stdout: dict[str, Any] | None = None
    if stdout:
        try:
            maybe_data = json.loads(stdout)
            if isinstance(maybe_data, dict):
                parsed_stdout = maybe_data
        except json.JSONDecodeError:
            parsed_stdout = None

    if completed.returncode != 0:
        if parsed_stdout is not None:
            message = str(
                parsed_stdout.get("message")
                or parsed_stdout.get("error")
                or parsed_stdout.get("reason")
                or parsed_stdout
            )
            raise WalletConfigError(f"Nimiq helper command failed: {message}")
        if stderr:
            raise WalletConfigError(f"Nimiq helper command failed: {stderr}")
        if stdout:
            raise WalletConfigError(f"Nimiq helper command failed: {stdout}")
        raise WalletConfigError(f"Nimiq helper command failed with exit code {completed.returncode}")

    if parsed_stdout is not None:
        data = parsed_stdout
    else:
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            detail = f" stdout={stdout!r}" if stdout else ""
            detail += f" stderr={stderr!r}" if stderr else ""
            raise WalletConfigError(f"Nimiq helper command returned invalid JSON.{detail}") from exc

    if not isinstance(data, dict):
        raise WalletConfigError("Nimiq helper command returned non-object JSON")
    if data.get("ok") is False:
        raise WalletConfigError(str(data.get("message") or data.get("error") or "Nimiq helper failed"))
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


# ---------------------------------------------------------------------------
# Spot deposit-address derivation
# ---------------------------------------------------------------------------

def spot_deposit_key_path(index: int, *, key_version: int | None = None) -> str:
    """Return the stored derivation path for a spot deposit key."""
    index = int(index)
    if index < 0:
        raise ValueError("deposit key index must be non-negative")
    template = getattr(const, "SPOT_DEPOSIT_KEY_PATH_TEMPLATE", "m/44'/242'/{index}'/0'")
    return str(template).format(index=index, version=key_version or const.SPOT_DEPOSIT_KEY_VERSION)


def _dev_address_from_seed(seed: bytes, *, key_index: int, key_path: str, key_version: int) -> str:
    material = f"spot-deposit:{key_version}:{key_path}:{key_index}".encode("utf-8")
    digest = hmac.new(seed, material, hashlib.blake2b).digest()
    token = base64.b32encode(digest[:20]).decode("ascii").rstrip("=")
    prefix = str(getattr(const, "PLACEHOLDER_SPOT_DEPOSIT_ADDRESS_PREFIX", "NQ00 NIMHUNT DEV SPOT DEPOSIT"))
    return f"{prefix} {token}"


def derive_spot_deposit_address(key_index: int, *, key_version: int | None = None) -> DerivedSpotAddress:
    """Derive the public deposit address for one SPOT.

    Production path: set NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND. The command gets
    key metadata and network config as JSON and must return at least
    `{ "address": "NQ..." }`.

    Development path: if no command is configured and dev placeholders are
    allowed, return a deterministic fake address so the UI/backend can still be
    exercised without touching real funds.
    """
    key_index = int(key_index)
    key_version = int(key_version or getattr(const, "SPOT_DEPOSIT_KEY_VERSION", 1))
    key_path = spot_deposit_key_path(key_index, key_version=key_version)

    command = _command_from_env(getattr(const, "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV", "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND"))
    if command:
        data = _run_json_command(
            command,
            {
                **_integration_payload_base(),
                "action": "derive_spot_deposit_address",
                "key_index": key_index,
                "key_path": key_path,
                "key_version": key_version,
            },
        )
        address = _validate_nimiq_address(str(data.get("address") or ""), field_name="address")
        return DerivedSpotAddress(
            address=address,
            key_index=int(data.get("key_index", key_index)),
            key_path=str(data.get("key_path") or key_path),
            key_version=int(data.get("key_version", key_version)),
        )

    if not getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False):
        raise WalletConfigError(
            "No real Nimiq address-derivation command is configured. Set "
            "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND, or enable development "
            "placeholders only for local tests."
        )

    seed = get_master_seed(required=True)
    address = _dev_address_from_seed(seed, key_index=key_index, key_path=key_path, key_version=key_version)
    return DerivedSpotAddress(
        address=address,
        key_index=key_index,
        key_path=key_path,
        key_version=key_version,
    )


# ---------------------------------------------------------------------------
# Sending funds
# ---------------------------------------------------------------------------

def _dev_tx_hash(*, from_address: str, to_address: str, amount: int) -> str:
    prefix = str(getattr(const, "PLACEHOLDER_SEND_TX_HASH_PREFIX", "devsend"))
    nonce = secrets.token_hex(16)
    digest = hashlib.sha256(f"{from_address}|{to_address}|{amount}|{nonce}".encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:58]}"


async def send_luna_from_spot_deposit(
    *,
    spot: dict[str, Any],
    to_address: str,
    amount: int,
    memo: str | None = None,
) -> WalletSendResult:
    """Sign/broadcast a send from a SPOT deposit address.

    trans_updater.py calls this helper when it has decided an app-level
    transaction should be submitted. In production, configure
    NIMHUNT_NIMIQ_SEND_COMMAND. The command must return a transaction hash.

    Expected command stdin shape includes:
        action, network, network_id, rpc_url, fee, from_address, to_address,
        amount, memo, deposit_key_index, deposit_key_path, deposit_key_version.

    Expected stdout contains at least `{ "tx_hash": "..." }` or
    `{ "hash": "..." }`.
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    from_address = _validate_nimiq_address(str(spot.get("deposit_address") or ""), field_name="from_address")
    to_address = _validate_nimiq_address(to_address, field_name="to_address")

    key_index = spot.get("deposit_key_index")
    if key_index is None:
        raise WalletConfigError("spot has no deposit_key_index; cannot derive signing key")
    key_version = int(spot.get("deposit_key_version") or getattr(const, "SPOT_DEPOSIT_KEY_VERSION", 1))
    key_path = str(spot.get("deposit_key_path") or spot_deposit_key_path(int(key_index), key_version=key_version))

    # Sanity check: the stored address must still match the configured address
    # derivation path. This catches stale/migrated rows before funds are sent.
    derived = derive_spot_deposit_address(int(key_index), key_version=key_version)
    if derived.address != from_address:
        raise WalletConfigError("stored deposit address does not match derived deposit address")

    command = _command_from_env(getattr(const, "NIMHUNT_NIMIQ_SEND_COMMAND_ENV", "NIMHUNT_NIMIQ_SEND_COMMAND"))
    if command:
        data = _run_json_command(
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
            raise WalletConfigError("Nimiq send command did not return tx_hash/hash")
        return WalletSendResult(
            tx_hash=tx_hash,
            from_address=str(data.get("from_address") or from_address),
            to_address=str(data.get("to_address") or to_address),
            amount=int(data.get("amount") or amount),
            raw=data,
        )

    if not getattr(const, "ALLOW_DEV_WALLET_SENDS", False):
        raise WalletConfigError(
            "Real Nimiq transaction signing is not connected yet. Set "
            "NIMHUNT_NIMIQ_SEND_COMMAND, or set ALLOW_DEV_WALLET_SENDS=True "
            "only for local tests with fake tx hashes."
        )

    return WalletSendResult(
        tx_hash=_dev_tx_hash(from_address=from_address, to_address=to_address, amount=amount),
        from_address=from_address,
        to_address=to_address,
        amount=amount,
        raw={
            "mode": "development-placeholder",
            "network": getattr(const, "NIMIQ_NETWORK", "TestAlbatross"),
            "fee": int(getattr(const, "NIMIQ_TRANSACTION_FEE", 0)),
            "memo": memo,
        },
    )
