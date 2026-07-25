"""Generated branded and OpenStreetMap social-card PNGs for NimHunt."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
import textwrap
import time
import urllib.request
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image, ImageDraw, ImageFont

import constants as const
import database as schema
import db_access
from database import get_db

router = APIRouter()

CARD_SIZE = (1200, 630)
MAP_SIZE = (600, 315)
TILE_SIZE = 256
TILE_FALLBACK_TTL = 7 * 24 * 60 * 60
CARD_TTL = 24 * 60 * 60
CARD_VERSION = "social-cards-v1"
TILE_USER_AGENT = "NimHuntSocialCards/1.0 (+https://nimhunt.app)"
MAX_AGE_RE = re.compile(r"(?:^|,)\s*max-age=(\d+)", re.I)

CARD_COPY = {
    "home": ("NimHunt", "Nimiq rewards, placed in the real world."),
    "about": ("About NimHunt", "A community geofaucet and Prizedraw mini-app."),
    "roadmap": ("NimHunt Roadmap", "What is coming next for NimHunt."),
    "find-spots": ("Find Spots", "Discover Nimiq-funded Spots near you."),
    "my-spots": ("My Spots", "Create, fund and manage your Spots."),
    "my-claims": ("My Claims", "Review your claims and Prizedraw entries."),
    "create": ("Create a Spot", "Place a Nimiq-funded reward on the map."),
    "claim": ("NimHunt Claim", "Open in NimPay to view the claim details."),
    "not-found": ("Page Not Found", "This NimHunt page could not be found."),
}


async def get_spot_by_ref(ref: str) -> dict[str, Any] | None:
    async with get_db() as db:
        row = await db_access.get_spot(db, spot_id=int(ref)) if ref.isdigit() else None
        if row is None:
            row = await db_access.get_spot_by_link(db, link=ref)
        if row is None:
            return None
        summary = await db_access.get_spot_owner_summary(db, spot_id=int(row[schema.SPOT_ID]))
        return summary or row


def public_spot_ref(spot: dict[str, Any]) -> str:
    return str(spot.get(schema.SPOT_LINK) or spot[schema.SPOT_ID])


def cache_root(env: str, default: str) -> Path:
    path = Path(os.getenv(env, f"/tmp/{default}")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def tile_paths(z: int, x: int, y: int) -> tuple[Path, Path]:
    root = cache_root("NIMHUNT_SOCIAL_TILE_CACHE_DIR", "nimhunt-social-tiles")
    tile = root / str(z) / str(x) / f"{y}.png"
    tile.parent.mkdir(parents=True, exist_ok=True)
    return tile, tile.with_suffix(".json")


def tile_ttl(headers: Any) -> int:
    match = MAX_AGE_RE.search(str(headers.get("Cache-Control") or ""))
    if match:
        return max(0, int(match.group(1)))
    expires = headers.get("Expires")
    if expires:
        try:
            expires_at = parsedate_to_datetime(str(expires)).timestamp()
            return max(0, int(expires_at - time.time()))
        except (TypeError, ValueError, OverflowError):
            pass
    return TILE_FALLBACK_TTL


def load_tile(z: int, x: int, y: int) -> Image.Image:
    world = 2**z
    x, y = x % world, max(0, min(world - 1, y))
    tile, metadata = tile_paths(z, x, y)
    try:
        expires = json.loads(metadata.read_text(encoding="utf-8"))["expires"]
        if expires > time.time():
            with Image.open(tile) as image:
                return image.convert("RGB")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass

    template = os.getenv("NIMHUNT_SOCIAL_MAP_TILE_URL", const.MAP_TILE_URL).strip()
    user_agent = os.getenv("NIMHUNT_SOCIAL_TILE_USER_AGENT", TILE_USER_AGENT).strip()
    request = urllib.request.Request(
        template.format(z=z, x=x, y=y),
        headers={
            "User-Agent": user_agent or TILE_USER_AGENT,
            "Accept": "image/png,image/*;q=0.8",
        },
    )
    timeout = max(1, int(os.getenv("NIMHUNT_SOCIAL_TILE_TIMEOUT_SECONDS", "8")))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data, ttl = response.read(), tile_ttl(response.headers)
    with Image.open(io.BytesIO(data)) as image:
        result = image.convert("RGB")
    result.save(tile, "PNG", optimize=True)
    metadata.write_text(
        json.dumps({"expires": time.time() + ttl}),
        encoding="utf-8",
    )
    return result


def world_pixel(lat: float, long: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    scale = TILE_SIZE * 2**zoom
    x = (((long + 180) % 360) / 360) * scale
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * scale
    return x, y


def metres_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * max(0.01, math.cos(math.radians(lat))) / 2**zoom


def map_zoom(lat: float, radius: int) -> int:
    target = min(MAP_SIZE) * 0.32
    return next(
        (z for z in range(18, 1, -1) if radius / metres_per_pixel(lat, z) <= target),
        2,
    )


def fallback_tile() -> Image.Image:
    image = Image.new("RGB", (TILE_SIZE, TILE_SIZE), (238, 245, 248))
    draw = ImageDraw.Draw(image)
    draw.line((0, 40, 256, 190), fill=(205, 218, 226), width=6)
    draw.line((40, 0, 190, 256), fill=(215, 226, 232), width=4)
    return image


def render_map(
    lat: float,
    long: float,
    radius: int,
    loader: Callable[[int, int, int], Image.Image] | None = None,
) -> tuple[Image.Image, float]:
    loader = loader or load_tile
    zoom = map_zoom(lat, radius)
    center_x, center_y = world_pixel(lat, long, zoom)
    left = center_x - MAP_SIZE[0] / 2
    top = center_y - MAP_SIZE[1] / 2
    x_start = math.floor(left / TILE_SIZE)
    x_end = math.floor((left + MAP_SIZE[0] - 1) / TILE_SIZE)
    y_start = math.floor(top / TILE_SIZE)
    y_end = math.floor((top + MAP_SIZE[1] - 1) / TILE_SIZE)
    canvas = Image.new("RGB", MAP_SIZE, (238, 245, 248))
    for x in range(x_start, x_end + 1):
        for y in range(y_start, y_end + 1):
            try:
                tile = loader(zoom, x, y).convert("RGB")
            except (OSError, ValueError):
                tile = fallback_tile()
            paste = (round(x * TILE_SIZE - left), round(y * TILE_SIZE - top))
            canvas.paste(tile, paste)
    return canvas, max(4, radius / metres_per_pixel(lat, zoom))


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()


def diamond(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    draw.polygon(
        ((x, y - size), (x + size, y), (x, y + size), (x - size, y)),
        fill=(33, 188, 165),
        outline=(255, 196, 53),
    )


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    start: int,
) -> ImageFont.ImageFont:
    for size in range(start, 29, -2):
        candidate = font(size, True)
        if draw.textbbox((0, 0), text, font=candidate)[2] <= width:
            return candidate
    return font(30, True)


def render_site_card(key: str) -> bytes:
    title, description = CARD_COPY.get(key, CARD_COPY["home"])
    image = Image.new("RGB", CARD_SIZE, (240, 248, 253))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((85, 65, 1115, 565), 54, fill=(255, 255, 255, 238))
    diamond(draw, 600, 205, 76)
    title_font = fit_font(draw, title, 900, 82)
    title_width = draw.textbbox((0, 0), title, font=title_font)[2]
    draw.text(
        ((CARD_SIZE[0] - title_width) / 2, 300),
        title,
        font=title_font,
        fill=(31, 35, 72),
    )
    lines = "\n".join(textwrap.wrap(description, width=62))
    body_font = font(32)
    box = draw.multiline_textbbox((0, 0), lines, font=body_font, align="center")
    draw.multiline_text(
        ((CARD_SIZE[0] - (box[2] - box[0])) / 2, 418),
        lines,
        font=body_font,
        fill=(31, 35, 72, 178),
        align="center",
        spacing=8,
    )
    draw.text((920, 520), "nimhunt.app", font=font(24, True), fill=(31, 35, 72, 150))
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def compact(value: object, fallback: str) -> str:
    return " ".join(str(value or "").split()) or fallback


def render_spot_card(
    spot: dict[str, Any],
    badge: str | None = None,
    tile_loader: Callable[[int, int, int], Image.Image] | None = None,
) -> bytes:
    lat, long = float(spot[schema.SPOT_LAT]), float(spot[schema.SPOT_LONG])
    radius = max(1, int(spot.get(schema.SPOT_RADIUS) or 25))
    title = str(spot.get(schema.SPOT_TITLE) or "NimHunt Spot")
    country = compact(spot.get(schema.SPOT_COUNTRY), "")
    city = compact(spot.get(schema.SPOT_CITY), country)
    map_image, radius_pixels = render_map(lat, long, radius, tile_loader)
    image = map_image.resize(CARD_SIZE, Image.Resampling.LANCZOS).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    cx, cy, rr = 600, 315, radius_pixels * 2
    draw.ellipse(
        (cx - rr, cy - rr, cx + rr, cy + rr),
        fill=(33, 188, 165, 54),
        outline=(33, 188, 165, 235),
        width=7,
    )
    diamond(draw, cx, cy, 24)
    draw.rounded_rectangle((36, 34, 480, 126), 28, fill=(255, 255, 255, 238))
    draw.text((72, 55), "NimHunt", font=font(44, True), fill=(31, 35, 72))
    diamond(draw, 410, 80, 18)
    if badge:
        badge_font = font(27, True)
        badge_width = draw.textbbox((0, 0), badge, font=badge_font)[2]
        left = 1200 - badge_width - 104
        draw.rounded_rectangle((left, 44, 1162, 112), 26, fill=(255, 196, 53, 238))
        draw.text((left + 33, 60), badge, font=badge_font, fill=(31, 35, 72))
    draw.rectangle((0, 475, 1200, 630), fill=(255, 255, 255, 240))
    draw.text(
        (58, 499),
        title,
        font=fit_font(draw, title, 1040, 52),
        fill=(31, 35, 72),
    )
    details = f"{city + ' · ' if city else ''}{radius} m radius"
    draw.text((60, 567), details, font=font(27), fill=(31, 35, 72, 178))
    attribution = "© OpenStreetMap contributors"
    attribution_font = font(20)
    width = draw.textbbox((0, 0), attribution, font=attribution_font)[2]
    draw.text(
        (1176 - width, 443),
        attribution,
        font=attribution_font,
        fill=(31, 35, 72),
        stroke_width=2,
        stroke_fill="white",
    )
    output = io.BytesIO()
    image.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def card_path(key: str) -> Path:
    digest = hashlib.sha256(f"{CARD_VERSION}:{key}".encode()).hexdigest()
    root = cache_root("NIMHUNT_SOCIAL_CARD_CACHE_DIR", "nimhunt-social-cards")
    return root / f"{digest}.png"


def cached_card(key: str, render: Callable[[], bytes]) -> bytes:
    path = card_path(key)
    try:
        if time.time() - path.stat().st_mtime <= CARD_TTL:
            return path.read_bytes()
    except OSError:
        pass
    data = render()
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return data


def png_response(request: Request, data: bytes) -> Response:
    etag = f'"{hashlib.sha256(data).hexdigest()}"'
    headers = {"Cache-Control": f"public, max-age={CARD_TTL}", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="image/png", headers=headers)


def spot_is_public(spot: dict[str, Any], now: int) -> bool:
    if int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_PUBLISHED:
        return False
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        return False
    starts, duration = spot.get(schema.SPOT_STARTS_AT), spot.get(schema.SPOT_ENDS_AT)
    expired = starts is not None and duration is not None
    if expired and int(starts) + int(duration) <= now:
        return False
    return spot.get(schema.SPOT_LAT) is not None and spot.get(schema.SPOT_LONG) is not None


@router.get("/social/site/{key}.png", include_in_schema=False)
async def site_card(request: Request, key: str) -> Response:
    if key not in CARD_COPY:
        raise HTTPException(status_code=404)
    data = await asyncio.to_thread(
        cached_card,
        f"site:{key}",
        lambda: render_site_card(key),
    )
    return png_response(request, data)


@router.get("/social/spot/{ref}.png", include_in_schema=False)
async def spot_card(request: Request, ref: str) -> Response:
    spot = await get_spot_by_ref(ref)
    async with get_db() as db:
        now = await db_access.get_unixepoch(db)
    if spot is None or not spot_is_public(spot, now):
        raise HTTPException(status_code=404)
    ref = public_spot_ref(spot)
    badge = "Prizedraw" if spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None else None
    data = await asyncio.to_thread(
        cached_card,
        f"spot:{ref}",
        lambda: render_spot_card(spot, badge),
    )
    return png_response(request, data)
