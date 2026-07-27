from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_in_section(path: str, start: str, end: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    section = text[start_index:end_index]
    count = section.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one section match in {path}, found {count}: {old[:80]!r}")
    section = section.replace(old, new, 1)
    file_path.write_text(text[:start_index] + section + text[end_index:], encoding="utf-8")


replace_once(
    "constants.py",
    "TEST_USER_ID = 0\n\n# Display-name validation.",
    "TEST_USER_ID = 0\n\n"
    "# Presentation-only allow-list for public display-name highlighting.\n"
    "# Membership grants no permissions and bypasses no account or payment checks.\n"
    "# Keep this immutable until a future administrative mechanism replaces it.\n"
    "SPECIAL_USER_IDS = frozenset({0})\n\n"
    "# Display-name validation.",
)

replace_once(
    "public_html.py",
    '_ASSET_VERSION = "creation-fee-processing-v1-20260727"',
    '_ASSET_VERSION = "special-user-badge-v1-20260727"',
)
replace_once(
    "public_html.py",
    "def _valid_device_id_hash(value: str | None) -> bool:\n"
    "    return bool(value and _DEVICE_ID_RE.fullmatch(value.strip()))\n\n\n"
    "def _public_user(row: dict[str, Any]) -> dict[str, Any]:",
    "def _valid_device_id_hash(value: str | None) -> bool:\n"
    "    return bool(value and _DEVICE_ID_RE.fullmatch(value.strip()))\n\n\n"
    "def _is_special_user_id(value: Any) -> bool:\n"
    "    \"\"\"Return whether a user receives the presentation-only special marker.\"\"\"\n"
    "    try:\n"
    "        return int(value) in const.SPECIAL_USER_IDS\n"
    "    except (TypeError, ValueError):\n"
    "        return False\n\n\n"
    "def _public_user(row: dict[str, Any]) -> dict[str, Any]:",
)
replace_in_section(
    "public_html.py",
    "def _serialise_spot_for_map(",
    "async def _get_public_spot_detail_row(",
    '        "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),\n',
    '        "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),\n'
    '        "creator_is_special": _is_special_user_id(spot.get(schema.SPOT_CREATED_BY)),\n',
)
replace_in_section(
    "public_html.py",
    "def _serialise_public_spot_for_detail(",
    "def _sort_spots_for_map(",
    '        "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),\n',
    '        "created_by": int(spot.get(schema.SPOT_CREATED_BY) or 0),\n'
    '        "creator_is_special": _is_special_user_id(spot.get(schema.SPOT_CREATED_BY)),\n',
)

replace_once(
    "static/interface_text.js",
    "    durationRequiredTooltip: 'This spot requires you to remain within its area for a set duration.',\n",
    "    durationRequiredTooltip: 'This spot requires you to remain within its area for a set duration.',\n"
    "    specialUserTooltip: 'This is a special user',\n",
)

replace_once(
    "static/spot_ui.js",
    "import { getSpotText } from './interface_text.js?v=polish-live-v1-20260720';",
    "import { getSpotText } from './interface_text.js?v=special-user-badge-v1-20260727';",
)
replace_once(
    "static/spot_ui.js",
    "    return svg;\n}\n\nexport function setCopyButtonIcon(button, iconName) {",
    "    return svg;\n}\n\n"
    "export function createUserDisplayName(displayName, { isSpecial = false } = {}) {\n"
    "    const wrap = document.createElement('span');\n"
    "    wrap.className = 'special-user-display-name';\n\n"
    "    const name = document.createElement('span');\n"
    "    name.className = 'special-user-name';\n"
    "    name.textContent = String(displayName || 'unknown creator');\n"
    "    wrap.append(name);\n\n"
    "    if (!isSpecial) return wrap;\n\n"
    "    wrap.classList.add('is-special-user', 'nq-purple');\n"
    "    const badge = document.createElement('span');\n"
    "    const tooltip = SPOT_TEXT.specialUserTooltip || 'This is a special user';\n"
    "    badge.className = 'special-user-badge';\n"
    "    badge.dataset.tooltip = tooltip;\n"
    "    badge.setAttribute('aria-label', tooltip);\n"
    "    badge.tabIndex = 0;\n"
    "    badge.append(createNimiqInlineIcon('nq-hexagon'));\n"
    "    attachRequirementTooltip(badge);\n"
    "    wrap.append(badge);\n"
    "    return wrap;\n"
    "}\n\n"
    "export function setCopyButtonIcon(button, iconName) {",
)

spot_ui_consumers = (
    "static/find_spots.js",
    "static/my_spots.js",
    "static/my_claims.js",
    "static/spot_detail.js",
    "static/claim_detail.js",
)
for path in spot_ui_consumers:
    replace_once(
        path,
        "./spot_ui.js?v=spot-requirements-v1-20260725",
        "./spot_ui.js?v=special-user-badge-v1-20260727",
    )

for path in ("static/find_spots.js", "static/spot_detail.js"):
    replace_once(path, "    appendBulletLine,\n", "    appendBulletLine,\n    createUserDisplayName,\n")
    replace_once(
        path,
        "./interface_text.js?v=single-open-details-v1-20260722",
        "./interface_text.js?v=special-user-badge-v1-20260727",
    )
    replace_once(
        path,
        "    appendBulletLine(lines, `Created by ${creator}`);",
        "    appendBulletLine(\n"
        "        lines,\n"
        "        'Created by ',\n"
        "        createUserDisplayName(creator, { isSpecial: Boolean(spot.creator_is_special) }),\n"
        "    );",
    )

replace_once(
    "static/find_spots.js",
    "return Boolean(target?.closest?.('a, button, input, textarea, select, .spot-copy-button, .spot-report-button'));",
    "return Boolean(target?.closest?.('a, button, input, textarea, select, .spot-copy-button, .spot-report-button, .special-user-badge'));",
)

replace_once(
    "static/home.css",
    ".spot-detail-line strong {\n    font-weight: 900;\n}\n\n",
    ".spot-detail-line strong {\n    font-weight: 900;\n}\n\n"
    ".special-user-display-name {\n"
    "    display: inline-flex;\n"
    "    align-items: center;\n"
    "    gap: 0.24em;\n"
    "    vertical-align: middle;\n"
    "}\n\n"
    ".special-user-display-name.is-special-user {\n"
    "    font-weight: 900;\n"
    "}\n\n"
    ".special-user-badge {\n"
    "    width: 1em;\n"
    "    height: 1em;\n"
    "    display: inline-flex;\n"
    "    align-items: center;\n"
    "    justify-content: center;\n"
    "    flex: 0 0 auto;\n"
    "}\n\n"
    ".special-user-badge .nq-icon {\n"
    "    width: 0.92em;\n"
    "    height: 0.92em;\n"
    "    display: block;\n"
    "    color: currentColor;\n"
    "    fill: currentColor;\n"
    "}\n\n"
    ".special-user-badge:focus-visible {\n"
    "    outline: 2px solid currentColor;\n"
    "    outline-offset: 2px;\n"
    "    border-radius: 3px;\n"
    "}\n\n",
)

for path in ("tests/test_map_list_hover_sync.py", "tests/test_marker_hover_outline.py"):
    replace_once(
        path,
        '_ASSET_VERSION = "creation-fee-processing-v1-20260727"',
        '_ASSET_VERSION = "special-user-badge-v1-20260727"',
    )
replace_once(
    "tests/test_spot_requirement_icons.py",
    'assert "spot_ui.js?v=spot-requirements-v1-20260725" in source',
    'assert "spot_ui.js?v=special-user-badge-v1-20260727" in source',
)

Path("tests/test_special_users.py").write_text(
    '''from pathlib import Path\n\nimport constants as const\nimport database as schema\nimport public_html\n\n\ndef _spot(created_by: int) -> dict:\n    return {\n        schema.SPOT_ID: 42,\n        schema.SPOT_CREATED_BY: created_by,\n        schema.SPOT_TITLE: "Special-user test Spot",\n        schema.SPOT_LAT: 51.5,\n        schema.SPOT_LONG: -0.1,\n    }\n\n\ndef test_special_user_allow_list_is_immutable_and_initially_contains_only_user_zero():\n    assert const.SPECIAL_USER_IDS == frozenset({0})\n\n\ndef test_map_spot_serialiser_marks_only_allow_listed_creator():\n    special = public_html._serialise_spot_for_map(_spot(0), now=1)\n    ordinary = public_html._serialise_spot_for_map(_spot(1), now=1)\n    assert special["creator_is_special"] is True\n    assert ordinary["creator_is_special"] is False\n\n\ndef test_detail_spot_serialiser_marks_only_allow_listed_creator():\n    special = public_html._serialise_public_spot_for_detail(_spot(0), now=1)\n    ordinary = public_html._serialise_public_spot_for_detail(_spot(1), now=1)\n    assert special["creator_is_special"] is True\n    assert ordinary["creator_is_special"] is False\n\n\ndef test_special_user_frontend_uses_shared_purple_hexagon_with_requested_tooltip():\n    root = Path(__file__).resolve().parents[1]\n    spot_ui = (root / "static" / "spot_ui.js").read_text(encoding="utf-8")\n    find_spots = (root / "static" / "find_spots.js").read_text(encoding="utf-8")\n    spot_detail = (root / "static" / "spot_detail.js").read_text(encoding="utf-8")\n    interface_text = (root / "static" / "interface_text.js").read_text(encoding="utf-8")\n    icon_sprite = (root / "static" / "nimiq-style.icons.svg").read_text(encoding="utf-8")\n\n    assert "createUserDisplayName" in spot_ui\n    assert "nq-hexagon" in spot_ui\n    assert "nq-purple" in spot_ui\n    assert "This is a special user" in interface_text\n    assert "createUserDisplayName" in find_spots\n    assert "createUserDisplayName" in spot_detail\n    assert ".special-user-badge" in find_spots\n    assert 'id="nq-hexagon"' in icon_sprite\n''',
    encoding="utf-8",
)
