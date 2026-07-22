from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "public_html.py",
    '_ASSET_VERSION = "map-list-hover-sync-v1-20260722"',
    '_ASSET_VERSION = "map-list-hover-visual-fix-v1-20260723"',
)

replace_once(
    "static/find_spots.js",
    "    highlight: '#1f2348',",
    "    highlight: '#0582ca',",
)

replace_once(
    "static/spot_map.js",
    "    highlightColour = '#1f2348',",
    "    highlightColour = '#0582ca',",
)

for path in ("static/my_spots.js", "static/my_claims.js"):
    replace_once(
        path,
        "./spot_map.js?v=map-list-hover-sync-v1-20260722",
        "./spot_map.js?v=map-list-hover-visual-fix-v1-20260723",
    )

replace_once(
    "static/home.css",
    "    --nh-success: #21bca5;\n",
    "    --nh-success: #21bca5;\n    --nh-highlight-blue: #0582ca;\n",
)

replace_once(
    "static/home.css",
    "    outline: 2px solid var(--nimiq-blue, #1f2348);",
    "    outline: 2px solid var(--nh-highlight-blue);",
)

replace_once(
    "static/home.css",
    """/* Intentionally compact on every Spot and Claim detail card. */
.spot-detail-description {
    margin: -0.35em 0 0.75em;
    color: var(--nh-muted);
    font-style: italic;
    font-weight: 750;
    text-align: center;
}
""",
    """.spot-detail-description {
    color: var(--nh-muted);
    font-style: italic;
    font-weight: 750;
    text-align: center;
}

/* Intentionally compact on every Spot and Claim detail card. The additional
   selector strength overrides Nimiq Style's generic paragraph margins. */
.nq-style .spot-list-detail > .spot-detail-description {
    margin: -0.35em 0 0.75em;
}
""",
)

replace_once(
    "tests/test_map_list_hover_sync.py",
    '    assert "outline: 2px solid var(--nimiq-blue, #1f2348);" in css\n',
    '    assert "--nh-highlight-blue: #0582ca;" in css\n'
    '    assert "outline: 2px solid var(--nh-highlight-blue);" in css\n',
)
replace_once(
    "tests/test_map_list_hover_sync.py",
    '    assert "/* Intentionally compact on every Spot and Claim detail card. */" in css\n',
    '    assert ".nq-style .spot-list-detail > .spot-detail-description" in css\n'
    '    assert "overrides Nimiq Style\'s generic paragraph margins" in css\n',
)
replace_once(
    "tests/test_map_list_hover_sync.py",
    '    assert \'_ASSET_VERSION = "map-list-hover-sync-v1-20260722"\' in public_html\n',
    '    assert \'_ASSET_VERSION = "map-list-hover-visual-fix-v1-20260723"\' in public_html\n',
)

replace_once(
    "tests/test_find_spots_map_interactions.py",
    '            "/* Intentionally compact on every Spot and Claim detail card. */",\n',
    '            ".nq-style .spot-list-detail > .spot-detail-description",\n',
)

# Explicitly guard against the navy text colour being reused as the hover blue.
path = Path("tests/test_map_list_hover_sync.py")
text = path.read_text(encoding="utf-8")
needle = '    assert "MAP_COLOURS.highlight" in js\n'
replacement = (
    needle
    + '    assert "highlight: \'#0582ca\'" in js\n'
    + '    assert "highlight: \'#1f2348\'" not in js\n'
)
if text.count(needle) != 1:
    raise RuntimeError("Could not add Find Spots blue regression assertion")
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")

path = Path("tests/test_map_list_hover_sync.py")
text = path.read_text(encoding="utf-8")
needle = '    assert "entry.marker?.setStyle" in js\n'
replacement = needle + '    assert "highlightColour = \'#0582ca\'" in js\n'
if text.count(needle) != 1:
    raise RuntimeError("Could not add reusable-map blue regression assertion")
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
