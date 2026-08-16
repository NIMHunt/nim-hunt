"""Install NimHunt's transaction, funding and claim-safety runtime hooks."""

import sys

import constants as const
from cancellation_safety import install as install_cancellation_safety
from claim_auth_abuse_guard import install as install_claim_auth_abuse_guard
from claim_code_policy import install as install_claim_code_policy
from claim_location_guard import install as install_claim_location_guard
from claim_network_security import install as install_claim_network_security
from claim_payout_throttle import install as install_claim_payout_throttle
from claim_security import install as install_claim_security
from claim_security_defence_in_depth import (
    install as install_claim_security_defence_in_depth,
)
from claim_security_maintenance import install as install_claim_security_maintenance
from claim_security_response_delivery import (
    install as install_claim_security_response_delivery,
)
from claim_settlement_security import install as install_claim_settlement_security
from claim_wallet_hourly_limit import install as install_claim_wallet_hourly_limit
from funding_fee_worker import install as install_fee_worker
from funding_monitor import funding_flow_diagnostics
from funding_monitor import install as install_monitor
from funding_status import install as install_status
from refund_address_safety import install as install_refund_address_safety

_INSTALLED = False


def install() -> None:
    """Install production runtime hooks without polluting isolated unit tests."""
    global _INSTALLED

    # The old development shortcut silently turned a browser with no Nimiq Pay
    # identity into TEST_USER_ID. That no longer represents the claim security
    # model: public Testnet requires an actual device plus wallet signature, and
    # a desktop fallback can create misleading local claims without a genuine
    # payout identity. Keep TEST_USER_ID as a spoof.py fixture owner, but never
    # make it the implicit current user of the running application.
    const.DEFAULT_TO_TEST_USER = False

    if _INSTALLED or "pytest" in sys.modules:
        return
    install_claim_code_policy()
    install_claim_location_guard()
    install_claim_security()
    install_claim_network_security()
    install_claim_auth_abuse_guard()
    install_claim_security_defence_in_depth()
    # The authoritative hourly wallet limit is derived from durable claim rows
    # and rechecked inside the serialized claim transaction. Install it after
    # defence-in-depth so it preserves that wrapper's wallet/payout protections.
    install_claim_wallet_hourly_limit()
    # Keep the primary security guard chain authoritative while restoring
    # FastAPI's intended response-before-BackgroundTasks ordering.
    install_claim_security_response_delivery()
    # The payout throttle must wrap the security-aware submitter rather than
    # replace it, so install it after claim_security and its extra safeguards.
    install_claim_payout_throttle()
    install_claim_settlement_security()
    install_claim_security_maintenance()
    # Preserve Nimiq Pay's ordinary account before cancellation/remainder guards
    # can submit money. The cancellation lease then wraps the corrected flow.
    install_refund_address_safety()
    install_cancellation_safety()
    install_status()
    install_fee_worker()
    install_monitor()
    _INSTALLED = True


__all__ = ["funding_flow_diagnostics", "install"]
