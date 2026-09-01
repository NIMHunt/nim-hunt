"""Server-rendered, isolated administrator routes for NimHunt."""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import admin_auth
import admin_moderation
import admin_store
import cache
import constants as const
import db_access
from database import get_db

router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory=str(const.TEMPLATES_DIR))

_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5
_LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)

_REASON_LABELS = {
    const.REPORT_REASON_SPAM: "Spam",
    const.REPORT_REASON_INAPPROPRIATE: "Inappropriate content",
    const.REPORT_REASON_FALSE_LOCATION: "False location",
    const.REPORT_REASON_SCAM: "Scam / misleading",
    const.REPORT_REASON_OTHER: "Other",
}
_USER_STATUS_LABELS = {
    const.USER_STATUS_ACTIVE: "Active",
    const.USER_STATUS_LIMITED: "Limited",
    const.USER_STATUS_BANNED: "Banned",
}
_SPOT_STATUS_LABELS = {
    const.SPOT_STATUS_DRAFT: "Draft",
    const.SPOT_STATUS_PUBLISHED: "Published",
    const.SPOT_STATUS_COMPLETED: "Completed",
    const.SPOT_STATUS_CANCELLED: "Cancelled",
    const.SPOT_STATUS_BANNED: "Banned",
}


def _shared_context(request: Request, *, page_title: str) -> dict:
    return {
        "request": request,
        "page_title": page_title,
        "app_name": const.APP_NAME,
        "app_icon_path": const.APP_ICON_PATH,
        "asset_version": "admin-panel-v1-20260818",
        "nimiq_style_cdn": "https://cdn.jsdelivr.net/npm/@nimiq/style@v0.8.5/nimiq-style.min.css",
        "google_font_muli": "https://fonts.googleapis.com/css?family=Muli:400,600,700",
    }


def _protect_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _session_for_request(request: Request) -> admin_auth.AdminSession | None:
    return admin_auth.read_admin_session(
        request.cookies.get(admin_auth.ADMIN_SESSION_COOKIE)
    )


def _require_session(request: Request) -> admin_auth.AdminSession:
    session = _session_for_request(request)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator authentication required.",
        )
    return session


async def _form_data(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Administrator forms require URL-encoded input.",
        )
    raw = await request.body()
    if len(raw) > 16_384:
        raise HTTPException(status_code=413, detail="Administrator form is too large.")
    try:
        parsed = parse_qs(
            raw.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=20,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid administrator form.") from exc
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _client_key(request: Request) -> str:
    return str(request.client.host if request.client else "unknown")


def _login_limited(request: Request) -> bool:
    key = _client_key(request)
    now = time.monotonic()
    attempts = _LOGIN_FAILURES[key]
    while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    return len(attempts) >= _LOGIN_MAX_FAILURES


def _record_login_failure(request: Request) -> None:
    _LOGIN_FAILURES[_client_key(request)].append(time.monotonic())


def _clear_login_failures(request: Request) -> None:
    _LOGIN_FAILURES.pop(_client_key(request), None)


def _redirect_admin() -> RedirectResponse:
    return _protect_response(RedirectResponse(url="/admin", status_code=303))


def _require_csrf(session: admin_auth.AdminSession, form: dict[str, str]) -> None:
    if not admin_auth.verify_csrf(session, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Administrator form expired or was invalid.")


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if _session_for_request(request) is not None:
        return _redirect_admin()
    context = {
        **_shared_context(request, page_title="NimHunt Administration"),
        "configured": admin_auth.admin_password_is_configured(),
        "error": None,
    }
    return _protect_response(
        templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context=context,
        )
    )


@router.post("/login")
async def admin_login(request: Request):
    if _login_limited(request):
        context = {
            **_shared_context(request, page_title="NimHunt Administration"),
            "configured": admin_auth.admin_password_is_configured(),
            "error": "Too many failed attempts. Try again later.",
        }
        return _protect_response(
            templates.TemplateResponse(
                request=request,
                name="admin_login.html",
                context=context,
                status_code=429,
            )
        )

    form = await _form_data(request)
    password = form.get("password", "")
    if not admin_auth.admin_password_is_configured():
        error = "Admin access is disabled until NIMHUNT_ADMIN_PASSWORD_HASH is configured."
        response_status = 503
    elif not admin_auth.verify_admin_password(password):
        _record_login_failure(request)
        error = "Incorrect administrator password."
        response_status = 401
    else:
        _clear_login_failures(request)
        token, _session = admin_auth.create_admin_session()
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            admin_auth.ADMIN_SESSION_COOKIE,
            token,
            max_age=admin_auth.ADMIN_SESSION_SECONDS,
            httponly=True,
            secure=bool(getattr(const, "PUBLIC_DEPLOYMENT", False)),
            samesite="strict",
            path="/admin",
        )
        return _protect_response(response)

    context = {
        **_shared_context(request, page_title="NimHunt Administration"),
        "configured": admin_auth.admin_password_is_configured(),
        "error": error,
    }
    return _protect_response(
        templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context=context,
            status_code=response_status,
        )
    )


@router.post("/logout")
async def admin_logout(request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(admin_auth.ADMIN_SESSION_COOKIE, path="/admin")
    return _protect_response(response)


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    session = _session_for_request(request)
    if session is None:
        return _protect_response(RedirectResponse(url="/admin/login", status_code=303))

    async with get_db() as db:
        metrics = await admin_store.dashboard_metrics(db)
        growth = await admin_store.user_growth(db, days=30)
        leaderboard = await admin_store.spot_creation_leaderboard(db, limit=10)
        reports = await admin_store.pending_reports(db, limit=50)
        audit = await admin_store.recent_audit(db, limit=20)

    for item in reports:
        item["reason_label"] = _REASON_LABELS.get(int(item["reason"]), f"Reason {item['reason']}")
        item["reporter_status_label"] = _USER_STATUS_LABELS.get(
            int(item["reporter_status"]), "Unknown"
        )
        item["owner_status_label"] = _USER_STATUS_LABELS.get(
            int(item["owner_status"]), "Unknown"
        )
        item["spot_status_label"] = _SPOT_STATUS_LABELS.get(
            int(item["spot_status"]), "Unknown"
        )
    for item in leaderboard:
        item["user_status_label"] = _USER_STATUS_LABELS.get(
            int(item["user_status"]), "Unknown"
        )

    context = {
        **_shared_context(request, page_title="NimHunt Administration"),
        "session": session,
        "csrf_token": session.csrf_token,
        "metrics": metrics,
        "growth": growth,
        "leaderboard": leaderboard,
        "reports": reports,
        "audit": audit,
    }
    return _protect_response(
        templates.TemplateResponse(
            request=request,
            name="admin_dashboard.html",
            context=context,
        )
    )


@router.post("/reports/{report_id}/dismiss")
async def dismiss_report(report_id: int, request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)
    note = form.get("note", "").strip()

    async with get_db() as db:
        await admin_store.ensure_admin_tables(db)
        async with db_access.transaction(db, immediate=True):
            await admin_store.set_report_status(
                db,
                report_id=int(report_id),
                status=const.REPORT_STATUS_DISMISSED,
                moderator_note=note or "Dismissed by administrator.",
            )
            await admin_store.record_audit(
                db,
                action="report_dismissed",
                target_type="report",
                target_id=int(report_id),
                detail=note or "Report dismissed.",
            )
    return _redirect_admin()


@router.post("/reports/{report_id}/approve")
async def approve_report(report_id: int, request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)
    note = form.get("note", "").strip()

    async with get_db() as db:
        await admin_store.ensure_admin_tables(db)
        async with db_access.transaction(db, immediate=True):
            await admin_store.set_report_status(
                db,
                report_id=int(report_id),
                status=const.REPORT_STATUS_APPROVED,
                moderator_note=note or "Approved by administrator.",
            )
            await admin_store.record_audit(
                db,
                action="report_approved",
                target_type="report",
                target_id=int(report_id),
                detail=note or "Report approved without an automatic account/Spot action.",
            )
    return _redirect_admin()


async def _change_user_status(*, user_id: int, new_status: int, action: str, detail: str) -> None:
    async with get_db() as db:
        await admin_store.ensure_admin_tables(db)
        async with db_access.transaction(db, immediate=True):
            await admin_store.set_user_status(
                db,
                user_id=int(user_id),
                status=int(new_status),
            )
            await admin_store.record_audit(
                db,
                action=action,
                target_type="user",
                target_id=int(user_id),
                detail=detail,
            )
        await cache.notify_user_changed(db, user_id=int(user_id))


@router.post("/users/{user_id}/limit")
async def limit_user(user_id: int, request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)
    await _change_user_status(
        user_id=int(user_id),
        new_status=const.USER_STATUS_LIMITED,
        action="user_limited",
        detail="Administrator limited this user: claims remain available; Spot creation is blocked.",
    )
    return _redirect_admin()


@router.post("/users/{user_id}/ban")
async def ban_user(user_id: int, request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)
    await _change_user_status(
        user_id=int(user_id),
        new_status=const.USER_STATUS_BANNED,
        action="user_banned",
        detail="Administrator banned this user from Spot creation and claims.",
    )
    return _redirect_admin()


@router.post("/users/{user_id}/activate")
async def activate_user(user_id: int, request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)
    await _change_user_status(
        user_id=int(user_id),
        new_status=const.USER_STATUS_ACTIVE,
        action="user_activated",
        detail="Administrator restored this user to active status.",
    )
    return _redirect_admin()


@router.post("/spots/{spot_id}/ban")
async def ban_spot(spot_id: int, request: Request):
    session = _require_session(request)
    form = await _form_data(request)
    _require_csrf(session, form)

    expected_confirmation = f"BAN {int(spot_id)}"
    supplied_confirmation = form.get("confirmation", "").strip()
    if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
        raise HTTPException(
            status_code=400,
            detail=f"Type {expected_confirmation} exactly to ban this Spot.",
        )

    # This is the only admin action which can trigger a constrained money move,
    # so require the password again even inside a valid admin session.
    if not admin_auth.verify_admin_password(form.get("password", "")):
        raise HTTPException(status_code=403, detail="Administrator password confirmation failed.")

    report_raw = form.get("report_id", "").strip()
    report_id = int(report_raw) if report_raw.isdigit() else None
    reason = form.get("note", "").strip() or "Severe moderation action from administrator panel."

    await admin_moderation.ban_spot(
        spot_id=int(spot_id),
        report_id=report_id,
        reason=reason,
    )
    return _redirect_admin()
