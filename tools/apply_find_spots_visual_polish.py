from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "public_html.py",
    '_ASSET_VERSION = "map-list-interactions-v1-20260722"',
    '_ASSET_VERSION = "find-spots-visual-polish-v1-20260722"',
)

replace_once(
    "static/find_spots.js",
    "./interface_text.js?v=map-list-interactions-v1-20260722",
    "./interface_text.js?v=find-spots-visual-polish-v1-20260722",
)

replace_once(
    "static/interface_text.js",
    "            listTitle: 'Spots',\n            listTitleWithCount: (n) => `${n} ${n === 1 ? 'Spot' : 'Spots'}`,",
    "            listTitle: 'Spots in Your Area',\n            listTitleWithCount: (n) => `${n} ${n === 1 ? 'Spot' : 'Spots'} in Your Area`,",
)

replace_once(
    "templates/find_spots.html",
    '<h2 id="visible-spots-title" data-i18n="findSpots.visibleSpots">Spots</h2>',
    '<h2 id="visible-spots-title" data-i18n="findSpots.visibleSpots">Spots in Your Area</h2>',
)

replace_once(
    "static/home.css",
    ".spot-centre-marker.is-interactive.leaflet-interactive {\n    cursor: pointer;\n}\n",
    ".spot-centre-marker {\n    filter: drop-shadow(0 3px 4px rgba(31, 35, 72, 0.18));\n}\n\n"
    ".spot-centre-marker.is-muted {\n    filter: drop-shadow(0 2px 3px rgba(31, 35, 72, 0.10));\n}\n\n"
    ".spot-centre-marker.is-interactive.leaflet-interactive {\n    cursor: pointer;\n}\n",
)

replace_once(
    "static/home.css",
    "    font-size: 1rem;\n    font-weight: 900;\n    line-height: 1.15;\n",
    "    font-size: 1.6rem;\n    font-weight: 750;\n    line-height: 1.25;\n",
)

replace_once(
    "static/home.css",
    ".spot-detail-description {\n    margin: 0 0 0.75em;\n    color: var(--nh-muted);\n    font-style: italic;\n    font-weight: 750;\n    text-align: center;\n}\n",
    ".spot-detail-description {\n    margin: 0 0 0.75em;\n    color: var(--nh-muted);\n    font-style: italic;\n    font-weight: 750;\n    text-align: center;\n}\n\n"
    ".find-shell .spot-list-item.is-expanded .spot-list-detail .spot-detail-description {\n    margin-top: -0.35em;\n}\n",
)

path = Path("tests/test_find_spots_map_interactions.py")
test = path.read_text(encoding="utf-8")
test = test.replace(
    "    def test_count_heading_is_plain_spot_count(self):\n"
    "        self.assertIn(\"listTitle: 'Spots'\", self.text)\n"
    "        self.assertIn(\"`${n} ${n === 1 ? 'Spot' : 'Spots'}`\", self.text)\n"
    "        self.assertNotIn(\"Visible Spots (${n})\", self.text)\n"
    "        self.assertIn('>Spots</h2>', self.template)\n",
    "    def test_count_heading_names_the_users_area(self):\n"
    "        self.assertIn(\"listTitle: 'Spots in Your Area'\", self.text)\n"
    "        self.assertIn(\"`${n} ${n === 1 ? 'Spot' : 'Spots'} in Your Area`\", self.text)\n"
    "        self.assertNotIn(\"Visible Spots (${n})\", self.text)\n"
    "        self.assertIn('>Spots in Your Area</h2>', self.template)\n",
    1,
)
test = test.replace(
    "        self.assertIn(\"font-size: 1rem;\", self.css)\n",
    "        self.assertIn(\"font-size: 1.6rem;\", self.css)\n"
    "        self.assertIn(\"font-weight: 750;\", self.css)\n"
    "        self.assertIn(\"line-height: 1.25;\", self.css)\n",
    1,
)
insert_before = "    def test_centre_diameter_is_doubled(self):\n"
addition = (
    "    def test_expanded_find_spot_descriptions_sit_closer_to_meta(self):\n"
    "        self.assertIn(\n"
    "            '.find-shell .spot-list-item.is-expanded .spot-list-detail .spot-detail-description',\n"
    "            self.css,\n"
    "        )\n"
    "        self.assertIn('margin-top: -0.35em;', self.css)\n\n"
    "    def test_centre_markers_use_subtle_button_like_depth(self):\n"
    "        self.assertIn('.spot-centre-marker {', self.css)\n"
    "        self.assertIn('filter: drop-shadow(0 3px 4px rgba(31, 35, 72, 0.18));', self.css)\n\n"
)
if test.count(insert_before) != 1:
    raise SystemExit("tests/test_find_spots_map_interactions.py: centre test anchor not found")
test = test.replace(insert_before, addition + insert_before, 1)
path.write_text(test, encoding="utf-8")
