"""Canonical, signed authorization envelope for an initial NimHunt claim.

A valid signature proves that the receiving wallet authorised the reported
location. It does not prove the receiving wallet was physically at that
location. Independent payout-exposure limits remain the financial loss boundary.

E6 coordinates retain roughly 11 cm precision, comfortably finer than browser
GPS, while integer centimetres represent accuracy without float formatting.
"""

from __future__ import annotations

import math
from typing import Any

VERSION = 2
ACTION = "claim"
ABSENT_ACCURACY_CM = -1


def _fixed(value: Any, scale: int, *, name: str, minimum: float, maximum: float) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{name} is outside its valid range")
    # Python round is deterministic ties-to-even. The client submits the server
    # returned integers on completion, so it never has to reproduce this step.
    return int(round(number * scale))


def canonical_location(
    lat: Any, long: Any, accuracy: Any | None
) -> tuple[int, int, int]:
    latitude_e6 = _fixed(lat, 1_000_000, name="latitude", minimum=-90, maximum=90)
    longitude_e6 = _fixed(long, 1_000_000, name="longitude", minimum=-180, maximum=180)
    if accuracy is None:
        accuracy_cm = ABSENT_ACCURACY_CM
    else:
        accuracy_cm = _fixed(
            accuracy, 100, name="accuracy", minimum=0, maximum=10_000_000
        )
    return latitude_e6, longitude_e6, accuracy_cm


def build_message(
    *,
    environment: str,
    network: str,
    spot_id: int,
    receiving_wallet: str,
    device: str,
    latitude_e6: int,
    longitude_e6: int,
    accuracy_cm: int,
    nonce: str,
    issued_at: int,
    expires_at: int,
    action: str = ACTION,
) -> str:
    if action != ACTION:
        raise ValueError("unsupported claim action")
    if int(spot_id) <= 0 or str(int(spot_id)) != str(spot_id):
        raise ValueError("Spot ID must be a canonical positive decimal integer")
    return (
        "NimHunt Claim Authorization\n"
        f"Version: {VERSION}\n"
        f"Environment: {environment}\n"
        f"Network: {network}\n"
        f"Action: {action}\n"
        f"SpotId: {int(spot_id)}\n"
        f"ReceivingWallet: {receiving_wallet}\n"
        f"Device: {device}\n"
        f"LatitudeE6: {int(latitude_e6)}\n"
        f"LongitudeE6: {int(longitude_e6)}\n"
        f"AccuracyCm: {int(accuracy_cm)}\n"
        f"Nonce: {nonce}\n"
        f"IssuedAt: {int(issued_at)}\n"
        f"ExpiresAt: {int(expires_at)}\n"
    )
