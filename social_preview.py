"""Open Graph and X/Twitter metadata for every NimHunt HTML page."""

from __future__ import annotations

import html
import os
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import status

import constants as const
import database as schema
from social_card_images import CARD_VERSION, get_spot_by_ref, public_spot_ref, router

DEFAULT_BASE_URL = "https://nimhunt.app"
MARKER = "<!-- nimhunt-social-preview -->"


@dataclass(frozen=True)
class SocialMetadata:
    title: str
    description: str
    canonical_url: str
    image_url: str
    image_alt: str


def public_base_url() -> str:
    value = os.getenv("NIMHUNT_PUBLIC_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return DEFAULT_BASE_URL
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else DEFAULT_BASE_URL


def public_url(path: str) -> str:
    path = str(path or "/").strip()
    return f"{public_base_url()}{path if path.startswith('/') else '/' + path}"


def image_url(path: str) -> str:
    return f"{public_url(path)}?v={CARD_VERSION}"


def compact(value: object, fallback: str) -> str:
    return " ".join(str(value or "").split()) or fallback


def build_social_tags(meta: SocialMetadata) -> str:
    def escape(value: str) -> str:
        return html.escape(str(value), quote=True)

    title = escape(meta.title)
    description = escape(meta.description)
    canonical = escape(meta.canonical_url)
    image = escape(meta.image_url)
    alt = escape(meta.image_alt)
    return "\n".join(
        (
            MARKER,
            f'<meta name="description" content="{description}">',
            f'<link rel="canonical" href="{canonical}">',
            f'<meta property="og:title" content="{title}">',
            f'<meta property="og:description" content="{description}">',
            '<meta property="og:type" content="website">',
            f'<meta property="og:url" content="{canonical}">',
            f'<meta property="og:image" content="{image}">',
            f'<meta property="og:image:secure_url" content="{image}">',
            '<meta property="og:image:type" content="image/png">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta property="og:image:alt" content="{alt}">',
            f'<meta property="og:site_name" content="{escape(const.APP_NAME)}">',
            '<meta property="og:locale" content="en_GB">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:title" content="{title}">',
            f'<meta name="twitter:description" content="{description}">',
            f'<meta name="twitter:image" content="{image}">',
            f'<meta name="twitter:image:alt" content="{alt}">',
        )
    )


def inject_social_tags(document: bytes, meta: SocialMetadata) -> bytes:
    if MARKER.encode() in document:
        return document
    index = document.lower().find(b"</head>")
    if index < 0:
        return document
    tags = (build_social_tags(meta) + "\n").encode()
    return document[:index] + tags + document[index:]


SITE_COPY = {
    "home": (
        "NimHunt",
        "Create Nimiq-funded geographic Spots, discover nearby rewards, and take part "
        "in Prizedraws through NimPay.",
    ),
    "about": (
        "About NimHunt",
        "Learn what NimHunt is, why it was made, and the ideas behind this Nimiq "
        "community project.",
    ),
    "roadmap": (
        "NimHunt Roadmap",
        "See the features currently planned and in development for NimHunt.",
    ),
    "find-spots": (
        "Find Spots · NimHunt",
        "Explore active and upcoming Nimiq-funded Spots near you.",
    ),
    "my-spots": ("My Spots · NimHunt", "Create, fund and manage your NimHunt Spots."),
    "my-claims": (
        "My Claims · NimHunt",
        "Review the NimHunt Spots and Prizedraws you have claimed or entered.",
    ),
    "create": (
        "Create Spot · NimHunt",
        "Create and fund a geographic NimHunt Spot with Nimiq.",
    ),
    "not-found": ("Page Not Found · NimHunt", "This NimHunt page could not be found."),
}


def site_metadata(key: str, canonical_path: str) -> SocialMetadata:
    title, description = SITE_COPY.get(key, SITE_COPY["home"])
    return SocialMetadata(
        title,
        description,
        public_url(canonical_path),
        image_url(f"/social/site/{key}.png"),
        f"{title} branded NimHunt preview.",
    )


def spot_metadata(spot: dict[str, Any]) -> SocialMetadata:
    title = str(spot.get(schema.SPOT_TITLE) or "NimHunt Spot")
    description = compact(
        spot.get(schema.SPOT_DESC),
        "Discover this Nimiq-funded geographic Spot on NimHunt.",
    )
    ref = public_spot_ref(spot)
    radius = max(1, int(spot.get(schema.SPOT_RADIUS) or 25))
    return SocialMetadata(
        f"NimHunt: {title}",
        description,
        public_url(f"{const.SPOT_PAGE_URL_PREFIX}/{ref}"),
        image_url(f"/social/spot/{ref}.png"),
        f"Map showing {title} and its {radius}-metre claim radius.",
    )


def claim_metadata(claim_id: int) -> SocialMetadata:
    # Claim IDs are sequential, so crawler-visible cards must not disclose Spot data.
    return SocialMetadata(
        "NimHunt Claim",
        "Open this NimHunt claim in NimPay to view its details.",
        public_url(f"{const.CLAIM_PAGE_URL_PREFIX}/{claim_id}"),
        image_url("/social/site/claim.png"),
        "NimHunt Claim branded preview.",
    )


async def metadata_for_request(
    path: str,
    query_string: bytes = b"",
    status_code: int = 200,
) -> SocialMetadata:
    if status_code == status.HTTP_404_NOT_FOUND:
        return site_metadata("not-found", path)
    if path in {"/", "/home"}:
        query = urllib.parse.parse_qs(query_string.decode(errors="ignore"))
        view = str((query.get("view") or [""])[0]).lower()
        if view in {"about", "roadmap"}:
            return site_metadata(view, f"/?view={view}")
        return site_metadata("home", "/")
    routes = {
        "/spots": ("find-spots", "/spots"),
        "/my-spots": ("my-spots", "/my-spots"),
        "/my-claims": ("my-claims", "/my-claims"),
        "/my-history": ("my-claims", "/my-claims"),
    }
    if path in routes:
        return site_metadata(*routes[path])
    if path == "/create" or path.startswith(("/create/", "/create-spot")):
        return site_metadata("create", path)
    spot_prefix = f"{const.SPOT_PAGE_URL_PREFIX}/"
    if path.startswith(spot_prefix):
        ref = urllib.parse.unquote(path[len(spot_prefix) :]).strip("/")
        spot = await get_spot_by_ref(ref)
        if spot is not None:
            return spot_metadata(spot)
    claim_prefix = f"{const.CLAIM_PAGE_URL_PREFIX}/"
    if path.startswith(claim_prefix):
        claim_id = path[len(claim_prefix) :].strip("/")
        if claim_id.isdigit():
            return claim_metadata(int(claim_id))
    return site_metadata("home", path or "/")


class SocialPreviewMiddleware:
    """Inject metadata into every uncompressed server-rendered HTML response."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") not in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        start: dict[str, Any] | None = None
        chunks: list[bytes] = []

        async def capture(message: dict[str, Any]) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, capture)
        if start is None:
            return
        headers = list(start.get("headers", []))
        header_map = {key.lower(): value for key, value in headers}
        body = b"".join(chunks)
        is_html = b"text/html" in header_map.get(b"content-type", b"").lower()
        compressed = bool(header_map.get(b"content-encoding", b""))
        if is_html and not compressed and body:
            meta = await metadata_for_request(
                str(scope.get("path") or "/"),
                scope.get("query_string", b""),
                int(start.get("status", 200)),
            )
            body = inject_social_tags(body, meta)
            headers = [
                (key, value)
                for key, value in headers
                if key.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(body)).encode()))
            start = {**start, "headers": headers}
        await send(start)
        await send({"type": "http.response.body", "body": body, "more_body": False})
