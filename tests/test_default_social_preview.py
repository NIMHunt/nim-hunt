"""Regression tests for the default NimHunt social preview image."""

from pathlib import Path

from PIL import Image

import database as schema
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


def test_default_social_image_is_the_uploaded_png() -> None:
    image_path = ROOT / "static/images/nimhunt-default-social-card.png"
    with Image.open(image_path) as image:
        assert image.format == "PNG"
        assert image.size == (1672, 941)


def test_png_social_tags_use_matching_open_graph_type() -> None:
    meta = social_preview.SocialMetadata(
        title="NimHunt",
        description="A default preview.",
        canonical_url="https://nimhunt.app/",
        image_url="https://nimhunt.app/static/images/nimhunt-default-social-card.png",
        image_alt="NimHunt branded preview.",
    )
    tags = social_preview.build_social_tags(meta)
    assert '<meta property="og:image:type" content="image/png">' in tags


def test_static_pages_share_default_social_image(monkeypatch) -> None:
    monkeypatch.delenv("NIMHUNT_PUBLIC_BASE_URL", raising=False)
    home = social_preview.site_metadata("home", "/")
    find_spots = social_preview.site_metadata("find-spots", "/spots")
    assert "/static/images/nimhunt-default-social-card.png" in home.image_url
    assert find_spots.image_url == home.image_url


def test_bespoke_spot_and_claim_images_are_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("NIMHUNT_PUBLIC_BASE_URL", raising=False)
    assert "/social/spot/pretty-place.png" in social_preview.spot_metadata(
        spot_fixture()
    ).image_url
    assert "/social/site/claim.png" in social_preview.claim_metadata(123).image_url
