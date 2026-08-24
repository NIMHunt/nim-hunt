"""Railway entrypoint with safe Mainnet RPC failover.

Railway imports no NimHunt modules until a working RPC endpoint has been chosen.
That matters because constants.py and trans_updater.py bind the configured RPC
URL during import. Selecting first ensures startup checks, background polling,
and the Node signing/broadcast helper all use the same verified endpoint.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

MAINNET_FALLBACK_RPC_URLS = (
    "https://rpc-mainnet.nimiqscan.com",
    "https://rpc.nimiqwatch.com",
)

_NETWORK_ALIASES = {
    "mainalbatross": "MainAlbatross",
    "mainnet": "MainAlbatross",
    "main": "MainAlbatross",
    "24": "MainAlbatross",
    "testalbatross": "TestAlbatross",
    "testnet": "TestAlbatross",
    "test": "TestAlbatross",
    "5": "TestAlbatross",
    "devalbatross": "DevAlbatross",
    "devnet": "DevAlbatross",
    "dev": "DevAlbatross",
    "6": "DevAlbatross",
}


class RpcUnavailableError(RuntimeError):
    """The endpoint could not provide a usable RPC response."""


class RpcNetworkMismatchError(RuntimeError):
    """The endpoint responded but serves the wrong Nimiq network."""


def _canonical_network(value: object) -> str:
    clean = str(value or "").strip().lower().replace("-", "").replace("_", "")
    return _NETWORK_ALIASES.get(clean, str(value or "").strip())


def _deployment_mode() -> str:
    explicit = os.getenv("NIMHUNT_DEPLOYMENT_MODE", "").strip().lower().replace("_", "-")
    if explicit:
        return explicit
    legacy = os.getenv("NIMHUNT_PRODUCTION", "").strip().lower()
    return "production" if legacy in {"1", "true", "yes", "on"} else "development"


def _safe_endpoint_label(url: str) -> str:
    """Return a log-safe endpoint label without path, query, or credentials."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "invalid RPC endpoint"
    return parsed.hostname or "invalid RPC endpoint"


def _unwrap_result(payload: object) -> object:
    if not isinstance(payload, dict):
        raise RpcUnavailableError("RPC returned non-object JSON")
    if payload.get("error") is not None:
        raise RpcUnavailableError("RPC returned a JSON-RPC error")
    result = payload.get("result")
    if isinstance(result, dict) and "data" in result and "metadata" in result:
        return result.get("data")
    return result


def probe_rpc_network(url: str, *, expected_network: str, timeout_seconds: int) -> None:
    """Prove one endpoint is reachable and serves the expected Nimiq network."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "getLatestBlock",
            "params": [False],
            "id": 1,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RpcUnavailableError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RpcUnavailableError(type(exc).__name__) from None

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RpcUnavailableError("invalid JSON") from None

    block = _unwrap_result(payload)
    if not isinstance(block, dict) or "network" not in block:
        raise RpcUnavailableError("getLatestBlock did not expose a network")

    actual_network = _canonical_network(block.get("network"))
    expected_network = _canonical_network(expected_network)
    if actual_network != expected_network:
        raise RpcNetworkMismatchError(
            f"RPC serves {actual_network or 'an unknown network'}, expected {expected_network}"
        )


def _configured_fallbacks(*, network: str) -> tuple[str, ...]:
    raw = os.getenv("NIMHUNT_NIMIQ_RPC_FALLBACK_URLS", "").strip()
    if raw:
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if _canonical_network(network) == "MainAlbatross":
        return MAINNET_FALLBACK_RPC_URLS
    return ()


def _candidate_urls(primary: str, *, network: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for value in (primary, *_configured_fallbacks(network=network)):
        clean = str(value or "").strip()
        if clean and clean not in candidates:
            candidates.append(clean)
    return tuple(candidates)


def select_rpc_url(
    primary: str,
    *,
    expected_network: str,
    timeout_seconds: int,
    probe=probe_rpc_network,
) -> str:
    """Return the first reachable endpoint that proves the expected network."""
    candidates = _candidate_urls(primary, network=expected_network)
    if not candidates:
        raise RuntimeError("No Nimiq RPC endpoint is configured")

    failures: list[str] = []
    for url in candidates:
        label = _safe_endpoint_label(url)
        try:
            probe(url, expected_network=expected_network, timeout_seconds=timeout_seconds)
        except RpcNetworkMismatchError:
            # A reachable wrong-network endpoint is a configuration/security error,
            # not an availability problem. Never hide it by silently failing over.
            raise
        except RpcUnavailableError as exc:
            failures.append(f"{label}: {exc}")
            continue
        print(f"NimHunt Railway RPC preflight selected {label}", file=sys.stderr, flush=True)
        return url

    raise RuntimeError(
        "No verified Nimiq RPC endpoint was available (" + "; ".join(failures) + ")"
    )


def main() -> None:
    mode = _deployment_mode()
    network = os.getenv("NIMHUNT_NIMIQ_NETWORK", "TestAlbatross").strip() or "TestAlbatross"

    # Only public deployments need the pre-import failover selection. Local
    # development keeps its existing behavior and lets main.py perform normal
    # configuration validation.
    if mode in {"production", "public-testnet"}:
        defaults = {
            "MainAlbatross": "https://rpc.nimiqwatch.com",
            "TestAlbatross": "https://rpc.testnet.nimiqwatch.com/",
        }
        primary = os.getenv("NIMHUNT_NIMIQ_RPC_URL", defaults.get(network, "")).strip()
        timeout_seconds = int(os.getenv("NIMHUNT_NIMIQ_RPC_TIMEOUT_SECONDS", "12"))
        selected = select_rpc_url(
            primary,
            expected_network=network,
            timeout_seconds=timeout_seconds,
        )
        # Set this before importing main.py. Every Python RPC call and the Node
        # transaction helper then inherits the same endpoint for this process.
        os.environ["NIMHUNT_NIMIQ_RPC_URL"] = selected

    argv = [
        "uvicorn",
        "main:app",
        *sys.argv[1:],
    ]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
