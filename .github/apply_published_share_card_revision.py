from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one anchor in {relative}, found {count}: {old[:90]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "social_card_images.py",
    '''def public_spot_ref(spot: dict[str, Any]) -> str:
    return str(spot.get(schema.SPOT_LINK) or spot[schema.SPOT_ID])


''',
    '''def public_spot_ref(spot: dict[str, Any]) -> str:
    return str(spot.get(schema.SPOT_LINK) or spot[schema.SPOT_ID])


def spot_card_revision(spot: dict[str, Any]) -> str:
    """Return a stable revision for every input that changes the map card.

    Drafts are not rendered publicly, but their location and radius can change.
    Deriving both the public image URL and local cache key from these inputs means
    the first card requested after publication always represents the latest draft
    values, without fetching map tiles after every private save.
    """

    def coordinate(value: object) -> float | None:
        if value is None:
            return None
        return round(float(value), 7)

    payload = {
        "lat": coordinate(spot.get(schema.SPOT_LAT)),
        "long": coordinate(spot.get(schema.SPOT_LONG)),
        "radius": max(1, int(spot.get(schema.SPOT_RADIUS) or 25)),
        "is_prizedraw": spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def spot_card_cache_key(spot: dict[str, Any]) -> str:
    """Return the cache key for the Spot's current map-card revision."""
    return f"spot:{public_spot_ref(spot)}:{spot_card_revision(spot)}"


''',
)
replace_once(
    "social_card_images.py",
    '''    ref = public_spot_ref(spot)
    is_prizedraw = spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    data = await asyncio.to_thread(
        cached_card,
        f"spot:{ref}",
        lambda: render_spot_card(spot, is_prizedraw),
    )
''',
    '''    is_prizedraw = spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    data = await asyncio.to_thread(
        cached_card,
        spot_card_cache_key(spot),
        lambda: render_spot_card(spot, is_prizedraw),
    )
''',
)

replace_once(
    "social_preview.py",
    "from social_card_images import CARD_VERSION, get_spot_by_ref, public_spot_ref\n",
    '''from social_card_images import (
    CARD_VERSION,
    get_spot_by_ref,
    public_spot_ref,
    spot_card_revision,
)
''',
)
replace_once(
    "social_preview.py",
    '''def image_url(path: str) -> str:
    return f"{public_url(path)}?v={CARD_VERSION}"
''',
    '''def image_url(path: str, *, revision: str | None = None) -> str:
    version = CARD_VERSION if not revision else f"{CARD_VERSION}-{revision}"
    return f"{public_url(path)}?v={version}"
''',
)
replace_once(
    "social_preview.py",
    '''        image_url(f"/social/spot/{ref}.png"),
''',
    '''        image_url(
            f"/social/spot/{ref}.png",
            revision=spot_card_revision(spot),
        ),
''',
)

replace_once(
    "x_auto_poster.py",
    '''    ref = str(spot.get(schema.SPOT_LINK) or spot[schema.SPOT_ID])
    is_prizedraw = spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    await asyncio.to_thread(
        social_card_images.cached_card,
        f"spot:{ref}",
        lambda: social_card_images.render_spot_card(spot, is_prizedraw),
    )
''',
    '''    is_prizedraw = spot.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    await asyncio.to_thread(
        social_card_images.cached_card,
        social_card_images.spot_card_cache_key(spot),
        lambda: social_card_images.render_spot_card(spot, is_prizedraw),
    )
''',
)

replace_once(
    "static/x_share.js",
    '''function shareUrlForRow(row) {
    if (isIndividualClaimPage()) return canonicalPageUrl();

    const spotHref = row.querySelector('.spot-link-anchor')?.href;
    return cleanAbsoluteUrl(spotHref || canonicalPageUrl());
}
''',
    '''function publishedSpotLinkForRow(row) {
    const spotHref = row.querySelector('.spot-link-anchor')?.href;
    if (!spotHref) return null;

    const url = new URL(spotHref, window.location.origin);
    return url.pathname.startsWith('/spot/') ? url : null;
}

function rowIsShareable(row) {
    return isIndividualClaimPage() || Boolean(publishedSpotLinkForRow(row));
}

function shareUrlForRow(row) {
    if (isIndividualClaimPage()) return canonicalPageUrl();
    return cleanAbsoluteUrl(publishedSpotLinkForRow(row));
}
''',
)
replace_once(
    "static/x_share.js",
    '''    for (const row of rows) {
        if (row.querySelector('.spot-x-share-link')) continue;

        const copyButton = row.querySelector('.spot-copy-button');
''',
    '''    for (const row of rows) {
        if (row.querySelector('.spot-x-share-link')) continue;
        if (!rowIsShareable(row)) continue;

        const copyButton = row.querySelector('.spot-copy-button');
''',
)

for template in (
    "templates/find_spots.html",
    "templates/my_spots.html",
    "templates/my_claims.html",
    "templates/spot.html",
    "templates/claim.html",
):
    path = ROOT / template
    text = path.read_text(encoding="utf-8")
    if "x-share-v3-20260725" not in text:
        raise RuntimeError(f"Expected X share cache key in {template}")
    path.write_text(
        text.replace("x-share-v3-20260725", "x-share-v4-20260726"),
        encoding="utf-8",
    )

replace_once(
    "tests/test_social_preview.py",
    '''def test_claim_metadata_does_not_leak_spot_details(monkeypatch) -> None:
''',
    '''def test_draft_map_changes_create_a_new_social_card_revision(monkeypatch) -> None:
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
''',
)
replace_once(
    "tests/test_x_share.py",
    'X_SHARE_VERSION = "x-share-v3-20260725"',
    'X_SHARE_VERSION = "x-share-v4-20260726"',
)
replace_once(
    "tests/test_x_share.py",
    '''    assert "copyButton.after(createXShareLink(shareUrlForRow(row)))" in source
''',
    '''    assert "if (!rowIsShareable(row)) continue" in source
    assert "url.pathname.startsWith('/spot/')" in source
    assert "return isIndividualClaimPage() || Boolean(publishedSpotLinkForRow(row))" in source
    assert "copyButton.after(createXShareLink(shareUrlForRow(row)))" in source
''',
)
