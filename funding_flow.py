"""Install NimHunt's transparent creation-fee funding workflow."""

from funding_fee_worker import install as install_fee_worker
from funding_monitor import funding_flow_diagnostics
from funding_monitor import install as install_monitor
from funding_status import install as install_status

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    install_status()
    install_fee_worker()
    install_monitor()
    _INSTALLED = True


__all__ = ["funding_flow_diagnostics", "install"]
