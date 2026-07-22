from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:140]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


VERSION = "single-open-details-v1-20260722"

replace_once(
    "public_html.py",
    '_ASSET_VERSION = "find-spots-visual-polish-v1-20260722"',
    f'_ASSET_VERSION = "{VERSION}"',
)

# Find Spots: cache keys, split reward information, revised duration wording,
# and a single-open accordion used by both direct and map-triggered expansion.
replace_once(
    "static/find_spots.js",
    "from './interface_text.js?v=find-spots-visual-polish-v1-20260722';",
    f"from './interface_text.js?v={VERSION}';",
)
replace_once(
    "static/find_spots.js",
    "} from './spot_ui.js?v=lock-prefix-v1-20260722';",
    f"}} from './spot_ui.js?v={VERSION}';",
)
replace_once(
    "static/find_spots.js",
    "function buildSpotDetail(spot) {\n",
    "function buildRewardAmountLine(amountText) {\n"
    "    const fragment = document.createDocumentFragment();\n"
    "    const amount = document.createElement('strong');\n"
    "    amount.textContent = amountText;\n"
    "    fragment.append(amount, document.createTextNode(' Per Claim'));\n"
    "    return fragment;\n"
    "}\n\n"
    "function buildSpotDetail(spot) {\n",
)
replace_once(
    "static/find_spots.js",
    "    const claimWord = availableClaims === 1 ? 'claim' : 'claims';\n",
    "",
)
replace_once(
    "static/find_spots.js",
    "    appendBulletLine(lines, `${nimPerClaimText(spot)} Per Claim (${availableClaims} ${claimWord} available)`);\n",
    "    appendBulletLine(lines, buildRewardAmountLine(nimPerClaimText(spot)));\n"
    "    appendBulletLine(lines, `${availableClaims} ${availableClaims === 1 ? 'Claim' : 'Claims'} Remaining`);\n",
)
replace_once(
    "static/find_spots.js",
    "        appendBulletLine(lines, `Requires a claim duration of ${duration}`);",
    "        appendBulletLine(lines, `Must remain on Spot for ${duration}`);",
)
replace_once(
    "static/find_spots.js",
    "function setListItemExpanded(item, summary, detail, spotId, expanded) {\n"
    "    item.classList.toggle('is-expanded', expanded);",
    "function collapseOtherSpotEntries(activeSpotId) {\n"
    "    const activeId = Number(activeSpotId);\n"
    "    for (const expandedId of [...state.expandedSpotIds]) {\n"
    "        if (Number(expandedId) !== activeId) state.expandedSpotIds.delete(expandedId);\n"
    "    }\n\n"
    "    for (const [otherSpotId, entry] of state.listEntriesBySpotId.entries()) {\n"
    "        if (Number(otherSpotId) === activeId) continue;\n"
    "        if (entry.summary.getAttribute('aria-expanded') !== 'true') continue;\n"
    "        setListItemExpanded(entry.item, entry.summary, entry.detail, Number(otherSpotId), false);\n"
    "    }\n"
    "}\n\n"
    "function setListItemExpanded(item, summary, detail, spotId, expanded) {\n"
    "    if (expanded) collapseOtherSpotEntries(spotId);\n"
    "    item.classList.toggle('is-expanded', expanded);",
)

# My Spots: retain the shared list component but collapse every other Spot,
# including Spots in a different status section, before opening the selected one.
replace_once(
    "static/my_spots.js",
    "} from './spot_ui.js?v=lock-prefix-v1-20260722';",
    f"}} from './spot_ui.js?v={VERSION}';",
)
replace_once(
    "static/my_spots.js",
    "from './interface_text.js?v=transaction-integrity-v1-20260721';",
    f"from './interface_text.js?v={VERSION}';",
)
replace_once(
    "static/my_spots.js",
    "function buildMySpotListItem(spot) {\n",
    "function collapseOtherMySpotItems(activeSpotId) {\n"
    "    const activeId = Number(activeSpotId);\n"
    "    for (const expandedId of [...state.expandedSpotIds]) {\n"
    "        if (Number(expandedId) !== activeId) state.expandedSpotIds.delete(expandedId);\n"
    "    }\n\n"
    "    for (const item of els.sections.querySelectorAll('.spot-list-item.is-expanded')) {\n"
    "        if (Number(item.dataset.spotId) === activeId) continue;\n"
    "        const summary = item.querySelector('.spot-list-toggle');\n"
    "        if (summary?.getAttribute('aria-expanded') === 'true') summary.click();\n"
    "    }\n"
    "}\n\n"
    "function buildMySpotListItem(spot) {\n",
)
replace_once(
    "static/my_spots.js",
    "        onToggle: (spotId, expanded) => {\n"
    "            if (expanded) state.expandedSpotIds.add(spotId);\n"
    "            else state.expandedSpotIds.delete(spotId);\n"
    "        },",
    "        onToggle: (spotId, expanded) => {\n"
    "            if (expanded) {\n"
    "                collapseOtherMySpotItems(spotId);\n"
    "                state.expandedSpotIds.add(spotId);\n"
    "            } else {\n"
    "                state.expandedSpotIds.delete(spotId);\n"
    "            }\n"
    "        },",
)

# My Claims: use the same page-wide single-open rule and preserve state through refreshes.
replace_once(
    "static/my_claims.js",
    "from './interface_text.js?v=qol-v1-20260717';",
    f"from './interface_text.js?v={VERSION}';",
)
replace_once(
    "static/my_claims.js",
    "} from './spot_ui.js?v=lock-prefix-v1-20260722';",
    f"}} from './spot_ui.js?v={VERSION}';",
)
replace_once(
    "static/my_claims.js",
    "function setClaimExpanded(item, summary, detail, claimId, expanded) {\n"
    "    item.classList.toggle('is-expanded', expanded);",
    "function collapseOtherClaimItems(activeClaimId) {\n"
    "    const activeId = Number(activeClaimId);\n"
    "    for (const expandedId of [...state.expandedClaimIds]) {\n"
    "        if (Number(expandedId) !== activeId) state.expandedClaimIds.delete(expandedId);\n"
    "    }\n\n"
    "    for (const item of els.list.querySelectorAll('.my-claim-list-item.is-expanded')) {\n"
    "        const otherClaimId = Number(item.dataset.claimId);\n"
    "        if (otherClaimId === activeId) continue;\n"
    "        const summary = item.querySelector('.spot-list-toggle');\n"
    "        const detail = item.querySelector('.claim-list-detail');\n"
    "        if (summary?.getAttribute('aria-expanded') === 'true' && detail) {\n"
    "            setClaimExpanded(item, summary, detail, otherClaimId, false);\n"
    "        }\n"
    "    }\n"
    "}\n\n"
    "function setClaimExpanded(item, summary, detail, claimId, expanded) {\n"
    "    if (expanded) collapseOtherClaimItems(claimId);\n"
    "    item.classList.toggle('is-expanded', expanded);",
)
replace_once(
    "static/my_claims.js",
    "    item.className = 'spot-list-item my-claim-list-item';\n",
    "    item.className = 'spot-list-item my-claim-list-item';\n"
    "    item.dataset.claimId = String(claimId);\n",
)

# Standalone Spot detail uses the same split lines, bold reward, and duration copy.
replace_once(
    "static/spot_detail.js",
    "from './interface_text.js?v=qol-v1-20260717';",
    f"from './interface_text.js?v={VERSION}';",
)
replace_once(
    "static/spot_detail.js",
    "} from './spot_ui.js?v=lock-prefix-v1-20260722';",
    f"}} from './spot_ui.js?v={VERSION}';",
)
replace_once(
    "static/spot_detail.js",
    "function buildSpotDetail(spot) {\n",
    "function buildRewardAmountLine(amountText) {\n"
    "    const fragment = document.createDocumentFragment();\n"
    "    const amount = document.createElement('strong');\n"
    "    amount.textContent = amountText;\n"
    "    fragment.append(amount, document.createTextNode(' Per Claim'));\n"
    "    return fragment;\n"
    "}\n\n"
    "function buildSpotDetail(spot) {\n",
)
replace_once(
    "static/spot_detail.js",
    "    const claimWord = availableClaims === 1 ? 'claim' : 'claims';\n",
    "",
)
replace_once(
    "static/spot_detail.js",
    "    appendBulletLine(lines, `${nimPerClaimText(spot)} Per Claim (${availableClaims} ${claimWord} available)`);\n",
    "    appendBulletLine(lines, buildRewardAmountLine(nimPerClaimText(spot)));\n"
    "    appendBulletLine(lines, `${availableClaims} ${availableClaims === 1 ? 'Claim' : 'Claims'} Remaining`);\n",
)
replace_once(
    "static/spot_detail.js",
    "        appendBulletLine(lines, `Requires a claim duration of ${duration}`);",
    "        appendBulletLine(lines, `Must remain on Spot for ${duration}`);",
)

# Owner-facing Spot details use the same clearer duration sentence.
replace_once(
    "static/interface_text.js",
    "            claimDuration: (duration) => `Requires a claim duration of ${duration}`,",
    "            claimDuration: (duration) => `Must remain on Spot for ${duration}`,",
)

# Regression coverage is intentionally source-focused, matching the repository's
# existing frontend tests while the full CI suite still parses every JS module.
Path("tests/test_single_open_details_and_spot_copy.py").write_text(
    '''from __future__ import annotations\n\nimport unittest\nfrom pathlib import Path\n\n\nclass SingleOpenDetailsAndSpotCopyTest(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n        cls.root = Path(__file__).resolve().parents[1]\n        cls.find = (cls.root / "static" / "find_spots.js").read_text(encoding="utf-8")\n        cls.my_spots = (cls.root / "static" / "my_spots.js").read_text(encoding="utf-8")\n        cls.my_claims = (cls.root / "static" / "my_claims.js").read_text(encoding="utf-8")\n        cls.spot_detail = (cls.root / "static" / "spot_detail.js").read_text(encoding="utf-8")\n        cls.interface_text = (cls.root / "static" / "interface_text.js").read_text(encoding="utf-8")\n\n    def test_find_spots_collapses_other_entries_for_every_expansion_path(self):\n        self.assertIn("function collapseOtherSpotEntries(activeSpotId)", self.find)\n        self.assertIn("if (expanded) collapseOtherSpotEntries(spotId);", self.find)\n        self.assertIn("setListItemExpanded(entry.item, entry.summary, entry.detail", self.find)\n        self.assertIn("focusSpotInList(spotId)", self.find)\n\n    def test_my_spots_uses_page_wide_single_open_behaviour(self):\n        self.assertIn("function collapseOtherMySpotItems(activeSpotId)", self.my_spots)\n        self.assertIn("els.sections.querySelectorAll('.spot-list-item.is-expanded')", self.my_spots)\n        self.assertIn("collapseOtherMySpotItems(spotId);", self.my_spots)\n\n    def test_my_claims_uses_page_wide_single_open_behaviour(self):\n        self.assertIn("function collapseOtherClaimItems(activeClaimId)", self.my_claims)\n        self.assertIn("item.dataset.claimId = String(claimId);", self.my_claims)\n        self.assertIn("if (expanded) collapseOtherClaimItems(claimId);", self.my_claims)\n\n    def test_reward_and_remaining_claims_are_separate_lines(self):\n        for source in (self.find, self.spot_detail):\n            self.assertIn("const amount = document.createElement('strong');", source)\n            self.assertIn("document.createTextNode(' Per Claim')", source)\n            self.assertIn("'Claim' : 'Claims'} Remaining", source)\n            self.assertNotIn("claims available)", source)\n\n    def test_duration_copy_is_consistent(self):\n        for source in (self.find, self.spot_detail, self.interface_text):\n            self.assertIn("Must remain on Spot for", source)\n        self.assertNotIn("Requires a claim duration of", self.interface_text)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    encoding="utf-8",
)
