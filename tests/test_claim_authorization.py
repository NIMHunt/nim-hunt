from __future__ import annotations

import unittest

import claim_authorization as authorization


class ClaimAuthorizationCanonicalTests(unittest.TestCase):
    def test_exact_canonical_message(self):
        message = authorization.build_message(
            environment="production",
            network="mainnet",
            spot_id=123,
            receiving_wallet="NQ00 CANONICAL",
            device="a" * 64,
            latitude_e6=55_864_000,
            longitude_e6=-4_250_000,
            accuracy_cm=1200,
            nonce="b" * 64,
            issued_at=100,
            expires_at=190,
        )
        self.assertEqual(
            message,
            (
                "NimHunt Claim Authorization\nVersion: 2\nEnvironment: production\n"
                "Network: mainnet\nAction: claim\nSpotId: 123\n"
                "ReceivingWallet: NQ00 CANONICAL\nDevice: " + "a" * 64 + "\n"
                "LatitudeE6: 55864000\nLongitudeE6: -4250000\nAccuracyCm: 1200\n"
                "Nonce: " + "b" * 64 + "\nIssuedAt: 100\nExpiresAt: 190\n"
            ),
        )

    def test_fixed_point_location_and_absent_accuracy(self):
        self.assertEqual(
            authorization.canonical_location(55.864, -4.25, 12),
            (55864000, -4250000, 1200),
        )
        self.assertEqual(authorization.canonical_location(0, 0, None), (0, 0, -1))

    def test_non_finite_and_out_of_range_location_fail_closed(self):
        for values in ((float("nan"), 0, 1), (91, 0, 1), (0, -181, 1), (0, 0, -1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                authorization.canonical_location(*values)

    def test_action_and_spot_are_not_malleable(self):
        common = dict(
            environment="production",
            network="mainnet",
            spot_id=1,
            receiving_wallet="NQ00 CANONICAL",
            device="a" * 64,
            latitude_e6=1,
            longitude_e6=2,
            accuracy_cm=3,
            nonce="b" * 64,
            issued_at=100,
            expires_at=190,
        )
        original = authorization.build_message(**common)
        changed = authorization.build_message(**{**common, "spot_id": 2})
        self.assertNotEqual(original, changed)
        with self.assertRaises(ValueError):
            authorization.build_message(**common, action="enter")

    def test_signed_fabricated_coordinate_is_intentionally_representable(self):
        # Cryptographic authorization establishes intent, not physical truth.
        fixed = authorization.canonical_location(55.864, -4.25, 3)
        self.assertEqual(fixed, (55864000, -4250000, 300))


if __name__ == "__main__":
    unittest.main()
