import tempfile
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import trans_updater


def _normalise_test_address(value, **_kwargs):
    return str(value or "").strip().lower() or None


class FundingWalletDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def _create_spot(self):
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="funding-owner")
            spot_id = await db_access.create_spot(db, created_by=owner_id, title="Funding Spot")
            spot = await db_access.get_spot(db, spot_id=spot_id)
            await db.commit()
        return owner_id, spot_id, spot

    async def test_first_confirmed_sender_owns_later_topups(self):
        owner_id, spot_id, spot = await self._create_spot()
        deposit_address = str(spot[schema.SPOT_DEPOSIT_ADDRESS])

        async with schema.get_db() as db:
            first_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=10_000_000,
                from_address="wallet-a",
                to_address=deposit_address,
                tx_hash="funding-first",
            )
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=first_id,
                block_number=1,
            )
            wrong_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=2_000_000,
                from_address="wallet-a",
                to_address=deposit_address,
                tx_hash="funding-wrong",
            )
            await db.commit()
            wrong = await db_access.get_transaction(db, trans_id=wrong_id)

            with (
                mock.patch.object(
                    trans_updater,
                    "_normalise_address_for_compare",
                    side_effect=_normalise_test_address,
                ),
                mock.patch.object(
                    trans_updater.cache,
                    "notify_transaction_changed",
                    mock.AsyncMock(),
                ),
            ):
                result = await trans_updater.mark_trans_as_confirmed(
                    db,
                    wrong,
                    block_number=2,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address="wallet-b",
                        to_address=deposit_address,
                        amount=2_000_000,
                    ),
                )

            wrong_after = await db_access.get_transaction(db, trans_id=wrong_id)
            self.assertEqual(result["status"], "failed")
            self.assertIn("original funding wallet", result["reason"])
            self.assertEqual(int(wrong_after[schema.TRANS_STATUS]), const.TRANS_STATUS_FAILED)
            self.assertEqual(wrong_after[schema.TRANS_FROM_ADDRESS], "wallet-b")
            self.assertEqual(int(wrong_after[schema.TRANS_BLOCK_NUMBER]), 2)
            self.assertEqual(
                await db_access.get_confirmed_spot_funding_address(db, spot_id=spot_id),
                "wallet-a",
            )

            same_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=3_000_000,
                from_address="wallet-a",
                to_address=deposit_address,
                tx_hash="funding-same",
            )
            await db.commit()
            same = await db_access.get_transaction(db, trans_id=same_id)
            with (
                mock.patch.object(
                    trans_updater,
                    "_normalise_address_for_compare",
                    side_effect=_normalise_test_address,
                ),
                mock.patch.object(
                    trans_updater.cache,
                    "notify_transaction_changed",
                    mock.AsyncMock(),
                ),
            ):
                same_result = await trans_updater.mark_trans_as_confirmed(
                    db,
                    same,
                    block_number=3,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address="wallet-a",
                        to_address=deposit_address,
                        amount=3_000_000,
                    ),
                )

            same_after = await db_access.get_transaction(db, trans_id=same_id)
            self.assertEqual(same_result["status"], "confirmed")
            self.assertEqual(int(same_after[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)
            self.assertEqual(
                await db_access.get_confirmed_spot_deposit_total(db, spot_id=spot_id),
                13_000_000,
            )


class FundingWalletSubmissionGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_known_different_sender_is_rejected_before_recording(self):
        spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_DEPOSIT_ADDRESS: "deposit-wallet",
        }
        with (
            mock.patch.object(
                trans_updater.db_access,
                "get_spot",
                mock.AsyncMock(return_value=spot),
            ),
            mock.patch.object(
                trans_updater.db_access,
                "get_confirmed_spot_funding_address",
                mock.AsyncMock(return_value="wallet-a"),
            ),
            mock.patch.object(
                trans_updater.wallet,
                "normalise_nimiq_address",
                side_effect=_normalise_test_address,
            ),
            mock.patch.object(
                trans_updater,
                "_normalise_address_for_compare",
                side_effect=_normalise_test_address,
            ),
            mock.patch.object(
                trans_updater.db_access,
                "create_spot_deposit_transaction",
                mock.AsyncMock(),
            ) as create_transaction,
        ):
            with self.assertRaisesRegex(ValueError, "original funding wallet"):
                await trans_updater.record_spot_deposit_transaction(
                    object(),
                    user_id=1,
                    spot_id=7,
                    amount=100,
                    from_address="wallet-b",
                    to_address="deposit-wallet",
                    tx_hash="wrong-wallet-topup",
                )

        create_transaction.assert_not_awaited()


class ClaimRuleWithoutLocationTest(unittest.IsolatedAsyncioTestCase):
    def _patch_rules(self, *, owner_id=99, capacity=True, reached_limit=False):
        return (
            mock.patch.object(db_access, "can_user_claim", mock.AsyncMock(return_value=True)),
            mock.patch.object(
                db_access,
                "get_public_spot",
                mock.AsyncMock(return_value={"availability_rank": 0}),
            ),
            mock.patch.object(
                db_access,
                "get_spot",
                mock.AsyncMock(return_value={schema.SPOT_CREATED_BY: owner_id}),
            ),
            mock.patch.object(
                db_access,
                "get_claim_distance_check",
                mock.AsyncMock(),
            ),
            mock.patch.object(
                db_access,
                "is_spot_claim_capacity_available",
                mock.AsyncMock(return_value=capacity),
            ),
            mock.patch.object(
                db_access,
                "has_user_reached_claim_limit",
                mock.AsyncMock(return_value=reached_limit),
            ),
            mock.patch.object(
                db_access,
                "has_spot_cancellation_started",
                mock.AsyncMock(return_value=False),
            ),
        )

    async def _check(self, *, owner_id=99, capacity=True, reached_limit=False):
        patches = self._patch_rules(
            owner_id=owner_id,
            capacity=capacity,
            reached_limit=reached_limit,
        )
        started = []
        for patcher in patches:
            started.append(patcher.start())
            self.addCleanup(patcher.stop)

        result = await db_access.get_claim_rule_check(
            object(),
            spot_id=7,
            user_id=1,
            lat=None,
            long=None,
        )
        started[3].assert_not_awaited()
        return result

    async def test_own_spot_supersedes_unknown_location(self):
        result = await self._check(owner_id=1, capacity=False, reached_limit=True)
        self.assertEqual(result["reason"], "own_spot")

    async def test_exhausted_capacity_supersedes_unknown_location(self):
        result = await self._check(capacity=False, reached_limit=True)
        self.assertEqual(result["reason"], "capacity_full")

    async def test_user_limit_supersedes_unknown_location(self):
        result = await self._check(capacity=True, reached_limit=True)
        self.assertEqual(result["reason"], "user_limit_reached")

    async def test_unknown_location_is_used_only_without_harder_blocker(self):
        result = await self._check(capacity=True, reached_limit=False)
        self.assertEqual(result["reason"], "location_unknown")
        self.assertFalse(result["allowed"])
        self.assertFalse(result["location_known"])


if __name__ == "__main__":
    unittest.main()
