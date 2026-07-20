from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import database as schema
import deposit_submission_safety


class DepositSubmissionSafetyTest(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = mock.AsyncMock()
        self.spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            schema.SPOT_DEPOSIT_ADDRESS: "NQ DEPOSIT",
        }

    async def test_first_deposit_accepts_missing_client_sender(self) -> None:
        with (
            mock.patch.object(
                deposit_submission_safety,
                "_existing_transaction_by_hash",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "get_spot",
                mock.AsyncMock(return_value=self.spot),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "get_confirmed_spot_funding_address",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "create_spot_deposit_transaction",
                mock.AsyncMock(return_value=91),
            ) as create_transaction,
            mock.patch.object(
                deposit_submission_safety.wallet,
                "normalise_nimiq_address",
                mock.Mock(side_effect=lambda value, **_kwargs: str(value).strip()),
            ),
        ):
            result = await deposit_submission_safety.record_spot_deposit_transaction_safely(
                self.db,
                user_id=3,
                spot_id=7,
                amount=100_000,
                from_address=None,
                to_address="NQ DEPOSIT",
                tx_hash="abc123",
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["already_recorded"])
        create_transaction.assert_awaited_once_with(
            self.db,
            user_id=3,
            spot_id=7,
            amount=100_000,
            from_address="",
            to_address="NQ DEPOSIT",
            tx_hash="abc123",
        )

    async def test_same_hash_is_an_idempotent_recording_retry(self) -> None:
        existing = {
            schema.TRANS_ID: 91,
            schema.TRANS_USER_ID: 3,
            schema.TRANS_SPOT_ID: 7,
            schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
            schema.TRANS_AMOUNT: 100_000,
            schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
            schema.TRANS_TX_HASH: "abc123",
        }
        with (
            mock.patch.object(
                deposit_submission_safety,
                "_existing_transaction_by_hash",
                mock.AsyncMock(return_value=existing),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "create_spot_deposit_transaction",
                mock.AsyncMock(),
            ) as create_transaction,
        ):
            result = await deposit_submission_safety.record_spot_deposit_transaction_safely(
                self.db,
                user_id=3,
                spot_id=7,
                amount=100_000,
                from_address=None,
                to_address="NQ DEPOSIT",
                tx_hash="abc123",
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["already_recorded"])
        self.assertEqual(result["trans_id"], 91)
        create_transaction.assert_not_awaited()

    async def test_same_hash_cannot_be_reused_for_another_spot(self) -> None:
        existing = {
            schema.TRANS_ID: 91,
            schema.TRANS_USER_ID: 3,
            schema.TRANS_SPOT_ID: 8,
            schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
            schema.TRANS_AMOUNT: 100_000,
            schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
            schema.TRANS_TX_HASH: "abc123",
        }
        with mock.patch.object(
            deposit_submission_safety,
            "_existing_transaction_by_hash",
            mock.AsyncMock(return_value=existing),
        ):
            with self.assertRaisesRegex(ValueError, "different record"):
                await deposit_submission_safety.record_spot_deposit_transaction_safely(
                    self.db,
                    user_id=3,
                    spot_id=7,
                    amount=100_000,
                    from_address=None,
                    to_address="NQ DEPOSIT",
                    tx_hash="abc123",
                )

    async def test_top_up_requires_identified_original_wallet(self) -> None:
        with (
            mock.patch.object(
                deposit_submission_safety,
                "_existing_transaction_by_hash",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "get_spot",
                mock.AsyncMock(return_value=self.spot),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "get_confirmed_spot_funding_address",
                mock.AsyncMock(return_value="NQ ORIGINAL"),
            ),
            mock.patch.object(
                deposit_submission_safety.wallet,
                "normalise_nimiq_address",
                mock.Mock(side_effect=lambda value, **_kwargs: str(value).strip()),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "did not expose the funding wallet"):
                await deposit_submission_safety.record_spot_deposit_transaction_safely(
                    self.db,
                    user_id=3,
                    spot_id=7,
                    amount=100_000,
                    from_address=None,
                    to_address="NQ DEPOSIT",
                    tx_hash="abc123",
                )

    async def test_top_up_from_different_wallet_is_rejected(self) -> None:
        with (
            mock.patch.object(
                deposit_submission_safety,
                "_existing_transaction_by_hash",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "get_spot",
                mock.AsyncMock(return_value=self.spot),
            ),
            mock.patch.object(
                deposit_submission_safety.db_access,
                "get_confirmed_spot_funding_address",
                mock.AsyncMock(return_value="NQ ORIGINAL"),
            ),
            mock.patch.object(
                deposit_submission_safety.wallet,
                "normalise_nimiq_address",
                mock.Mock(side_effect=lambda value, **_kwargs: str(value).strip()),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "original funding wallet"):
                await deposit_submission_safety.record_spot_deposit_transaction_safely(
                    self.db,
                    user_id=3,
                    spot_id=7,
                    amount=100_000,
                    from_address="NQ DIFFERENT",
                    to_address="NQ DEPOSIT",
                    tx_hash="abc123",
                )
