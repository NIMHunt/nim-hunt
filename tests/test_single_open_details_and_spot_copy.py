from __future__ import annotations

import unittest
from pathlib import Path


class SingleOpenDetailsAndSpotCopyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.find = (cls.root / "static" / "find_spots.js").read_text(encoding="utf-8")
        cls.my_spots = (cls.root / "static" / "my_spots.js").read_text(encoding="utf-8")
        cls.my_claims = (cls.root / "static" / "my_claims.js").read_text(encoding="utf-8")
        cls.spot_detail = (cls.root / "static" / "spot_detail.js").read_text(encoding="utf-8")
        cls.interface_text = (cls.root / "static" / "interface_text.js").read_text(encoding="utf-8")

    def test_find_spots_collapses_other_entries_for_every_expansion_path(self):
        self.assertIn("function collapseOtherSpotEntries(activeSpotId)", self.find)
        self.assertIn("if (expanded) collapseOtherSpotEntries(spotId);", self.find)
        self.assertIn("setListItemExpanded(entry.item, entry.summary, entry.detail", self.find)
        self.assertIn("focusSpotInList(spotId)", self.find)

    def test_my_spots_uses_page_wide_single_open_behaviour(self):
        self.assertIn("function collapseOtherMySpotItems(activeSpotId)", self.my_spots)
        self.assertIn("els.sections.querySelectorAll('.spot-list-item.is-expanded')", self.my_spots)
        self.assertIn("collapseOtherMySpotItems(spotId);", self.my_spots)

    def test_my_claims_uses_page_wide_single_open_behaviour(self):
        self.assertIn("function collapseOtherClaimItems(activeClaimId)", self.my_claims)
        self.assertIn("item.dataset.claimId = String(claimId);", self.my_claims)
        self.assertIn("if (expanded) collapseOtherClaimItems(claimId);", self.my_claims)

    def test_reward_and_remaining_claims_are_separate_lines(self):
        for source in (self.find, self.spot_detail):
            self.assertIn("const amount = document.createElement('strong');", source)
            self.assertIn("document.createTextNode(' Per Claim')", source)
            self.assertIn("'Claim' : 'Claims'} Remaining", source)
            self.assertNotIn("claims available)", source)

    def test_duration_copy_is_consistent(self):
        for source in (self.find, self.spot_detail, self.interface_text):
            self.assertIn("Must remain on Spot for", source)
        self.assertNotIn("Requires a claim duration of", self.interface_text)


if __name__ == "__main__":
    unittest.main()
