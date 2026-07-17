"""NimHunt FastAPI application setup and background-service lifecycle."""

import logging
import os
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
import trans_updater
import wallet
from public_html import render_not_found_page
from public_html import router as public_router

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
            unsafe.append("NIMIQ_RPC_URL must not point at a mainnet RPC endpoint")
        if "testnet" not in hub_host:
            unsafe.append(
                "NIMIQ_HUB_URL must clearly identify the TestAlbatross/testnet Hub"
            )
    elif mode == "production":
        if "testnet" in rpc_host:
            unsafe.append("NIMIQ_RPC_URL must point at a mainnet RPC endpoint")
        if "testnet" in hub_host or "nimiq-testnet" in hub_host:
            unsafe.append("NIMIQ_HUB_URL must point at the mainnet Hub")

    database_path = Path(str(database.DB_PATH)).expanduser()
    if not database_path.is_absolute():
        unsafe.append("NIMHUNT_DB_PATH must be an absolute persistent path")

    fee_address = str(getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")).strip()
    try:
        wallet.normalise_nimiq_address(
            fee_address,
            field_name="SPOT_CANCELLATION_FEE_ADDRESS",
            allow_dev_placeholder=False,
        )
    except ValueError:
        unsafe.append(
            "SPOT_CANCELLATION_FEE_ADDRESS must be a valid operator-controlled address"
        )

    if unsafe:
        raise RuntimeError(f"Unsafe {mode} configuration: {', '.join(unsafe)}")


def validate_production_safety() -> None:
    """Backward-compatible alias for the deployment-aware safety validator."""
    validate_deployment_safety()


def verify_public_signing_access() -> None:
    """Prove that a public deployment can derive from its configured private key."""
    if not bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        return
    try:
        wallet.derive_spot_deposit_address(0)
    except Exception:
        raise RuntimeError(
            "Public deployment signer validation failed; check the private mnemonic "
            "or external signer configuration"
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
app.mount("/static", StaticFiles(directory=str(const.STATIC_DIR)), name="static")
app.include_router(public_router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Return a lightweight, secret-free readiness response."""
    return JSONResponse(
        {
            "ok": True,
            "deployment_mode": getattr(const, "DEPLOYMENT_MODE", "development"),
            "network": getattr(const, "NIMIQ_NETWORK", ""),
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
