"""Install NimHunt's transaction, funding and claim-safety runtime hooks."""

import sys

from cancellation_safety import install as install_cancellation_safety
from claim_code_policy import install as install_claim_code_policy
from claim_location_guard import install as install_claim_location_guard
from funding_fee_worker import install as install_fee_worker
from funding_monitor import funding_flow_diagnostics
from funding_monitor import install as install_monitor
from funding_status import install as install_status

_INSTALLED = False


def install() -> None:
    """Install production runtime hooks without polluting isolated unit tests."""
    global _INSTALLED
    if _INSTALLED or "pytest" in sys.modules:
        return
    install_claim_code_policy()
    install_claim_location_guard()
    install_cancellation_safety()
    install_status()
    install_fee_worker()
    install_monitor()
    _INSTALLED = True


__all__ = ["funding_flow_diagnostics", "install"]
