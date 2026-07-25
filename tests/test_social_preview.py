"""Regression tests for NimHunt social metadata and card images."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image

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


def test_spot_card_avoids_live_network_in_tests() -> None:
    def blank_tile(_z: int, _x: int, _y: int) -> Image.Image:
        return Image.new("RGB", (256, 256), (235, 241, 244))

    data = social_card_images.render_spot_card(
        spot_fixture(),
        tile_loader=blank_tile,
    )
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 630)


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
