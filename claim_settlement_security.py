"""Small settlement adapter for security-held claim payouts.

claim_security.py installs the final transaction boundary and may deliberately
return a successful *deferred* result without broadcasting a Nimiq transaction.
The original standard-settlement helper predates that state and treats any
successful non-duplicate result as `paid=True`.

This adapter preserves the existing settlement workflow while correcting only
that bookkeeping distinction: deferred/skipped/security-held results remain
successful recovery outcomes, but they are not reported as paid until an
actual payout intent is submitted.
"""

from __future__ import annotations

from typing import Any

import settlement_updater

RowDict = dict[str, Any]

_ORIGINAL_PAYOUT_STANDARD_CLAIM = settlement_updater.payout_standard_claim_if_ready
_INSTALLED = False


def normalise_standard_payout_result(result: RowDict) -> RowDict:
    """Ensure a deliberately deferred payout is never labelled as paid."""
    clean = dict(result)
    if clean.get("security_hold") or clean.get("deferred") or clean.get("skipped"):
        clean["paid"] = False
    return clean


async def payout_standard_claim_if_ready_with_security(*, claim_id: int) -> RowDict:
    result = await _ORIGINAL_PAYOUT_STANDARD_CLAIM(claim_id=int(claim_id))
    return normalise_standard_payout_result(result)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    settlement_updater.payout_standard_claim_if_ready = payout_standard_claim_if_ready_with_security
    _INSTALLED = True


__all__ = [
    "install",
    "normalise_standard_payout_result",
    "payout_standard_claim_if_ready_with_security",
]
