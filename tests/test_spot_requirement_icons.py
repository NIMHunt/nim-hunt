"""Regression coverage for Spot password/duration icons and Prizedraw colours."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_shared_title_helper_renders_password_and_duration_requirements() -> None:
    spot_ui = read("static/spot_ui.js")
    interface_text = read("static/interface_text.js")

    assert "iconName: 'nq-lock-locked'" in spot_ui
    assert "iconName: 'nq-stopwatch'" in spot_ui
    assert "Number(spot?.claim_duration || 0) > 0" in spot_ui
    assert "appendSpotRequirementIcons(titleEl, spot);" in spot_ui
    assert "durationRequiredTooltip" in interface_text
    assert "remain within its area for a set duration" in interface_text


def test_both_icons_can_be_appended_to_the_same_title() -> None:
    spot_ui = read("static/spot_ui.js")

    assert "if (spot?.use_password)" in spot_ui
    assert "if (Number(spot?.claim_duration || 0) > 0)" in spot_ui
    assert "for (const requirement of requirements)" in spot_ui
    assert "container.append(createRequirementIcon(" in spot_ui


def test_requirement_icons_are_used_in_lists_detail_pages_and_map_popups() -> None:
    find_spots = read("static/find_spots.js")
    my_spots = read("static/my_spots.js")
    my_claims = read("static/my_claims.js")
    spot_detail = read("static/spot_detail.js")
    claim_detail = read("static/claim_detail.js")

    assert "appendSpotTitleWithLock(title, spot, { truncate: true });" in find_spots
    assert "appendSpotRequirementIcons(requirements, spot, { interactive: false });" in find_spots
    assert "appendSpotRequirementIcons(title, spot, { interactive: false });" in my_spots
    assert "appendSpotRequirementIcons(wrap, item, { interactive: false });" in my_claims
    assert "appendSpotTitleWithLock(title, spot);" in spot_detail
    assert "appendSpotRequirementIcons(title, spot, { interactive: false });" in spot_detail
    assert "appendSpotTitleWithLock(title, spot);" in claim_detail


def test_requirement_icon_css_is_generic_and_supports_pairs() -> None:
    stylesheet = read("static/home.css")

    assert ".spot-title-requirement-icon-wrap" in stylesheet
    assert ".map-spot-title-tooltip-requirements" in stylesheet
    assert "gap: 0.14em;" in stylesheet
    assert ".spot-title-lock-icon-wrap" not in stylesheet


def test_in_range_prizedraw_list_and_map_colours_are_both_yellow() -> None:
    find_spots = read("static/find_spots.js")
    stylesheet = read("static/home.css")
    public_html = read("public_html.py")

    # The backend deliberately chooses Prizedraw before checking password/duration,
    # so a coded or duration Prizedraw remains yellow rather than conditional blue.
    kind_function = public_html.split("def _claim_kind_for_spot", 1)[1].split(
        "@router.post(\"/api/spots/claim-status\")",
        1,
    )[0]
    assert kind_function.index("if _spot_has_prizedraw(spot):") < kind_function.index(
        "SPOT_CLAIM_DURATION"
    )
    assert 'return "prizedraw"' in kind_function

    assert "item.classList.add('is-claim-nearby', `is-claim-${claimKind}`);" in find_spots
    assert "return spot.is_prizedraw ? MAP_COLOURS.prizedraw : MAP_COLOURS.standard;" in find_spots
    assert "prizedraw: '#ffc435'" in find_spots

    prizedraw_rule = stylesheet.split(".spot-list-item.is-claim-prizedraw {", 1)[1].split(
        "}",
        1,
    )[0]
    assert "background: #ffc435;" in prizedraw_rule
