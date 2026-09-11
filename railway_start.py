"""Railway entrypoint with fail-closed degraded RPC startup.

NimHunt must not confuse a community RPC outage with an application failure.
For public deployments this entrypoint keeps the ordinary deployment, signer,
database, and network-integrity checks intact, but permits Uvicorn to start when
the configured RPC is temporarily unreachable. Chain-dependent work continues
to fail closed and the background workers keep retrying.

A deployment that actually reaches an RPC serving the wrong Nimiq network still
fails immediately. If NimHunt had to start while the RPC was unavailable, the
first later Python RPC use must prove the configured network with getLatestBlock
before any normal RPC call proceeds. Server-originated sends are gated before a
durable send intent is created for the same reason.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import urllib.error
from types import SimpleNamespace
from typing import Any

RAILWAY_HTTP_PROXY_CIDR = "100.0.0.0/8"


def _temporary_rpc_validation_failure(exc: BaseException) -> bool:
    """Return True only for failures that mean the RPC could not be used.

    Wrong-network and unverifiable-network responses are deliberately excluded:
    those are integrity failures, not availability failures, and must still stop
    a public deployment.
    """
    if isinstance(exc, (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError)):
        return True

    message = str(exc)
    if "Configured Nimiq RPC serves " in message:
        return False
    if "did not expose a network" in message:
        return False

    return any(
        marker in message
        for marker in (
            "getLatestBlock returned HTTP ",
            "getLatestBlock failed (",
            "getLatestBlock returned an RPC error",
        )
    )


def _warn_degraded(reason: BaseException) -> None:
    print(
        "WARNING: Nimiq RPC is temporarily unavailable; starting NimHunt in "
        "degraded chain mode. Chain-dependent operations remain fail-closed "
        f"and background workers will retry. Reason: {reason}",
        file=sys.stderr,
        flush=True,
    )


def install_degraded_rpc_startup_policy(app_module: Any) -> SimpleNamespace:
    """Make temporary RPC unavailability non-fatal without weakening chain safety."""
    state = SimpleNamespace(degraded=False, reason=None)

    if not bool(getattr(app_module.const, "PUBLIC_DEPLOYMENT", False)):
        return state

    original_verify = app_module.verify_public_rpc_network
    original_refresh_height = app_module.trans_updater.refresh_chain_head_height
    original_rpc_post = app_module.trans_updater._json_rpc_post_sync
    original_recorded_send = app_module.trans_updater._submit_recorded_chain_send
    original_start_settlement = app_module.settlement_updater.start_settlement_refresher
    original_start_transactions = app_module.trans_updater.start_transaction_refresher

    expected_network = str(getattr(app_module.const, "NIMIQ_NETWORK", "")).strip()
    rpc_url = str(getattr(app_module.const, "NIMIQ_RPC_URL", "")).strip()
    timeout_seconds = int(getattr(app_module.const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12))
    verification_lock = threading.Lock()

    def ensure_recovered_rpc_is_verified() -> None:
        """Prove the configured network before chain work resumes after degraded boot."""
        if not state.degraded:
            return

        with verification_lock:
            if not state.degraded:
                return

            result = original_rpc_post(
                rpc_url=rpc_url,
                method="getLatestBlock",
                params=[False],
                timeout_seconds=timeout_seconds,
            )
            block, _metadata = app_module.trans_updater._unwrap_rpc_result(result)
            if not isinstance(block, dict) or "network" not in block:
                raise RuntimeError(
                    "Recovered Nimiq RPC getLatestBlock did not expose a network"
                )

            actual_network = app_module._canonical_rpc_network_name(block.get("network"))
            if actual_network != expected_network:
                raise RuntimeError(
                    f"Recovered Nimiq RPC serves {actual_network or 'an unknown network'}, "
                    f"expected {expected_network}"
                )

            state.degraded = False
            state.reason = None
            print(
                "Nimiq RPC recovered and was verified; normal chain processing resumed.",
                file=sys.stderr,
                flush=True,
            )

    async def tolerant_verify_public_rpc_network() -> None:
        try:
            await original_verify()
        except RuntimeError as exc:
            if not _temporary_rpc_validation_failure(exc):
                raise
            state.degraded = True
            state.reason = str(exc)
            _warn_degraded(exc)

    async def tolerant_refresh_chain_head_height(*args, **kwargs):
        if state.degraded:
            return None
        try:
            return await original_refresh_height(*args, **kwargs)
        except Exception as exc:
            if not _temporary_rpc_validation_failure(exc):
                raise
            state.degraded = True
            state.reason = str(exc)
            _warn_degraded(exc)
            return None

    def guarded_rpc_post_sync(*, rpc_url: str, method: str, params: list[Any], timeout_seconds: int):
        if state.degraded:
            ensure_recovered_rpc_is_verified()
        return original_rpc_post(
            rpc_url=rpc_url,
            method=method,
            params=params,
            timeout_seconds=timeout_seconds,
        )

    async def guarded_recorded_chain_send(*args, **kwargs):
        # _submit_recorded_chain_send creates the durable uniqueness guard before
        # broadcasting. Verify recovery first so an outage cannot create a local
        # send intent merely because the chain provider is unavailable.
        if state.degraded:
            await asyncio.to_thread(ensure_recovered_rpc_is_verified)
        return await original_recorded_send(*args, **kwargs)

    async def tolerant_start_settlement_refresher(*args, **kwargs):
        if state.degraded:
            kwargs["fail_on_initial_error"] = False
        return await original_start_settlement(*args, **kwargs)

    async def tolerant_start_transaction_refresher(*args, **kwargs):
        if state.degraded:
            kwargs["fail_on_initial_error"] = False
        return await original_start_transactions(*args, **kwargs)

    app_module.verify_public_rpc_network = tolerant_verify_public_rpc_network
    app_module.trans_updater.refresh_chain_head_height = tolerant_refresh_chain_head_height
    app_module.trans_updater._json_rpc_post_sync = guarded_rpc_post_sync
    app_module.trans_updater._submit_recorded_chain_send = guarded_recorded_chain_send
    app_module.settlement_updater.start_settlement_refresher = tolerant_start_settlement_refresher
    app_module.trans_updater.start_transaction_refresher = tolerant_start_transaction_refresher

    return state


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start NimHunt on Railway")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--proxy-headers", action="store_true")
    parser.add_argument("--forwarded-allow-ips", default=RAILWAY_HTTP_PROXY_CIDR)
    return parser.parse_args(argv)


def main(*, app_module: Any | None = None, uvicorn_module: Any | None = None) -> None:
    args = _parse_args(sys.argv[1:])
    if args.workers != 1:
        raise RuntimeError("NimHunt Railway startup requires exactly one Uvicorn worker")

    if app_module is None:
        import main as app_module  # Imported here so tests can inject a fake app module.
    if uvicorn_module is None:
        import uvicorn as uvicorn_module

    install_degraded_rpc_startup_policy(app_module)

    # Run the already-imported app in this process so the guarded RPC policy
    # remains installed. Passing a module string would allow a worker process to
    # import an unpatched copy of main.py.
    uvicorn_module.run(
        app_module.app,
        host=args.host,
        port=args.port,
        workers=1,
        proxy_headers=bool(args.proxy_headers),
        forwarded_allow_ips=args.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
