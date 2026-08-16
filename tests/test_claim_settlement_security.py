from __future__ import annotations

import unittest

import claim_settlement_security


class ClaimSettlementSecurityTest(unittest.TestCase):
    def test_security_hold_is_successful_but_not_paid(self):
        result = claim_settlement_security.normalise_standard_payout_result(
            {
                "ok": True,
                "claim_id": 12,
                "paid": True,
                "security_hold": True,
                "deferred": True,
                "reason": "security_hold",
            }
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["paid"])
        self.assertTrue(result["security_hold"])

    def test_real_submitted_payout_keeps_paid_state(self):
        result = claim_settlement_security.normalise_standard_payout_result(
            {
                "ok": True,
                "claim_id": 13,
                "paid": True,
                "trans_id": 44,
                "tx_hash": "a" * 64,
            }
        )

        self.assertTrue(result["paid"])
        self.assertEqual(result["trans_id"], 44)

    def test_ordinary_skip_is_not_paid(self):
        result = claim_settlement_security.normalise_standard_payout_result(
            {
                "ok": True,
                "claim_id": 14,
                "paid": True,
                "skipped": True,
                "reason": "zero_amount",
            }
        )

        self.assertFalse(result["paid"])


if __name__ == "__main__":
    unittest.main()
