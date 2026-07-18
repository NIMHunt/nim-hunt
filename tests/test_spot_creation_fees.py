import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from unittest import mock

import constants as const
import database as schema
import db_access
import public_html
import trans_updater
import spoof


class SpotCreationFeeFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash=f"owner-{id(self)}",
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def create_standard_spot(
        self,
        *,
        fee: int | None = None,
        total_value: int | None = None,
        title: str = "Creation Fee Spot",
    ) -> int:
        fee = const.STANDARD_SPOT_CREATION_FEE if fee is None else int(fee)
        total_value = const.MIN_SPOT_TOTAL_VALUE if total_value is None else int(total_value)
        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", fee), mock.patch.object(
            const,
            "SPOT_CANCELLATION_FEE_ADDRESS",
            const.DEV_PLATFORM_FEE_ADDRESS,
        ):
            async with schema.get_db() as db:
                spot_id = await db_access.create_spot(
                    db,
                    created_by=self.owner_id,
                    title=title,
                    desc="A fully configured standard Spot.",
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=0,
                    max_claims_per_user=1,
                    max_total_claims=1,
                    total_value=total_value,
                    starts_at=int(time.time()) + 3600,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
                await db.commit()
                return spot_id

    async def create_prizedraw_spot(self, *, fee: int) -> int:
        with mock.patch.object(const, "PRIZEDRAW_SPOT_CREATION_FEE", int(fee)), mock.patch.object(
            const,
            "SPOT_CANCELLATION_FEE_ADDRESS",
            const.DEV_PLATFORM_FEE_ADDRESS,
        ):
            async with schema.get_db() as db:
                spot_id = await db_access.create_prizedraw(
                    db,
                    created_by=self.owner_id,
                    title="Fee Prizedraw",
                    desc="A fully configured Prizedraw.",
                    lat=51.5,
                    long=-0.1,
                    radius=100,
                    claim_duration=0,
                    max_claims_per_user=1,
                    max_total_claims=2,
                    total_value=2 * const.MIN_PRIZEDRAW_PRIZE_PAYOUT,
                    prize_count=1,
                    starts_at=int(time.time()) + 3600,
                    ends_at=const.MIN_SPOT_ENDS_AFTER_SECONDS,
                    auto_reverse_geocode=False,
                    city="London",
                    country="United Kingdom",
                )
                await db.commit()
                return spot_id

    async def get_spot(self, spot_id: int):
        async with schema.get_db() as db:
            return await db_access.get_spot(db, spot_id=int(spot_id))

    async def create_deposit(
        self,
        spot_id: int,
        amount: int,
        *,
        status: int = const.TRANS_STATUS_CONFIRMED,
        suffix: str = "deposit",
    ) -> int:
        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=int(spot_id))
            trans_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=self.owner_id,
                spot_id=int(spot_id),
                amount=int(amount),
                from_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                to_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                tx_hash=f"{suffix}-{spot_id}-{time.time_ns()}",
            )
            if status == const.TRANS_STATUS_CONFIRMED:
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=trans_id,
                    block_number=123,
                )
            elif status == const.TRANS_STATUS_FAILED:
                await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
            await db.commit()
            return trans_id

    async def create_creation_fee_transaction(
        self,
        spot_id: int,
        *,
        status: int = const.TRANS_STATUS_PENDING,
        suffix: str = "fee",
    ) -> int:
        async with schema.get_db() as db:
            spot = await db_access.get_spot(db, spot_id=int(spot_id))
            trans_id = await db_access.create_spot_creation_fee_transaction(
                db,
                user_id=self.owner_id,
                spot_id=int(spot_id),
                amount=int(spot[schema.SPOT_CREATION_FEE]),
                from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
                tx_hash=f"{suffix}-{spot_id}-{time.time_ns()}",
            )
            if status == const.TRANS_STATUS_CONFIRMED:
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=trans_id,
                    block_number=456,
                )
            elif status == const.TRANS_STATUS_FAILED:
                await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
            await db.commit()
            return trans_id


class CreationFeeSnapshotTests(SpotCreationFeeFixture):
    async def test_standard_and_prizedraw_fees_are_snapshotted_independently(self):
        standard_fee = 123_456
        prizedraw_fee = 654_321
        standard_id = await self.create_standard_spot(fee=standard_fee)
        prizedraw_id = await self.create_prizedraw_spot(fee=prizedraw_fee)

        standard = await self.get_spot(standard_id)
        prizedraw = await self.get_spot(prizedraw_id)
        self.assertEqual(standard[schema.SPOT_CREATION_FEE], standard_fee)
        self.assertEqual(prizedraw[schema.SPOT_CREATION_FEE], prizedraw_fee)
        self.assertEqual(
            standard[schema.SPOT_CREATION_FEE_ADDRESS],
            const.DEV_PLATFORM_FEE_ADDRESS,
        )
        self.assertEqual(
            prizedraw[schema.SPOT_CREATION_FEE_ADDRESS],
            const.DEV_PLATFORM_FEE_ADDRESS,
        )

        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", 999_999), mock.patch.object(
            const,
            "SPOT_CANCELLATION_FEE_ADDRESS",
            "NQ52 J5R7 4U5Y 5XDL YKJ2 96ME 3AQ9 V7DP 8MX8",
        ):
            unchanged = await self.get_spot(standard_id)
        self.assertEqual(unchanged[schema.SPOT_CREATION_FEE], standard_fee)
        self.assertEqual(
            unchanged[schema.SPOT_CREATION_FEE_ADDRESS],
            const.DEV_PLATFORM_FEE_ADDRESS,
        )

    async def test_zero_fee_requires_only_spot_value_and_no_fee_transaction(self):
        spot_id = await self.create_standard_spot(fee=0)
        spot = await self.get_spot(spot_id)
        self.assertEqual(db_access.spot_required_deposit_amount(spot), spot[schema.SPOT_TOTAL_VALUE])

        await self.create_deposit(spot_id, spot[schema.SPOT_TOTAL_VALUE])
        async with schema.get_db() as db:
            self.assertTrue(await db_access.can_publish_spot(db, spot_id=spot_id))
            result = await trans_updater.submit_spot_creation_fee_transaction(
                db,
                spot_id=spot_id,
            )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "zero_amount")


class CreationFeeFundingTests(SpotCreationFeeFixture):
    async def test_partial_deposits_do_not_trigger_fee_until_full_total_is_confirmed(self):
        fee = 2 * const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee)
        spot = await self.get_spot(spot_id)
        required = db_access.spot_required_deposit_amount(spot)

        await self.create_deposit(spot_id, required // 2, suffix="part-a")
        async with schema.get_db() as db:
            self.assertEqual(
                await db_access.get_spot_ids_ready_for_creation_fee(db),
                [],
            )
            result = await trans_updater.submit_spot_creation_fee_transaction(
                db,
                spot_id=spot_id,
            )
            self.assertEqual(result["reason"], "not_fully_funded")

        await self.create_deposit(spot_id, required - required // 2, suffix="part-b")
        async with schema.get_db() as db:
            self.assertEqual(
                await db_access.get_spot_ids_ready_for_creation_fee(db),
                [spot_id],
            )

    async def test_database_deposit_totals_preserve_pending_status_zero(self):
        spot_id = await self.create_standard_spot(fee=0, title="Pending Total")
        pending_amount = 7 * const.LUNA_PER_NIM
        await self.create_deposit(
            spot_id,
            pending_amount,
            status=const.TRANS_STATUS_PENDING,
        )
        async with schema.get_db() as db:
            totals = await db_access.get_spot_deposit_totals(db, spot_id=spot_id)
        self.assertEqual(totals["pending_amount"], pending_amount)
        self.assertEqual(totals["confirmed_amount"], 0)

    async def test_creation_fee_database_boundary_rejects_changed_amount_or_recipient(self):
        fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee, title="Fee Boundary")
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "amount does not match"):
                async with db_access.transaction(db, immediate=True):
                    await db_access.create_spot_creation_fee_transaction(
                        db,
                        user_id=self.owner_id,
                        spot_id=spot_id,
                        amount=fee + 1,
                        from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                        to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
                        tx_hash="wrong-amount",
                    )

            with self.assertRaisesRegex(ValueError, "recipient does not match"):
                async with db_access.transaction(db, immediate=True):
                    await db_access.create_spot_creation_fee_transaction(
                        db,
                        user_id=self.owner_id,
                        spot_id=spot_id,
                        amount=fee,
                        from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                        to_address="NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF",
                        tx_hash="wrong-recipient",
                    )

            with self.assertRaisesRegex(ValueError, "sender does not match"):
                async with db_access.transaction(db, immediate=True):
                    await db_access.create_spot_creation_fee_transaction(
                        db,
                        user_id=self.owner_id,
                        spot_id=spot_id,
                        amount=fee,
                        from_address="NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF",
                        to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
                        tx_hash="wrong-sender",
                    )

            other_owner_id = await db_access.create_user(
                db,
                device_id_hash=f"other-owner-{time.time_ns()}",
            )
            await db.commit()
            with self.assertRaisesRegex(ValueError, "user does not match"):
                async with db_access.transaction(db, immediate=True):
                    await db_access.create_spot_creation_fee_transaction(
                        db,
                        user_id=other_owner_id,
                        spot_id=spot_id,
                        amount=fee,
                        from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                        to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
                        tx_hash="wrong-owner",
                    )

    async def test_publish_waits_for_fee_confirmation(self):
        fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee)
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        async with schema.get_db() as db:
            self.assertFalse(await db_access.can_publish_spot(db, spot_id=spot_id))

        fee_trans_id = await self.create_creation_fee_transaction(spot_id)
        async with schema.get_db() as db:
            self.assertFalse(await db_access.can_publish_spot(db, spot_id=spot_id))
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=fee_trans_id,
                block_number=999,
            )
            await db.commit()
            self.assertTrue(await db_access.can_publish_spot(db, spot_id=spot_id))

    async def test_failed_creation_fee_can_be_retried_but_active_fee_is_unique(self):
        fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee)
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        first_id = await self.create_creation_fee_transaction(spot_id)
        with self.assertRaises(Exception):
            await self.create_creation_fee_transaction(spot_id, suffix="duplicate")

        async with schema.get_db() as db:
            await db_access.set_transaction_status_to_failed(db, trans_id=first_id)
            await db.commit()
        second_id = await self.create_creation_fee_transaction(spot_id, suffix="retry")
        self.assertNotEqual(first_id, second_id)

    async def test_scheduler_submits_fee_once_with_snapshotted_amount_and_address(self):
        fee = 3 * const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee)
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        submitted = trans_updater.SubmittedChainTransaction(
            tx_hash="creation-fee-chain-hash",
            from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
            to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
            amount=fee,
        )
        with mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True), mock.patch.object(
            trans_updater,
            "submit_chain_send_from_spot_deposit",
            mock.AsyncMock(return_value=submitted),
        ) as send:
            async with schema.get_db() as db:
                result = await trans_updater.submit_ready_spot_creation_fees(db)
                await db.commit()
                again = await trans_updater.submit_ready_spot_creation_fees(db)

        self.assertTrue(result["ok"])
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(again["submitted_count"], 0)
        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["amount"], fee)
        self.assertEqual(kwargs["to_address"], spot[schema.SPOT_CREATION_FEE_ADDRESS])
        self.assertIn("Creation Fee", kwargs["memo"])

        async with schema.get_db() as db:
            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )
        fee_rows = [
            row
            for row in transactions
            if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CREATION_FEE
        ]
        self.assertEqual(len(fee_rows), 1)
        self.assertEqual(fee_rows[0][schema.TRANS_AMOUNT], fee)
        self.assertEqual(
            fee_rows[0][schema.TRANS_TO_ADDRESS],
            spot[schema.SPOT_CREATION_FEE_ADDRESS],
        )


class CreationFeeCancellationTests(SpotCreationFeeFixture):
    async def test_partial_funded_draft_is_refunded_without_creation_fee(self):
        creation_fee = 5 * const.LUNA_PER_NIM
        cancellation_fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=creation_fee)
        deposit = 20 * const.LUNA_PER_NIM
        await self.create_deposit(spot_id, deposit)

        with mock.patch.object(
            trans_updater,
            "submit_platform_fee_transaction",
            mock.AsyncMock(return_value={"ok": True, "trans_id": 11}),
        ) as fee_send, mock.patch.object(
            trans_updater,
            "submit_spot_refund_transaction",
            mock.AsyncMock(return_value={"ok": True, "trans_id": 12}),
        ) as refund_send:
            async with schema.get_db() as db:
                result = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=cancellation_fee,
                )

        self.assertEqual(result["confirmed_creation_fee_total"], 0)
        self.assertEqual(result["fee_amount"], cancellation_fee)
        self.assertEqual(result["refund_amount"], deposit - cancellation_fee)
        fee_send.assert_awaited_once()
        refund_send.assert_awaited_once()

    async def test_fully_funded_draft_cannot_cancel_before_creation_fee_confirms(self):
        creation_fee = 5 * const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=creation_fee)
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "must finish paying its creation fee"):
                await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                )

        refreshed = await self.get_spot(spot_id)
        self.assertIsNone(refreshed[schema.SPOT_CANCELLATION_STARTED_AT])

    async def test_confirmed_creation_fee_is_retained_and_not_refunded(self):
        creation_fee = 5 * const.LUNA_PER_NIM
        cancellation_fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=creation_fee)
        spot = await self.get_spot(spot_id)
        required = db_access.spot_required_deposit_amount(spot)
        await self.create_deposit(spot_id, required)
        await self.create_creation_fee_transaction(
            spot_id,
            status=const.TRANS_STATUS_CONFIRMED,
        )

        with mock.patch.object(
            trans_updater,
            "submit_platform_fee_transaction",
            mock.AsyncMock(return_value={"ok": True, "trans_id": 21}),
        ), mock.patch.object(
            trans_updater,
            "submit_spot_refund_transaction",
            mock.AsyncMock(return_value={"ok": True, "trans_id": 22}),
        ) as refund_send:
            async with schema.get_db() as db:
                result = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=cancellation_fee,
                )

        self.assertEqual(result["confirmed_creation_fee_total"], creation_fee)
        self.assertEqual(result["remaining_cancellable_total"], spot[schema.SPOT_TOTAL_VALUE])
        self.assertEqual(result["refund_amount"], spot[schema.SPOT_TOTAL_VALUE] - cancellation_fee)
        self.assertEqual(refund_send.await_args.kwargs["amount"], spot[schema.SPOT_TOTAL_VALUE] - cancellation_fee)

    async def test_pending_deposit_or_creation_fee_blocks_cancellation(self):
        spot_id = await self.create_standard_spot(fee=const.LUNA_PER_NIM)
        await self.create_deposit(
            spot_id,
            10 * const.LUNA_PER_NIM,
            status=const.TRANS_STATUS_PENDING,
        )
        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "pending deposit"):
                await trans_updater.submit_spot_cancellation_transactions(db, spot_id=spot_id)

        second_id = await self.create_standard_spot(fee=const.LUNA_PER_NIM, title="Fee Pending")
        second = await self.get_spot(second_id)
        await self.create_deposit(second_id, db_access.spot_required_deposit_amount(second))
        await self.create_creation_fee_transaction(second_id)
        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "pending cancellation, refund, fee, or reward"):
                await trans_updater.submit_spot_cancellation_transactions(db, spot_id=second_id)


    async def test_creation_fee_intent_rechecks_cancellation_marker_inside_write_transaction(self):
        fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee, title="Race Guard")
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                await db_access.mark_spot_cancellation_started(db, spot_id=spot_id)
            with self.assertRaisesRegex(ValueError, "cancellation has started"):
                async with db_access.transaction(db, immediate=True):
                    await db_access.create_spot_creation_fee_transaction(
                        db,
                        user_id=self.owner_id,
                        spot_id=spot_id,
                        amount=fee,
                        from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                        to_address=str(spot[schema.SPOT_CREATION_FEE_ADDRESS]),
                        tx_hash="must-not-be-created",
                    )

    async def test_cancelling_draft_cannot_be_edited_or_funded_again(self):
        spot_id = await self.create_standard_spot(fee=0, title="Locked Draft")
        spot = await self.get_spot(spot_id)
        async with schema.get_db() as db:
            async with db_access.transaction(db, immediate=True):
                await db_access.mark_spot_cancellation_started(db, spot_id=spot_id)

            with self.assertRaisesRegex(ValueError, "cannot be edited"):
                await db_access.modify_draft_spot(db, spot_id=spot_id, desc="Too late")
            with self.assertRaisesRegex(ValueError, "being cancelled"):
                await trans_updater.record_spot_deposit_transaction(
                    db,
                    user_id=self.owner_id,
                    spot_id=spot_id,
                    amount=const.LUNA_PER_NIM,
                    from_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                    to_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                    tx_hash="late-deposit",
                )

    async def test_draft_cancellation_finalizes_after_refund_and_fee_confirm(self):
        spot_id = await self.create_standard_spot(fee=0, title="Finalise Draft")
        deposit = 10 * const.LUNA_PER_NIM
        cancellation_fee = const.LUNA_PER_NIM
        await self.create_deposit(spot_id, deposit)
        spot = await self.get_spot(spot_id)

        async with schema.get_db() as db:
            await db_access.mark_spot_cancellation_started(db, spot_id=spot_id)
            fee_id = await db_access.create_platform_fee_transaction(
                db,
                user_id=self.owner_id,
                spot_id=spot_id,
                amount=cancellation_fee,
                from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                to_address=const.DEV_PLATFORM_FEE_ADDRESS,
                tx_hash="confirmed-cancel-fee",
            )
            refund_id = await db_access.create_spot_refund_transaction(
                db,
                user_id=self.owner_id,
                spot_id=spot_id,
                amount=deposit - cancellation_fee,
                from_address=str(spot[schema.SPOT_DEPOSIT_ADDRESS]),
                to_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                tx_hash="confirmed-draft-refund",
            )
            await db_access.set_transaction_status_to_confirmed(db, trans_id=fee_id, block_number=10)
            await db_access.set_transaction_status_to_confirmed(db, trans_id=refund_id, block_number=11)
            await db.commit()

            self.assertTrue(
                await trans_updater._finalize_cancelled_spot_if_ready(
                    db,
                    spot_id=spot_id,
                )
            )
            await db.commit()
            final_spot = await db_access.get_spot(db, spot_id=spot_id)

        self.assertEqual(final_spot[schema.SPOT_STATUS], const.SPOT_STATUS_CANCELLED)

    async def test_any_deposit_history_blocks_delete_and_failed_only_draft_is_archived(self):
        funded_id = await self.create_standard_spot(fee=0)
        await self.create_deposit(funded_id, const.LUNA_PER_NIM)
        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "deposit history cannot be deleted"):
                await db_access.delete_draft_spot(db, spot_id=funded_id)

        failed_id = await self.create_standard_spot(fee=0, title="Failed Deposit")
        await self.create_deposit(
            failed_id,
            const.LUNA_PER_NIM,
            status=const.TRANS_STATUS_FAILED,
        )
        async with schema.get_db() as db:
            with self.assertRaisesRegex(ValueError, "deposit history cannot be deleted"):
                await db_access.delete_draft_spot(db, spot_id=failed_id)

            result = await trans_updater.submit_spot_cancellation_transactions(
                db,
                spot_id=failed_id,
            )
            final_spot = await db_access.get_spot(db, spot_id=failed_id)
            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=failed_id,
                limit=db_access.MAX_LIMIT,
            )

        self.assertTrue(result["cancelled"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["failed_deposit_count"], 1)
        self.assertEqual(result["refund_amount"], 0)
        self.assertEqual(final_spot[schema.SPOT_STATUS], const.SPOT_STATUS_CANCELLED)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0][schema.TRANS_STATUS], const.TRANS_STATUS_FAILED)


class CreationFeeDepositApiTests(SpotCreationFeeFixture):
    async def test_deposit_submission_can_record_a_safe_partial_amount(self):
        spot_id = await self.create_standard_spot(fee=const.LUNA_PER_NIM, title="Part Funding")
        partial_amount = 25 * const.LUNA_PER_NIM
        user = {schema.USER_ID: self.owner_id}

        with mock.patch.object(
            public_html,
            "_identify_private_page_user",
            mock.AsyncMock(return_value=(user, {"test_user": True}, 200)),
        ), mock.patch.object(
            public_html.cache,
            "notify_transaction_changed",
            mock.AsyncMock(),
        ):
            response = await public_html.my_spots_deposit_submitted_api(
                spot_id,
                public_html.DepositSubmittedRequest(
                    wallet_available=False,
                    tx_hash="partial-api-deposit",
                    from_address="NQ00 NIMHUNT DEV FUNDING WALLET",
                    amount=partial_amount,
                ),
            )

        self.assertEqual(response.status_code, 200)
        async with schema.get_db() as db:
            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=db_access.MAX_LIMIT,
            )
        deposits = [
            row
            for row in transactions
            if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_FILL_SPOT
        ]
        self.assertEqual(len(deposits), 1)
        self.assertEqual(deposits[0][schema.TRANS_AMOUNT], partial_amount)
        self.assertEqual(deposits[0][schema.TRANS_STATUS], const.TRANS_STATUS_PENDING)


class CreationFeeMockDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_seed_applies_creation_fee_before_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/records.db"
            old_path = schema.DB_PATH
            schema.DB_PATH = path
            try:
                summary = await spoof.seed_mock_data()
                self.assertEqual(summary["published_spot_count"], 8)
                with closing(sqlite3.connect(path)) as db:
                    published_without_fee = db.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {schema.SPOT_TABLE_NAME} s
                        WHERE s.{schema.SPOT_STATUS} = ?
                          AND s.{schema.SPOT_CREATION_FEE} > 0
                          AND NOT EXISTS (
                              SELECT 1
                              FROM {schema.TRANS_TABLE_NAME} t
                              WHERE t.{schema.TRANS_SPOT_ID} = s.{schema.SPOT_ID}
                                AND t.{schema.TRANS_TYPE} = ?
                                AND t.{schema.TRANS_STATUS} = ?
                          )
                        """,
                        (
                            const.SPOT_STATUS_PUBLISHED,
                            const.TRANS_TYPE_CREATION_FEE,
                            const.TRANS_STATUS_CONFIRMED,
                        ),
                    ).fetchone()[0]
                self.assertEqual(published_without_fee, 0)
            finally:
                schema.DB_PATH = old_path



class CreationFeePresentationTests(unittest.TestCase):
    def test_deposit_summary_separates_spot_value_and_creation_fee(self):
        summary = public_html._deposit_summary(
            total_value=100 * const.LUNA_PER_NIM,
            creation_fee=2 * const.LUNA_PER_NIM,
            creation_fee_address=const.DEV_PLATFORM_FEE_ADDRESS,
            transactions=[],
        )
        self.assertEqual(summary["spot_value"], 100 * const.LUNA_PER_NIM)
        self.assertEqual(summary["creation_fee"], 2 * const.LUNA_PER_NIM)
        self.assertEqual(summary["required_total"], 102 * const.LUNA_PER_NIM)
        self.assertEqual(summary["amount_due"], 102 * const.LUNA_PER_NIM)
        self.assertFalse(summary["funding_complete"])
        self.assertFalse(summary["fee_paid"])

    def test_deposit_summary_reports_processing_until_fee_confirms(self):
        total = 100 * const.LUNA_PER_NIM
        fee = const.LUNA_PER_NIM
        transactions = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: total + fee,
            },
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CREATION_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
                schema.TRANS_AMOUNT: fee,
            },
        ]
        summary = public_html._deposit_summary(
            total_value=total,
            creation_fee=fee,
            creation_fee_address=const.DEV_PLATFORM_FEE_ADDRESS,
            transactions=transactions,
        )
        self.assertEqual(summary["status"], "processing")
        self.assertEqual(summary["status_label"], "Creation Fee Processing")
        self.assertTrue(summary["funding_complete"])
        self.assertFalse(summary["fee_paid"])
        self.assertEqual(summary["fee_status"], "pending")
        self.assertEqual(summary["pending_fee_amount"], fee)


    def test_cancellation_summary_flags_failed_deposits_for_manual_review(self):
        amount = 7 * const.LUNA_PER_NIM
        summary = public_html._cancellation_summary(
            [
                {
                    schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                    schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
                    schema.TRANS_AMOUNT: amount,
                }
            ]
        )
        self.assertTrue(summary["manual_review_required"])
        self.assertEqual(summary["failed_deposit_count"], 1)
        self.assertEqual(summary["failed_deposit_amount"], amount)
        self.assertEqual(summary["remaining_amount"], 0)

    def test_confirmed_fee_to_wrong_address_does_not_unlock_publishing_ui(self):
        total = 100 * const.LUNA_PER_NIM
        fee = const.LUNA_PER_NIM
        deposit_address = "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"
        summary = public_html._deposit_summary(
            total_value=total,
            creation_fee=fee,
            deposit_address=deposit_address,
            creation_fee_address=const.DEV_PLATFORM_FEE_ADDRESS,
            transactions=[
                {
                    schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                    schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                    schema.TRANS_AMOUNT: total + fee,
                },
                {
                    schema.TRANS_TYPE: const.TRANS_TYPE_CREATION_FEE,
                    schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                    schema.TRANS_AMOUNT: fee,
                    schema.TRANS_FROM_ADDRESS: deposit_address,
                    schema.TRANS_TO_ADDRESS: "NQ52 J5R7 4U5Y 5XDL YKJ2 96ME 3AQ9 V7DP 8MX8",
                },
            ],
        )
        self.assertTrue(summary["funding_complete"])
        self.assertFalse(summary["fee_paid"])
        self.assertEqual(summary["matching_confirmed_fee_amount"], 0)
        self.assertEqual(summary["fee_status"], "verification_mismatch")
        self.assertEqual(summary["status"], "processing")

    def test_pending_deposit_is_counted_and_blocks_a_second_full_request(self):
        total = 100 * const.LUNA_PER_NIM
        pending = 40 * const.LUNA_PER_NIM
        summary = public_html._deposit_summary(
            total_value=total,
            creation_fee=0,
            transactions=[
                {
                    schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                    schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
                    schema.TRANS_AMOUNT: pending,
                }
            ],
        )
        self.assertEqual(summary["pending_amount"], pending)
        self.assertEqual(summary["amount_due"], total - pending)
        self.assertTrue(summary["has_pending"])


if __name__ == "__main__":
    unittest.main()
