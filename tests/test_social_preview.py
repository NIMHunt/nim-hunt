"""Regression tests for NimHunt social metadata and card images."""

from __future__ import annotations

import asyncio
import inspect
import io
from pathlib import Path

from PIL import Image
from starlette.requests import Request

import database as schema
import social_card_images
import social_preview

ROOT = Path(__file__).resolve().parents[1]


def spot_fixture() -> dict[str, object]:
    return {
        schema.SPOT_ID: 42,
        schema.SPOT_LINK: "pretty-place",
        schema.SPOT_TITLE: "Pretty Place",
        schema.SPOT_DESC: "A pleasant place to find some NIM.",
        schema.SPOT_LAT: 51.5074,
        schema.SPOT_LONG: -0.1278,
        schema.SPOT_RADIUS: 250,
        schema.SPOT_CITY: "London",
        schema.SPOT_STATUS: 1,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
        schema.PRIZEDRAW_PRIZE_COUNT: None,
    }


def test_public_url_uses_configurable_origin(monkeypatch) -> None:
    monkeypatch.setenv("NIMHUNT_PUBLIC_BASE_URL", "https://preview.nimhunt.app/")
    assert social_preview.public_url("/spot/example") == (
        "https://preview.nimhunt.app/spot/example"
    )


def test_social_tags_include_open_graph_and_twitter_metadata() -> None:
    meta = social_preview.SocialMetadata(
        title="NimHunt: A & B",
        description='Find "NIM" here.',
        canonical_url="https://nimhunt.app/spot/a-b",
        image_url="https://nimhunt.app/social/spot/a-b.png",
        image_alt="A map preview.",
    )
    tags = social_preview.build_social_tags(meta)
    assert '<meta property="og:title" content="NimHunt: A &amp; B">' in tags
    assert '<meta property="og:type" content="website">' in tags
    assert '<meta name="twitter:card" content="summary_large_image">' in tags
    assert 'content="Find &quot;NIM&quot; here."' in tags
    assert '<link rel="canonical" href="https://nimhunt.app/spot/a-b">' in tags
    assert '<meta property="og:image:width" content="1200">' in tags
    assert '<meta property="og:image:height" content="630">' in tags


def test_spot_metadata_uses_requested_title_description_and_map(monkeypatch) -> None:
    monkeypatch.delenv("NIMHUNT_PUBLIC_BASE_URL", raising=False)
    meta = social_preview.spot_metadata(spot_fixture())
    assert meta.title == "NimHunt: Pretty Place"
    assert meta.description == "A pleasant place to find some NIM."
    assert meta.canonical_url == "https://nimhunt.app/spot/pretty-place"
    assert "/social/spot/pretty-place.png" in meta.image_url
    assert "250-metre claim radius" in meta.image_alt


def test_draft_map_changes_create_a_new_social_card_revision(monkeypatch) -> None:
    monkeypatch.delenv("NIMHUNT_PUBLIC_BASE_URL", raising=False)
    original = spot_fixture()
    moved = {**original, schema.SPOT_LAT: 55.9533, schema.SPOT_LONG: -3.1883}
    resized = {**original, schema.SPOT_RADIUS: 500}

    original_revision = social_card_images.spot_card_revision(original)
    assert social_card_images.spot_card_revision(moved) != original_revision
    assert social_card_images.spot_card_revision(resized) != original_revision
    assert social_card_images.spot_card_cache_key(moved) != (
        social_card_images.spot_card_cache_key(original)
    )
    assert social_preview.spot_metadata(moved).image_url != (
        social_preview.spot_metadata(original).image_url
    )

    draft = {**moved, schema.SPOT_STATUS: 0}
    assert not social_card_images.spot_is_public(draft, now=0)


def test_non_visual_spot_copy_does_not_change_map_card_revision() -> None:
    original = spot_fixture()
    renamed = {**original, schema.SPOT_TITLE: "A Different Title"}
    assert social_card_images.spot_card_revision(renamed) == (
        social_card_images.spot_card_revision(original)
    )


def test_claim_metadata_does_not_leak_spot_details(monkeypatch) -> None:
    monkeypatch.delenv("NIMHUNT_PUBLIC_BASE_URL", raising=False)
    meta = social_preview.claim_metadata(123)
    assert meta.title == "NimHunt Claim"
    assert "Open this NimHunt claim in NimPay" in meta.description
    assert meta.canonical_url == "https://nimhunt.app/claim/123"
    assert "/social/site/claim.png" in meta.image_url
    assert "Pretty Place" not in meta.title


def test_site_card_is_large_png() -> None:
    data = social_card_images.render_site_card("home")
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 630)


def blank_map_tile(_z: int, _x: int, _y: int) -> Image.Image:
    return Image.new("RGB", (256, 256), (235, 241, 244))


def render_test_spot_card(*, is_prizedraw: bool = False) -> Image.Image:
    data = social_card_images.render_spot_card(
        spot_fixture(),
        is_prizedraw=is_prizedraw,
        tile_loader=blank_map_tile,
    )
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGB")


def test_standard_spot_card_uses_only_green_radius_and_marker() -> None:
    image = render_test_spot_card()
    assert image.size == (1200, 630)
    assert image.getpixel((600, 315)) == social_card_images.STANDARD_SPOT_COLOUR
    assert image.getpixel((624, 315)) == (255, 255, 255)
    assert image.getpixel((700, 315)) == (191, 229, 227)
    assert image.getpixel((0, 0)) == (235, 241, 244)


def test_prizedraw_spot_card_uses_nimiq_yellow() -> None:
    image = render_test_spot_card(is_prizedraw=True)
    assert image.getpixel((600, 315)) == social_card_images.PRIZEDRAW_SPOT_COLOUR
    assert image.getpixel((624, 315)) == (255, 255, 255)
    assert image.getpixel((700, 315)) == (239, 231, 202)


def test_spot_card_renderer_contains_no_decorative_clutter() -> None:
    source = inspect.getsource(social_card_images.render_spot_card)
    unwanted_tokens = (
        "rounded_rectangle",
        "diamond(",
        "draw.text",
        "title",
        "city",
        "badge",
    )
    for unwanted in unwanted_tokens:
        assert unwanted not in source
    assert "_draw_osm_attribution(image)" in source


def head_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "HEAD",
            "path": "/social/site/home.png",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("nimhunt.app", 443),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "http_version": "1.1",
        }
    )


def test_social_image_routes_accept_head_requests() -> None:
    data = social_card_images.render_site_card("home")
    response = social_card_images.png_response(head_request(), data)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert int(response.headers["content-length"]) == len(data)
    assert response.body == b""

    methods_by_path = {
        route.path: route.methods
        for route in social_card_images.router.routes
        if hasattr(route, "methods")
    }
    assert {"GET", "HEAD"} <= methods_by_path["/social/site/{key}.png"]
    assert {"GET", "HEAD"} <= methods_by_path["/social/spot/{ref}.png"]


def test_cold_map_render_uses_bounded_parallel_tile_loading() -> None:
    source = inspect.getsource(social_card_images.render_map)
    assert "ThreadPoolExecutor" in source
    assert "NIMHUNT_SOCIAL_TILE_WORKERS" in source


def test_middleware_injects_about_metadata() -> None:
    async def app(_scope, _receive, send) -> None:
        body = b"<html><head><title>NimHunt</title></head><body></body></html>"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    middleware = social_preview.SocialPreviewMiddleware(app)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "query_string": b"view=about",
            },
            receive,
            send,
        )
    )
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message.get("type") == "http.response.body"
    )
    assert b"nimhunt-social-preview" in body
    assert b"About NimHunt" in body
    assert b"summary_large_image" in body


def test_middleware_preserves_zero_copy_static_file_messages() -> None:
    original = [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"image/png")],
        },
        {"type": "http.response.pathsend", "path": "/tmp/example.png"},
    ]

    async def app(_scope, _receive, send) -> None:
        for message in original:
            await send(message)

    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        social_preview.SocialPreviewMiddleware(app)(
            {
                "type": "http",
                "method": "GET",
                "path": "/static/example.png",
                "query_string": b"",
            },
            receive,
            send,
        )
    )
    assert sent == original


def test_osm_policy_safeguards_are_explicit() -> None:
    source = (ROOT / "social_card_images.py").read_text(encoding="utf-8")
    assert "NimHuntSocialCards/1.0 (+https://nimhunt.app)" in source
    assert "7 * 24 * 60 * 60" in source
    assert "© OpenStreetMap contributors" in source
    assert "Cache-Control" in source


def test_application_registers_social_preview_layer() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "import social_preview" in main
    assert "app.add_middleware(social_preview.SocialPreviewMiddleware)" in main
    assert "app.include_router(social_preview.router)" in main
    assert "Pillow==12.3.0" in requirements
