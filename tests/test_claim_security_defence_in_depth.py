from __future__ import annotations

import unittest
from unittest import mock

import claim_security_defence_in_depth as defence
import constants as const
import database as schema


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

    async def test_public_claim_forces_verified_wallet_and_ignores_browser_payout(self):
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
            result = await defence._create_claim_attempt_bound_to_verified_wallet(
                object(),
                spot_id=3,
                user_id=4,
                lat=51.5,
                long=-0.1,
                payout_address="client-controlled-value-is-ignored",
            )

        self.assertEqual(result, {"id": 7})
        self.assertEqual(delegate.await_args.kwargs["payout_address"], verified)

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
