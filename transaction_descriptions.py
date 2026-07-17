"""Build short, consistent descriptions for Nimiq transactions."""

from __future__ import annotations

import constants as const


def _normalise_title(value: object) -> str:
    """Return a one-line Spot title suitable for public transaction data."""
    title = " ".join(str(value or "Spot").split())
    return title or "Spot"


def _truncate_utf8(value: str, *, max_bytes: int) -> str:
    """Truncate text without splitting a UTF-8 character.

    Three ASCII dots make truncation visible and work consistently in wallets
    and explorers. The returned value never exceeds ``max_bytes`` when encoded.
    """
    max_bytes = max(0, int(max_bytes))
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    if max_bytes == 0:
        return ""

    suffix = "..."
    suffix_bytes = suffix.encode("utf-8")
    if max_bytes <= len(suffix_bytes):
        return "." * max_bytes

    prefix_bytes = encoded[: max_bytes - len(suffix_bytes)]
    while prefix_bytes:
        try:
            prefix = prefix_bytes.decode("utf-8").rstrip()
            return f"{prefix}{suffix}"
        except UnicodeDecodeError:
            prefix_bytes = prefix_bytes[:-1]
    return suffix


def build_transaction_description(kind: str, spot_title: object) -> str:
    """Return ``Kind: Spot title`` within NimHunt's on-chain byte budget."""
    clean_kind = " ".join(str(kind or "NimHunt").split()) or "NimHunt"
    description = f"{clean_kind}: {_normalise_title(spot_title)}"
    return _truncate_utf8(
        description,
        max_bytes=int(getattr(const, "NIMIQ_TRANSACTION_DESCRIPTION_MAX_BYTES", 30)),
    )
