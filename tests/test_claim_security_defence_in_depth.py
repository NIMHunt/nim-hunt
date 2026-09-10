from __future__ import annotations

import unittest
from unittest import mock

import claim_security_defence_in_depth as defence
import constants as const
import database as schema

VALID_NIMIQ_PAY_PAYOUT = "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"


class ClaimSecurityDefenceInDepthTest(unittest.IsolatedAsyncioTestCase):
    def _event(
        self,
        *,
        claim_id: int,
        spot_id: int,
        claimed_at: int,
        lat: float,
        long: float,
        device: str,
        wallet: str,
        centre_offset: float = 25.0,
    ) -> dict:
        return {
            "claim_id": claim_id,
            "spot_id": spot_id,
            "device_id_hash": device,
            "verified_wallet": wallet,
            "claimed_at": claimed_at,
            "user_created_at": claimed_at - 10,
            "session_created_at": claimed_at - 10,
            "spot_lat": lat,
            "spot_long": long,
            "spot_radius": 50,
            "centre_offset_metres": centre_offset,
            "payout_address": "shared-payout",
        }

    def test_broad_burst_catches_coordinate_noise(self):
        now = 1_700_000_000
        coordinates = [
            (-33.8568, 151.2153),
            (41.0082, 28.9784),
            (-22.9519, -43.2105),
            (25.1972, 55.2744),
            (40.7128, -74.0060),
        ]
        events = [
            self._event(
                claim_id=index + 1,
                spot_id=index + 1,
                claimed_at=now + index,
                lat=lat,
                long=long,
                device=(f"{index + 1:x}" * 64)[:64],
                wallet=f"wallet-{index}",
                centre_offset=25.0,
            )
            for index, (lat, long) in enumerate(coordinates)
        ]

        ids = defence.broad_new_identity_burst_claim_ids(events, now=now + 10)
        self.assertEqual(ids, [1, 2, 3, 4, 5])

    def test_broad_burst_requires_several_distinct_identities(self):
        now = 1_700_000_000
        events = [
            self._event(
                claim_id=index + 1,
                spot_id=index + 1,
                claimed_at=now + index,
                lat=51.5 + index,
                long=-0.1 - index,
                device=(f"{index + 1:x}" * 64)[:64],
                wallet=f"wallet-{index}",
            )
            for index in range(max(1, defence.BROAD_BURST_MIN_IDENTITIES - 1))
        ]

        self.assertEqual(
            defence.broad_new_identity_burst_claim_ids(events, now=now + 10),
            [],
        )

    def test_broad_burst_catches_sweeps_of_one_to_four_spots(self):
        now = 1_700_000_000
        for spot_count in range(1, 5):
            events = [
                self._event(
                    claim_id=index + 1,
                    spot_id=(index % spot_count) + 1,
                    claimed_at=now + index,
                    lat=51.5,
                    long=-0.1,
                    device=f"{index + 1:064x}",
                    wallet=f"wallet-{index}",
                )
                for index in range(defence.BROAD_BURST_MIN_IDENTITIES)
            ]
            with self.subTest(spot_count=spot_count):
                self.assertEqual(
                    defence.broad_new_identity_burst_claim_ids(events, now=now + 20),
                    list(range(1, defence.BROAD_BURST_MIN_IDENTITIES + 1)),
                )

    def test_pre_aged_users_and_sessions_are_still_first_claim_identities(self):
        now = 1_700_000_000
        events = [
            {**self._event(claim_id=i + 1, spot_id=1, claimed_at=now + i,
                           lat=51.5, long=-0.1, device=f"{i + 1:064x}", wallet=f"wallet-{i}"),
             "user_created_at": now - 86_400, "session_created_at": now - 7_200}
            for i in range(defence.BROAD_BURST_MIN_IDENTITIES)
        ]
        self.assertTrue(defence.broad_new_identity_burst_claim_ids(events, now=now + 20))

    def test_legitimate_venue_crowd_is_not_marked_from_novelty_or_wifi(self):
        now = 1_700_000_000
        events = [
            {
                **self._event(
                    claim_id=i + 1, spot_id=1, claimed_at=now + i * 30,
                    lat=51.5 + i * 0.000001, long=-0.1 - i * 0.000001,
                    device=f"{i + 1:064x}", wallet=f"wallet-{i}", centre_offset=3 + i,
                ),
                "payout_address": f"independent-payout-{i}",
                "ip_hash": "venue-public-wifi",
            }
            for i in range(20)
        ]
        self.assertEqual(
            defence.broad_new_identity_burst_claim_ids(events, now=now + 15 * 60), []
        )

    def test_source_network_alone_never_blocks(self):
        with mock.patch.object(
            defence,
            "_ORIGINAL_PRECLAIM_RISK",
            return_value={
                "blocked": True,
                "reason": "source_network_impossible_travel",
                "retry_at": 123,
            },
        ):
            decision = defence._preclaim_risk_without_ip_only_block([], {})

        self.assertFalse(decision["blocked"])
        self.assertEqual(decision["reason"], "allow")

    async def test_public_claim_uses_verified_signer_as_payout(self):
        verified = const.DEV_PLATFORM_FEE_ADDRESS
        delegate = mock.AsyncMock(return_value={"id": 7})
        binding = {"wallet_address": verified}

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                defence.claim_security,
                "_metadata_get",
                new=mock.AsyncMock(return_value=binding),
            ),
            mock.patch.object(
                defence,
                "_verified_wallet_owns_spot",
                new=mock.AsyncMock(return_value=False),
            ) as owns_spot,
            mock.patch.object(
                defence,
                "_wallet_has_reached_spot_limit",
                new=mock.AsyncMock(return_value=False),
            ) as reached_limit,
            mock.patch.object(defence, "_CLAIM_ATTEMPT_DELEGATE", delegate),
        ):
            result = await defence._create_claim_attempt_bound_to_verified_wallet(
                object(),
                spot_id=3,
                user_id=4,
                lat=51.5,
                long=-0.1,
                payout_address=VALID_NIMIQ_PAY_PAYOUT,
            )

        self.assertEqual(result, {"id": 7})
        owns_spot.assert_awaited_once_with(
            mock.ANY,
            spot_id=3,
            wallet_address=verified,
        )
        reached_limit.assert_awaited_once_with(
            mock.ANY,
            spot_id=3,
            wallet_address=verified,
        )
        self.assertEqual(
            delegate.await_args.kwargs["payout_address"],
            verified,
        )
        self.assertNotEqual(
            delegate.await_args.kwargs["payout_address"],
            defence.claim_security._canonical_optional_address(VALID_NIMIQ_PAY_PAYOUT),
        )

    async def test_public_claim_does_not_require_browser_payout_address(self):
        verified = const.DEV_PLATFORM_FEE_ADDRESS
        delegate = mock.AsyncMock(return_value={"id": 7})
        binding = {"wallet_address": verified}

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                defence.claim_security,
                "_metadata_get",
                new=mock.AsyncMock(return_value=binding),
            ),
            mock.patch.object(
                defence,
                "_verified_wallet_owns_spot",
                new=mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(
                defence,
                "_wallet_has_reached_spot_limit",
                new=mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(defence, "_CLAIM_ATTEMPT_DELEGATE", delegate),
        ):
            for payout_address in (None, "", "not-a-nimiq-address"):
                with self.subTest(payout_address=payout_address):
                    await defence._create_claim_attempt_bound_to_verified_wallet(
                        object(),
                        spot_id=3,
                        user_id=4,
                        lat=51.5,
                        long=-0.1,
                        payout_address=payout_address,
                    )

        self.assertEqual(delegate.await_count, 3)
        self.assertTrue(all(
            call.kwargs["payout_address"] == verified
            for call in delegate.await_args_list
        ))

    async def test_same_verified_wallet_cannot_reset_spot_limit_with_new_device(self):
        verified = const.DEV_PLATFORM_FEE_ADDRESS
        delegate = mock.AsyncMock(return_value={"id": 7})
        binding = {"wallet_address": verified}

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                defence.claim_security,
                "_metadata_get",
                new=mock.AsyncMock(return_value=binding),
            ),
            mock.patch.object(
                defence,
                "_verified_wallet_owns_spot",
                new=mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(
                defence,
                "_wallet_has_reached_spot_limit",
                new=mock.AsyncMock(return_value=True),
            ),
            mock.patch.object(defence, "_CLAIM_ATTEMPT_DELEGATE", delegate),
        ):
            with self.assertRaisesRegex(ValueError, "claim limit"):
                await defence._create_claim_attempt_bound_to_verified_wallet(
                    object(),
                    spot_id=3,
                    user_id=999,
                    lat=51.5,
                    long=-0.1,
                    payout_address=None,
                )

        delegate.assert_not_awaited()

    async def test_same_verified_wallet_cannot_claim_own_spot_with_new_device(self):
        verified = const.DEV_PLATFORM_FEE_ADDRESS
        delegate = mock.AsyncMock(return_value={"id": 7})
        binding = {"wallet_address": verified}

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                defence.claim_security,
                "_metadata_get",
                new=mock.AsyncMock(return_value=binding),
            ),
            mock.patch.object(
                defence,
                "_verified_wallet_owns_spot",
                new=mock.AsyncMock(return_value=True),
            ),
            mock.patch.object(
                defence,
                "_wallet_has_reached_spot_limit",
                new=mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(defence, "_CLAIM_ATTEMPT_DELEGATE", delegate),
        ):
            with self.assertRaisesRegex(ValueError, "own spot"):
                await defence._create_claim_attempt_bound_to_verified_wallet(
                    object(),
                    spot_id=3,
                    user_id=999,
                    lat=51.5,
                    long=-0.1,
                    payout_address=None,
                )

        delegate.assert_not_awaited()

    async def test_duration_promotion_rechecks_wallet_limit(self):
        verified = const.DEV_PLATFORM_FEE_ADDRESS
        pending = {
            schema.CLAIM_ID: 12,
            schema.CLAIM_SPOT_ID: 3,
            schema.CLAIM_RECIPIENT: 999,
            schema.CLAIM_STATUS: const.CLAIM_STATUS_PENDING,
        }
        failed = {**pending, schema.CLAIM_STATUS: const.CLAIM_STATUS_FAILED}
        delegate = mock.AsyncMock(return_value={**pending, schema.CLAIM_STATUS: const.CLAIM_STATUS_SUCCESS})

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(
                defence.db_access,
                "get_claim",
                new=mock.AsyncMock(side_effect=[pending, failed]),
            ),
            mock.patch.object(
                defence.db_access,
                "is_prizedraw",
                new=mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(
                defence.claim_security,
                "_metadata_get",
                new=mock.AsyncMock(return_value={"wallet_address": verified}),
            ),
            mock.patch.object(
                defence,
                "_wallet_has_reached_spot_limit",
                new=mock.AsyncMock(return_value=True),
            ),
            mock.patch.object(
                defence.db_access,
                "set_claim_status_to_failed",
                new=mock.AsyncMock(),
            ) as fail_claim,
            mock.patch.object(defence, "_PROMOTE_CLAIM_DELEGATE", delegate),
        ):
            result = await defence._promote_claim_with_verified_wallet_limit(
                object(),
                claim_id=12,
            )

        fail_claim.assert_awaited_once_with(mock.ANY, claim_id=12)
        delegate.assert_not_awaited()
        self.assertEqual(result[schema.CLAIM_STATUS], const.CLAIM_STATUS_FAILED)
        self.assertEqual(
            result["capacity_promotion"]["reason"],
            "verified_wallet_claim_limit_reached",
        )


if __name__ == "__main__":
    unittest.main()
