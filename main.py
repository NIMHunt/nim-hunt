"""
main.py

Minimal FastAPI entrypoint for trying the NimHunt home page.
If your project already has a main.py, copy the relevant parts instead of
using this file wholesale.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import init_db, get_db
from public_html import render_not_found_page, router as public_router
import constants as const

try:
    import cache
except Exception:  # cache is optional while bootstrapping
    cache = None  # type: ignore[assignment]

try:
    import settlement_updater
except Exception:  # settlement is optional while bootstrapping
    settlement_updater = None  # type: ignore[assignment]


app = FastAPI(title=const.APP_NAME)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(public_router)


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
    if path == "/" or path.startswith("/api/") or path.startswith("/static/"):
        return False

    if path in {"/favicon.ico", "/robots.txt"}:
        return False

    return not _request_prefers_json(request)


@app.exception_handler(StarletteHTTPException)
async def nimhunt_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Show a branded 404 page while preserving useful API/static errors."""
    if exc.status_code == 404 and _should_render_404_page(request):
        return render_not_found_page(request)

    detail = exc.detail if exc.detail is not None else "Request failed"
    if _request_prefers_json(request):
        return JSONResponse({"detail": detail}, status_code=exc.status_code)

    return PlainTextResponse(str(detail), status_code=exc.status_code)


@app.on_event("startup")
async def startup() -> None:
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


@app.on_event("shutdown")
async def shutdown() -> None:
    if cache is not None:
        stop = getattr(cache, "stop_cache_refresher", None)
        if callable(stop):
            await stop()

    if settlement_updater is not None:
        stop_settlement = getattr(settlement_updater, "stop_settlement_refresher", None)
        if callable(stop_settlement):
            await stop_settlement()
