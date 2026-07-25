"""NimHunt FastAPI application setup and background-service lifecycle."""

import asyncio
import logging
import os
import urllib.error
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import cache
import constants as const
import database
import settlement_updater
import social_preview
import trans_updater
import wallet
import x_auto_poster
from funding_flow import funding_flow_diagnostics
from funding_flow import install as install_funding_flow
from public_html import render_not_found_page
from public_html import router as public_router

install_funding_flow()

logger = logging.getLogger(__name__)


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _endpoint_host(value: str) -> str:
    try:
        return (urlparse(value).hostname or "").lower()
    except ValueError:
        return ""


def _is_https_endpoint(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.hostname)


def _uses_bundled_helper(command: str) -> bool:
    return "nimiq_helper.mjs" in str(command or "").lower()


def validate_deployment_safety() -> None:
    """Validate deployment safety, chain selection, and signing configuration."""
    mode = str(getattr(const, "DEPLOYMENT_MODE", "development")).strip()
    public_deployment = bool(getattr(const, "PUBLIC_DEPLOYMENT", False))
    network = str(getattr(const, "NIMIQ_NETWORK", "")).strip()
    network_ids = getattr(const, "NIMIQ_NETWORK_IDS", {})
    expected_network_id = network_ids.get(network) if isinstance(network_ids, dict) else None
    configured_network_id = int(getattr(const, "NIMIQ_NETWORK_ID", 0))
    rpc_url = str(getattr(const, "NIMIQ_RPC_URL", "")).strip()
    hub_url = str(getattr(const, "NIMIQ_HUB_URL", "")).strip()

    errors: list[str] = []
    if mode not in {"development", "public-testnet", "production"}:
        errors.append(f"unsupported deployment mode {mode!r}")
    if expected_network_id is None:
        errors.append(f"unsupported NIMIQ_NETWORK {network!r}")
    elif configured_network_id != int(expected_network_id):
        errors.append(f"NIMIQ_NETWORK_ID must be {expected_network_id} for {network}")
    if not rpc_url:
        errors.append("NIMIQ_RPC_URL must be configured")
    if not hub_url:
        errors.append("NIMIQ_HUB_URL must be configured")

    if mode == "public-testnet":
        if network != "TestAlbatross" or configured_network_id != 5:
            errors.append("public-testnet requires TestAlbatross with network ID 5")
    elif mode == "production":
        if network != "MainAlbatross" or configured_network_id != 24:
            errors.append("production requires MainAlbatross with network ID 24")

    if errors:
        raise RuntimeError(f"Invalid NimHunt deployment configuration: {', '.join(errors)}")

    if not public_deployment:
        return

    unsafe: list[str] = []
    for name in (
        "TEST_FEATURES_ENABLED",
        "DEFAULT_TO_TEST_USER",
        "ALLOW_DEV_WALLET_PLACEHOLDERS",
        "ALLOW_DEV_WALLET_SENDS",
    ):
        if bool(getattr(const, name, False)):
            unsafe.append(name)

    dev_seed_env = getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED")
    if os.getenv(dev_seed_env):
        unsafe.append(f"{dev_seed_env} must not be set")

    default_mnemonic_env = getattr(
        const,
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV",
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
    )
    if _env_enabled(default_mnemonic_env):
        unsafe.append(f"{default_mnemonic_env} must not be enabled")

    derive_env = getattr(
        const,
        "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV",
        "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND",
    )
    send_env = getattr(
        const,
        "NIMHUNT_NIMIQ_SEND_COMMAND_ENV",
        "NIMHUNT_NIMIQ_SEND_COMMAND",
    )
    derive_command = os.getenv(derive_env, "").strip()
    send_command = os.getenv(send_env, "").strip()
    if not derive_command:
        unsafe.append(f"{derive_env} must be configured")
    if not send_command:
        unsafe.append(f"{send_env} must be configured")

    mnemonic_env = getattr(
        const, "NIMHUNT_NIMIQ_MNEMONIC_ENV", "NIMHUNT_NIMIQ_MNEMONIC"
    )
    mnemonic = os.getenv(mnemonic_env, "").strip()
    public_default = (
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon about"
    )
    if mnemonic and " ".join(mnemonic.split()).lower() == public_default:
        unsafe.append(f"{mnemonic_env} must not contain the public default test mnemonic")

    external_signer_env = getattr(
        const,
        "NIMHUNT_NIMIQ_EXTERNAL_SIGNER_ENV",
        "NIMHUNT_NIMIQ_EXTERNAL_SIGNER",
    )
    external_signer = _env_enabled(external_signer_env)
    bundled_helper = _uses_bundled_helper(derive_command) or _uses_bundled_helper(
        send_command
    )
    if bundled_helper and not mnemonic:
        unsafe.append(f"{mnemonic_env} must be configured for the bundled Nimiq helper")
    elif not mnemonic and not external_signer:
        unsafe.append(
            f"configure private {mnemonic_env} or enable {external_signer_env} "
            "for a key-managed external signer"
        )

    rpc_host = _endpoint_host(rpc_url)
    hub_host = _endpoint_host(hub_url)
    if not _is_https_endpoint(rpc_url):
        unsafe.append("NIMIQ_RPC_URL must be an HTTPS endpoint")
    if not _is_https_endpoint(hub_url):
        unsafe.append("NIMIQ_HUB_URL must be an HTTPS endpoint")

    if mode == "public-testnet":
        # A private/custom TestAlbatross RPC hostname may not contain the word
        # "testnet", so reject only endpoints that clearly identify mainnet.
        if rpc_host == "rpc.nimiqwatch.com" or "mainnet" in rpc_host:
            unsafe.append("NIMHUNT_NIMIQ_RPC_URL must not point at a mainnet RPC endpoint")
        if "testnet" not in hub_host:
            unsafe.append(
                "NIMHUNT_NIMIQ_HUB_URL must clearly identify the TestAlbatross/testnet Hub"
            )
    elif mode == "production":
        if "testnet" in rpc_host:
            unsafe.append("NIMHUNT_NIMIQ_RPC_URL must point at a mainnet RPC endpoint")
        if "testnet" in hub_host or "nimiq-testnet" in hub_host:
            unsafe.append("NIMHUNT_NIMIQ_HUB_URL must point at the mainnet Hub")

    database_path = Path(str(database.DB_PATH)).expanduser()
    if not database_path.is_absolute():
        unsafe.append("NIMHUNT_DB_PATH must be an absolute persistent path")

    # Current Spot funding targets reserve reward value and platform fees, but
    # not an additional per-transaction miner/network-fee budget for every
    # future payout. Zero-fee Nimiq transactions are the supported public
    # accounting model; reject a non-zero setting rather than underfunding a
    # Spot immediately when its creation fee is sent.
    if int(getattr(const, "NIMIQ_TRANSACTION_FEE", 0)) != 0:
        unsafe.append(
            "NIMIQ_TRANSACTION_FEE must remain 0 in public deployments because "
            "Spot funding targets do not reserve network fees"
        )

    fee_address = str(getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")).strip()
    try:
        normalised_fee_address = wallet.normalise_nimiq_address(
            fee_address,
            field_name="SPOT_CANCELLATION_FEE_ADDRESS",
            allow_dev_placeholder=False,
        )
    except ValueError:
        unsafe.append(
            "SPOT_CANCELLATION_FEE_ADDRESS must be a valid operator-controlled address"
        )
    else:
        dev_fee_address = str(getattr(const, "DEV_PLATFORM_FEE_ADDRESS", "")).strip()
        if dev_fee_address:
            try:
                normalised_dev_fee_address = wallet.normalise_nimiq_address(
                    dev_fee_address,
                    field_name="DEV_PLATFORM_FEE_ADDRESS",
                    allow_dev_placeholder=False,
                )
            except ValueError:
                normalised_dev_fee_address = ""
            if normalised_fee_address == normalised_dev_fee_address:
                unsafe.append(
                    "SPOT_CANCELLATION_FEE_ADDRESS must not use the public development fee address"
                )

    if unsafe:
        raise RuntimeError(f"Unsafe {mode} configuration: {', '.join(unsafe)}")


def validate_production_safety() -> None:
    """Backward-compatible alias for the deployment-aware safety validator."""
    validate_deployment_safety()


def verify_public_signing_access() -> None:
    """Prove both public signer commands can access the same private key."""
    if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        return
    try:
        wallet.validate_public_signer_configuration()
    except Exception:
        raise RuntimeError(
            "Public deployment signer validation failed; check the private mnemonic, "
            "external signer, and both helper commands"
        ) from None


def _canonical_rpc_network_name(value: object) -> str:
    """Normalise network labels returned by Nimiq RPC block responses."""
    clean = str(value or "").strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "testalbatross": "TestAlbatross",
        "testnet": "TestAlbatross",
        "test": "TestAlbatross",
        "5": "TestAlbatross",
        "mainalbatross": "MainAlbatross",
        "mainnet": "MainAlbatross",
        "main": "MainAlbatross",
        "24": "MainAlbatross",
        "devalbatross": "DevAlbatross",
        "devnet": "DevAlbatross",
        "dev": "DevAlbatross",
        "6": "DevAlbatross",
    }
    return aliases.get(clean, str(value or "").strip())


async def _verify_rpc_network_from_latest_block(
    *,
    expected_network: str,
    rpc_url: str,
    timeout_seconds: int,
) -> None:
    """Verify network via getLatestBlock when getNetworkId is unavailable."""
    result = await asyncio.to_thread(
        trans_updater._json_rpc_post_sync,
        rpc_url=rpc_url,
        method="getLatestBlock",
        params=[False],
        timeout_seconds=timeout_seconds,
    )
    block, _metadata = trans_updater._unwrap_rpc_result(result)
    if not isinstance(block, dict) or "network" not in block:
        raise RuntimeError("Nimiq RPC getLatestBlock did not expose a network")
    actual_network = _canonical_rpc_network_name(block.get("network"))
    if actual_network != expected_network:
        raise RuntimeError(
            f"Configured Nimiq RPC serves {actual_network or 'an unknown network'}, "
            f"expected {expected_network}"
        )


async def verify_public_rpc_network() -> None:
    """Prove that the configured public RPC actually serves the selected network."""
    if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        return

    expected_network = str(getattr(const, "NIMIQ_NETWORK", "")).strip()
    expected_network_id = int(getattr(const, "NIMIQ_NETWORK_ID", 0))
    rpc_url = str(getattr(const, "NIMIQ_RPC_URL", "")).strip()
    timeout_seconds = int(getattr(const, "NIMIQ_RPC_TIMEOUT_SECONDS", 12))

    try:
        await trans_updater.verify_configured_rpc_network(
            expected_network_id=expected_network_id,
            rpc_url=rpc_url,
            timeout_seconds=timeout_seconds,
        )
        return
    except urllib.error.HTTPError as exc:
        # Nimiqwatch returns unsupported JSON-RPC methods as HTTP 400 instead
        # of a successful HTTP response containing a JSON-RPC error object.
        # A 400 therefore gets the same safe latest-block fallback; any other
        # HTTP error remains fatal.
        if exc.code != 400:
            raise RuntimeError(
                "Public deployment RPC network validation failed; check the selected "
                "Nimiq network and RPC endpoint"
            ) from None
    except RuntimeError as exc:
        message = str(exc).lower()
        if "method not found" not in message and "-32601" not in message:
            raise RuntimeError(
                "Public deployment RPC network validation failed; check the selected "
                "Nimiq network and RPC endpoint"
            ) from None

    try:
        await _verify_rpc_network_from_latest_block(
            expected_network=expected_network,
            rpc_url=rpc_url,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        raise RuntimeError(
            "Public deployment RPC network validation failed; check the selected "
            "Nimiq network and RPC endpoint"
        ) from None


def _request_prefers_json(request: Request) -> bool:
    """Return True when a request should receive a machine-readable error.

    API requests should stay JSON-like. Static-file requests should stay as real
    errors too, so a missing CSS/JS/image file cannot silently receive HTML.
    Browser page navigations can receive the branded NimHunt error page.
    """
    path = request.url.path
    if path.startswith("/api/"):
        return True

    accept = request.headers.get("accept", "").lower()
    if "application/json" not in accept:
        return False

    # Browser navigations usually include text/html. Treat those as pages even
    # if the broad Accept header also contains JSON-like wildcards.
    return "text/html" not in accept


def _should_render_404_page(request: Request) -> bool:
    """Return True for missing page-style URLs that should show NimHunt 404."""
    if request.method not in {"GET", "HEAD"}:
        return False

    path = request.url.path
    if path == "/" or path.startswith(("/api/", "/static/")):
        return False

    if path in {"/favicon.ico", "/robots.txt"}:
        return False

    return not _request_prefers_json(request)


async def startup() -> None:
    """Initialise storage, caches, settlement work, and transaction polling."""
    validate_deployment_safety()
    verify_public_signing_access()
    await verify_public_rpc_network()
    if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        # Seed the height cache during the same strict startup phase that verifies
        # the RPC network. Deposit dialogs can then use this recent value without
        # making a new public-RPC request on every click.
        await trans_updater.refresh_chain_head_height()
    await database.init_db()

    derive_env = getattr(
        const,
        "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV",
        "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND",
    )
    send_env = getattr(
        const,
        "NIMHUNT_NIMIQ_SEND_COMMAND_ENV",
        "NIMHUNT_NIMIQ_SEND_COMMAND",
    )
    logger.info(
        "NimHunt starting: deployment_mode=%s network=%s network_id=%s database=%s "
        "derive_helper_configured=%s send_helper_configured=%s public_safety=%s",
        getattr(const, "DEPLOYMENT_MODE", "development"),
        getattr(const, "NIMIQ_NETWORK", ""),
        getattr(const, "NIMIQ_NETWORK_ID", 0),
        database.DB_PATH,
        bool(os.getenv(derive_env, "").strip()),
        bool(os.getenv(send_env, "").strip()),
        bool(getattr(const, "PUBLIC_DEPLOYMENT", False)),
    )

    strict_startup = bool(getattr(const, "PUBLIC_DEPLOYMENT", False))
    try:
        await cache.start_cache_refresher(run_immediately=True)
        await settlement_updater.start_settlement_refresher(
            run_immediately=True,
            fail_on_initial_error=strict_startup,
        )
        await trans_updater.start_transaction_refresher(
            run_immediately=True,
            fail_on_initial_error=strict_startup,
        )
        await x_auto_poster.start_x_auto_poster(run_immediately=True)
    except Exception:
        # FastAPI does not enter the lifespan context when startup fails, so
        # stop any service that started before the failing one. Cleanup errors
        # are logged without replacing the more useful startup exception.
        try:
            await shutdown()
        except Exception:  # pragma: no cover - shutdown is already defensive
            logger.exception("Failed to clean up after application startup error")
        raise


async def shutdown() -> None:
    """Stop NimHunt's background services cleanly."""
    services = (
        ("automatic X poster", x_auto_poster.stop_x_auto_poster),
        ("transaction refresher", trans_updater.stop_transaction_refresher),
        ("settlement refresher", settlement_updater.stop_settlement_refresher),
        ("cache refresher", cache.stop_cache_refresher),
    )
    for service_name, stop_service in services:
        try:
            await stop_service()
        except Exception:  # pragma: no cover - best effort during process exit
            logger.exception("Failed to stop %s", service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Connect FastAPI's lifespan to NimHunt's startup and shutdown routines."""
    await startup()
    try:
        yield
    finally:
        await shutdown()


app = FastAPI(title=const.APP_NAME, lifespan=lifespan)
app.add_middleware(social_preview.SocialPreviewMiddleware)
app.mount("/static", StaticFiles(directory=str(const.STATIC_DIR)), name="static")
app.include_router(public_router)
app.include_router(social_preview.router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Return a lightweight, secret-free readiness response."""
    return JSONResponse(
        {
            "ok": True,
            "deployment_mode": getattr(const, "DEPLOYMENT_MODE", "development"),
            "network": getattr(const, "NIMIQ_NETWORK", ""),
            "x_auto_post": x_auto_poster.x_auto_poster_status(),
        }
    )


@app.get("/transaction-healthz", include_in_schema=False)
async def transaction_healthz() -> JSONResponse:
    """Return secret-free transaction-refresher diagnostics."""
    return JSONResponse(
        {
            "ok": True,
            "transactions": await funding_flow_diagnostics(),
        }
    )


@app.exception_handler(StarletteHTTPException)
async def nimhunt_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Show a branded 404 page while preserving useful API/static errors."""
    if exc.status_code == 404 and _should_render_404_page(request):
        return render_not_found_page(request)

    detail = exc.detail if exc.detail is not None else "Request failed"
    if _request_prefers_json(request):
        return JSONResponse({"detail": detail}, status_code=exc.status_code)

    return PlainTextResponse(str(detail), status_code=exc.status_code)
