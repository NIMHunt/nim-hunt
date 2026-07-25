"""Apply the simplified Spot social-card renderer, then remove this helper."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD_PATH = ROOT / "social_card_images.py"
TEST_PATH = ROOT / "tests" / "test_social_preview.py"

source = CARD_PATH.read_text(encoding="utf-8")
old_version = 'CARD_VERSION = "social-cards-v1"\n'
new_version = '''CARD_VERSION = "social-cards-v2"
STANDARD_SPOT_COLOUR = (33, 188, 165)
PRIZEDRAW_SPOT_COLOUR = (255, 196, 53)
RADIUS_FILL_ALPHA = round(255 * 0.22)
RADIUS_STROKE_ALPHA = round(255 * 0.95)
'''
if old_version not in source:
    raise RuntimeError("Social-card version anchor is missing")
source = source.replace(old_version, new_version, 1)

spot_renderer = '''def _draw_osm_attribution(image: Image.Image) -> None:
    """Keep the map provider credit visible without adding decorative UI."""
    draw = ImageDraw.Draw(image, "RGBA")
    attribution = "© OpenStreetMap contributors"
    attribution_font = font(20)
    width = draw.textbbox((0, 0), attribution, font=attribution_font)[2]
    draw.text(
        (1176 - width, 596),
        attribution,
        font=attribution_font,
        fill=(31, 35, 72, 220),
        stroke_width=2,
        stroke_fill=(255, 255, 255, 235),
    )


def render_spot_card(
    spot: dict[str, Any],
    is_prizedraw: bool = False,
    tile_loader: Callable[[int, int, int], Image.Image] | None = None,
) -> bytes:
    lat = float(spot[schema.SPOT_LAT])
    long = float(spot[schema.SPOT_LONG])
    radius = max(1, int(spot.get(schema.SPOT_RADIUS) or 25))
    map_image, radius_pixels = render_map(lat, long, radius, tile_loader)
    image = map_image.resize(CARD_SIZE, Image.Resampling.LANCZOS).convert("RGBA")

    colour = PRIZEDRAW_SPOT_COLOUR if is_prizedraw else STANDARD_SPOT_COLOUR
    centre_x, centre_y = CARD_SIZE[0] // 2, CARD_SIZE[1] // 2
    radius_pixels *= CARD_SIZE[0] / MAP_SIZE[0]

    radius_overlay = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    radius_draw = ImageDraw.Draw(radius_overlay, "RGBA")
    radius_draw.ellipse(
        (
            centre_x - radius_pixels,
            centre_y - radius_pixels,
            centre_x + radius_pixels,
            centre_y + radius_pixels,
        ),
        fill=(*colour, RADIUS_FILL_ALPHA),
        outline=(*colour, RADIUS_STROKE_ALPHA),
        width=5,
    )
    image = Image.alpha_composite(image, radius_overlay)

    marker_radius = 24
    marker_draw = ImageDraw.Draw(image, "RGBA")
    marker_draw.ellipse(
        (
            centre_x - marker_radius,
            centre_y - marker_radius,
            centre_x + marker_radius,
            centre_y + marker_radius,
        ),
        fill=(*colour, 255),
        outline=(255, 255, 255, 255),
        width=4,
    )

    _draw_osm_attribution(image)
    output = io.BytesIO()
    image.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()
'''
pattern = re.compile(r"def compact\(.*?\n\ndef card_path", re.DOTALL)
source, count = pattern.subn(spot_renderer + "\n\ndef card_path", source, count=1)
if count != 1:
    raise RuntimeError("Existing Spot-card renderer block was not found")

old_route = '    badge = "Prizedraw" if spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None else None\n'
new_route = '    is_prizedraw = spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None\n'
if old_route not in source:
    raise RuntimeError("Prizedraw route anchor is missing")
source = source.replace(old_route, new_route, 1)
old_call = "        lambda: render_spot_card(spot, badge),\n"
new_call = "        lambda: render_spot_card(spot, is_prizedraw),\n"
if old_call not in source:
    raise RuntimeError("Spot-card render call anchor is missing")
source = source.replace(old_call, new_call, 1)
CARD_PATH.write_text(source, encoding="utf-8")

tests = TEST_PATH.read_text(encoding="utf-8")
if "import inspect\n" not in tests:
    tests = tests.replace("import io\n", "import io\nimport inspect\n", 1)

replacement_tests = '''def blank_map_tile(_z: int, _x: int, _y: int) -> Image.Image:
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
    for unwanted in ("rounded_rectangle", "diamond(", "draw.text", "title", "city", "badge"):
        assert unwanted not in source
    assert "_draw_osm_attribution(image)" in source
'''
pattern = re.compile(
    r"def test_spot_card_avoids_live_network_in_tests\(\) -> None:.*?"
    r"(?=def test_middleware_injects_about_metadata)",
    re.DOTALL,
)
tests, count = pattern.subn(replacement_tests + "\n\n", tests, count=1)
if count != 1:
    raise RuntimeError("Existing Spot-card regression test was not found")
TEST_PATH.write_text(tests, encoding="utf-8")

for relative in (
    ".github/workflows/apply-simple-spot-card.yml",
    "scripts/apply_simple_spot_card_patch.py",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
