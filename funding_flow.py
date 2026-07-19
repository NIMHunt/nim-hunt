"""Install NimHunt's transparent creation-fee funding workflow."""

import sys

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
    install_status()
    install_fee_worker()
    install_monitor()
    _INSTALLED = True


__all__ = ["funding_flow_diagnostics", "install"]
