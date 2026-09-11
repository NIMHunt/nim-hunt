"""Cryptographically verified claim identity primitives.

A device identifier is continuity data, a signature proves control of one key,
and the canonical signer address is the only payout identity for new claims.
Nothing in this module treats a device, IP address, or browser GPS assertion as
proof of a unique person or physical presence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import wallet

_DEVICE_ID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERIFY_HELPER = Path(__file__).resolve().parent / "helpers" / "verify_nimiq_message.mjs"
_NODE_BINARY = os.getenv("NIMHUNT_NIMIQ_NODE_BINARY", "node").strip() or "node"


def clean_device_identifier(value: Any) -> str:
    """Validate continuity data; this does not authenticate a human or wallet."""
    clean = str(value or "").strip().lower()
    if not _DEVICE_ID_RE.fullmatch(clean):
        raise ValueError("A valid Nimiq Pay device identifier is required.")
    return clean


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def canonical_payout_address(value: Any) -> str | None:
    """Return a canonical address, never an assertion that it was verified."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return wallet.normalise_nimiq_address(
            raw,
            field_name="claim payout address",
            allow_dev_placeholder=False,
        )
    except ValueError:
        return None


def verify_wallet_signature_sync(*, message: str, public_key: str, signature: str) -> str:
    """Verify a Nimiq signature and derive its canonical signer address."""
    if not _VERIFY_HELPER.exists():
        raise RuntimeError("Nimiq authentication verifier is missing")
    payload = {
        "message": str(message),
        "public_key": str(public_key),
        "signature": str(signature),
    }
    try:
        completed = subprocess.run(
            [_NODE_BINARY, str(_VERIFY_HELPER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Nimiq authentication verifier is unavailable") from exc

    try:
        result = json.loads((completed.stdout or "{}").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Nimiq authentication verifier returned invalid JSON") from exc
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("ok") is False:
        message_text = "Invalid Nimiq authentication signature"
        if isinstance(result, dict) and result.get("message"):
            message_text = str(result["message"])
        raise ValueError(message_text)
    return wallet.normalise_nimiq_address(
        str(result.get("address") or ""),
        field_name="authenticated wallet address",
        allow_dev_placeholder=False,
    )


async def verify_wallet_signature(*, message: str, public_key: str, signature: str) -> str:
    """Run the bounded subprocess outside SQLite writer transactions."""
    return await asyncio.to_thread(
        verify_wallet_signature_sync,
        message=message,
        public_key=public_key,
        signature=signature,
    )
