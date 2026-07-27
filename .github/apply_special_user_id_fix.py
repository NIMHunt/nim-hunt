from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "constants.py",
    "SPECIAL_USER_IDS = frozenset({0})",
    "SPECIAL_USER_IDS = frozenset({1})",
)

replace_once(
    "tests/test_special_users.py",
    "def test_special_user_allow_list_is_immutable_and_initially_contains_only_user_zero():\n"
    "    assert const.SPECIAL_USER_IDS == frozenset({0})",
    "def test_special_user_allow_list_is_immutable_and_contains_live_operator_user():\n"
    "    assert const.SPECIAL_USER_IDS == frozenset({1})",
)
replace_once(
    "tests/test_special_users.py",
    "    special = public_html._serialise_spot_for_map(_spot(0), now=1)\n"
    "    ordinary = public_html._serialise_spot_for_map(_spot(1), now=1)",
    "    special = public_html._serialise_spot_for_map(_spot(1), now=1)\n"
    "    ordinary = public_html._serialise_spot_for_map(_spot(0), now=1)",
)
replace_once(
    "tests/test_special_users.py",
    "    special = public_html._serialise_public_spot_for_detail(_spot(0), now=1)\n"
    "    ordinary = public_html._serialise_public_spot_for_detail(_spot(1), now=1)",
    "    special = public_html._serialise_public_spot_for_detail(_spot(1), now=1)\n"
    "    ordinary = public_html._serialise_public_spot_for_detail(_spot(0), now=1)",
)
