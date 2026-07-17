"""NimHunt FastAPI application setup and background-service lifecycle."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import cache
import constants as const
import settlement_updater
import trans_updater
import wallet
from database import init_db
from public_html import render_not_found_page
from public_html import router as public_router

logger = logging.getLogger(__name__)


def validate_production_safety() -> None:
    """Validate chain settings and refuse development shortcuts in production."""
    network = str(getattr(const, "NIMIQ_NETWORK", "")).strip()
    network_ids = getattr(const, "NIMIQ_NETWORK_IDS", {})
    expected_network_id = network_ids.get(network) if isinstance(network_ids, dict) else None
    configured_network_id = int(getattr(const, "NIMIQ_NETWORK_ID", 0))
    rpc_url = str(getattr(const, "NIMIQ_RPC_URL", "")).strip()

    configuration_errors: list[str] = []
    if expected_network_id is None:
        configuration_errors.append(f"unsupported NIMIQ_NETWORK {network!r}")
    elif configured_network_id != int(expected_network_id):
        configuration_errors.append(
            f"NIMIQ_NETWORK_ID must be {expected_network_id} for {network}"
        )
    if not rpc_url:
        configuration_errors.append("NIMIQ_RPC_URL must be configured")

    if configuration_errors:
        raise RuntimeError(f"Invalid Nimiq configuration: {', '.join(configuration_errors)}")

    if not bool(getattr(const, "PRODUCTION_MODE", False)):
        return

    unsafe_settings: list[str] = []
    for name in (
        "TEST_FEATURES_ENABLED",
        "DEFAULT_TO_TEST_USER",
        "ALLOW_DEV_WALLET_PLACEHOLDERS",
        "ALLOW_DEV_WALLET_SENDS",
    ):
        if bool(getattr(const, name, False)):
            unsafe_settings.append(name)

    dev_seed_env = getattr(const, "NIMHUNT_DEV_MASTER_SEED_ENV", "NIMHUNT_DEV_MASTER_SEED")
    if os.getenv(dev_seed_env):
        unsafe_settings.append(f"{dev_seed_env} must not be set")

    default_mnemonic_env = getattr(
        const,
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV",
        "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC",
    )
    if os.getenv(default_mnemonic_env, "").strip().lower() in {"1", "true", "yes", "on"}:
        unsafe_settings.append(f"{default_mnemonic_env} must not be enabled")

    if str(getattr(const, "NIMIQ_NETWORK", "")).strip() != "MainAlbatross":
        unsafe_settings.append("NIMIQ_NETWORK must be MainAlbatross")

    if int(getattr(const, "NIMIQ_NETWORK_ID", 0)) != 24:
        unsafe_settings.append("NIMIQ_NETWORK_ID must be 24 for MainAlbatross")

    rpc_url = str(getattr(const, "NIMIQ_RPC_URL", "")).strip().lower()
    if not rpc_url or "testnet" in rpc_url:
        unsafe_settings.append("NIMIQ_RPC_URL must point at a mainnet RPC endpoint")

    hub_url = str(getattr(const, "NIMIQ_HUB_URL", "")).strip().lower()
    if not hub_url or "testnet" in hub_url or "hub.nimiq-testnet" in hub_url:
        unsafe_settings.append("NIMIQ_HUB_URL must point at the mainnet Hub")

    for env_constant, fallback in (
        ("NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND_ENV", "NIMHUNT_NIMIQ_DERIVE_ADDRESS_COMMAND"),
        ("NIMHUNT_NIMIQ_SEND_COMMAND_ENV", "NIMHUNT_NIMIQ_SEND_COMMAND"),
    ):
        env_name = getattr(const, env_constant, fallback)
        if not os.getenv(env_name, "").strip():
            unsafe_settings.append(f"{env_name} must be configured")

    fee_address = str(getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")).strip()
    try:
        wallet.normalise_nimiq_address(
            fee_address,
            field_name="SPOT_CANCELLATION_FEE_ADDRESS",
            allow_dev_placeholder=False,
        )
    except ValueError:
        unsafe_settings.append(
            "SPOT_CANCELLATION_FEE_ADDRESS must be a valid production address"
        )

    if unsafe_settings:
        joined = ", ".join(unsafe_settings)
        raise RuntimeError(f"Unsafe production configuration: {joined}")


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
    validate_production_safety()
    await init_db()

    strict_startup = bool(getattr(const, "PRODUCTION_MODE", False))
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


@app.exception_handler(StarletteHTTPException)
async def nimhunt_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Show a branded 404 page while preserving useful API/static errors."""
    if exc.status_code == 404 and _should_render_404_page(request):
        return render_not_found_page(request)

    detail = exc.detail if exc.detail is not None else "Request failed"
    if _request_prefers_json(request):
        return JSONResponse({"detail": detail}, status_code=exc.status_code)

    return PlainTextResponse(str(detail), status_code=exc.status_code)
