"""NimHunt FastAPI application setup and background-service lifecycle."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import constants as const
from database import get_db, init_db
from public_html import render_not_found_page
from public_html import router as public_router

try:
    import cache
except Exception:  # cache is optional while bootstrapping
    cache = None  # type: ignore[assignment]

try:
    import settlement_updater
except Exception:  # settlement is optional while bootstrapping
    settlement_updater = None  # type: ignore[assignment]

try:
    import trans_updater
except Exception:  # transaction polling is optional while bootstrapping
    trans_updater = None  # type: ignore[assignment]


def validate_production_safety() -> None:
    """Refuse unsafe test/development settings in explicit production mode."""
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

    hub_url = str(getattr(const, "NIMIQ_HUB_URL", "")).strip().lower()
    if "testnet" in hub_url or "hub.nimiq-testnet" in hub_url:
        unsafe_settings.append("NIMIQ_HUB_URL must not point at testnet")

    fee_address = str(getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", "")).strip().upper()
    if (
        not fee_address
        or "DEV" in fee_address
        or "PLACEHOLDER" in fee_address
        or fee_address.startswith("NQ00 NIMHUNT")
    ):
        unsafe_settings.append("SPOT_CANCELLATION_FEE_ADDRESS must be a production address")

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

    if cache is not None:
        start = getattr(cache, "start_cache_refresher", None)
        refresh = getattr(cache, "refresh_all_caches", None)

        if callable(start):
            await start(run_immediately=True)
        elif callable(refresh):
            async with get_db() as db:
                await refresh(db)

    if settlement_updater is not None:
        start_settlement = getattr(settlement_updater, "start_settlement_refresher", None)
        if callable(start_settlement):
            await start_settlement(run_immediately=True)

    if trans_updater is not None:
        start_transactions = getattr(trans_updater, "start_transaction_refresher", None)
        if callable(start_transactions):
            await start_transactions(run_immediately=True)


async def shutdown() -> None:
    """Stop NimHunt's background services cleanly."""
    if cache is not None:
        stop = getattr(cache, "stop_cache_refresher", None)
        if callable(stop):
            await stop()

    if settlement_updater is not None:
        stop_settlement = getattr(settlement_updater, "stop_settlement_refresher", None)
        if callable(stop_settlement):
            await stop_settlement()

    if trans_updater is not None:
        stop_transactions = getattr(trans_updater, "stop_transaction_refresher", None)
        if callable(stop_transactions):
            await stop_transactions()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Connect FastAPI's lifespan to NimHunt's startup and shutdown routines."""
    await startup()
    try:
        yield
    finally:
        await shutdown()


app = FastAPI(title=const.APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
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
