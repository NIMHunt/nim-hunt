from pathlib import Path

import constants as const
import database as schema
import public_html


def _spot(created_by: int) -> dict:
    return {
        schema.SPOT_ID: 42,
        schema.SPOT_CREATED_BY: created_by,
        schema.SPOT_TITLE: "Special-user test Spot",
        schema.SPOT_LAT: 51.5,
        schema.SPOT_LONG: -0.1,
    }


def test_special_user_allow_list_is_immutable_and_initially_contains_only_user_zero():
    assert const.SPECIAL_USER_IDS == frozenset({0})


def test_map_spot_serialiser_marks_only_allow_listed_creator():
    special = public_html._serialise_spot_for_map(_spot(0), now=1)
    ordinary = public_html._serialise_spot_for_map(_spot(1), now=1)
    assert special["creator_is_special"] is True
    assert ordinary["creator_is_special"] is False


def test_detail_spot_serialiser_marks_only_allow_listed_creator():
    special = public_html._serialise_public_spot_for_detail(_spot(0), now=1)
    ordinary = public_html._serialise_public_spot_for_detail(_spot(1), now=1)
    assert special["creator_is_special"] is True
    assert ordinary["creator_is_special"] is False


def test_special_user_frontend_uses_shared_purple_hexagon_with_requested_tooltip():
    root = Path(__file__).resolve().parents[1]
    spot_ui = (root / "static" / "spot_ui.js").read_text(encoding="utf-8")
    find_spots = (root / "static" / "find_spots.js").read_text(encoding="utf-8")
    spot_detail = (root / "static" / "spot_detail.js").read_text(encoding="utf-8")
    interface_text = (root / "static" / "interface_text.js").read_text(encoding="utf-8")
    icon_sprite = (root / "static" / "nimiq-style.icons.svg").read_text(encoding="utf-8")

    assert "createUserDisplayName" in spot_ui
    assert "nq-hexagon" in spot_ui
    assert "nq-purple" in spot_ui
    assert "This is a special user" in interface_text
    assert "createUserDisplayName" in find_spots
    assert "createUserDisplayName" in spot_detail
    assert ".special-user-badge" in find_spots
    assert 'id="nq-hexagon"' in icon_sprite
