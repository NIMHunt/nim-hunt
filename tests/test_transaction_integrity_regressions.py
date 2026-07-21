from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import constants as const
import database as schema
import db_access
import public_html
import trans_updater

BASIC_FROM = "NQ88 MK32 JK09 4U4P 2QXU D4BY JHCU C0AB SKB4"
BASIC_TO = "NQ54 FTGY F6VJ EJPU NSMN RA5Q 0K21 8EQT Q05P"
TX_HASH = "cf41b77531dd7fc5b3ae0ba26d1f95bd8eff6ffe6df612dcf01a0f50ba7a2692"


class TransactionExecutionResultTest(unittest.TestCase):
    def test_nested_failed_execution_is_never_verified(self):
        raw = {
            "data": {
                "transaction": {
                    "sender": BASIC_FROM,
                    "recipient": BASIC_TO,
                    "value": 39_900_000,
                    "executionResult": False,
                }
            }
        }
        self.assertTrue(trans_updater._execution_result_is_failure(raw))
        trans = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_FROM_ADDRESS: BASIC_FROM,
            schema.TRANS_TO_ADDRESS: BASIC_TO,
            schema.TRANS_AMOUNT: 39_900_000,
        }
        status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=TX_HASH,
            raw=raw,
        )
        verified = trans_updater._verify_chain_details_for_record(trans, status)
        self.assertFalse(verified.ok)
        self.assertIn("failed execution", str(verified.reason))

    def test_nested_success_is_not_mistaken_for_failure(self):
        self.assertFalse(
            trans_updater._execution_result_is_failure(
                {"data": {"transaction": {"executionResult": True}}}
            )
        )


class CreatorFundingPresentationTest(unittest.TestCase):
    def test_full_combined_deposit_is_ready_while_fee_is_reconciling(self):
        total_value = 40_000_000
        creation_fee = 100_000
        transactions = [{
            schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
            schema.TRANS_AMOUNT: total_value + creation_fee,
            schema.TRANS_CREATED_AT: 1,
        }]
        summary = public_html._deposit_summary(
            transactions,
            total_value=total_value,
            creation_fee=creation_fee,
            deposit_address=BASIC_FROM,
            creation_fee_address=BASIC_TO,
        )
        self.assertTrue(summary["funding_complete"])
        self.assertFalse(summary["fee_paid"])
        self.assertEqual(summary["fee_status"], "preparing")
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["status_label"], "Ready")

    def test_refund_summary_exposes_actual_basic_destination(self):
        transactions = [
            {
                schema.TRANS_ID: 1,
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 40_100_000,
            },
            {
                schema.TRANS_ID: 2,
                schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 100_000,
            },
            {
                schema.TRANS_ID: 3,
                schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 39_900_000,
                schema.TRANS_TO_ADDRESS: BASIC_TO,
                schema.TRANS_TX_HASH: TX_HASH,
                schema.TRANS_BLOCK_NUMBER: 6_609_204,
                schema.TRANS_CREATED_AT: 3,
            },
        ]
        summary = public_html._cancellation_summary(transactions)
        refund = summary["refund_transaction"]
        self.assertIsNotNone(refund)
        self.assertEqual(refund["status"], "confirmed")
        self.assertEqual(refund["to_address"], BASIC_TO)
        self.assertEqual(refund["tx_hash"], TX_HASH)


class PublishRuleTest(unittest.IsolatedAsyncioTestCase):
    async def test_internal_creation_fee_confirmation_does_not_block_publish(self):
        spot = {
            schema.SPOT_ID: 1,
            schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
            schema.SPOT_CREATED_BY: 7,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            schema.SPOT_TITLE: "Funded Spot",
            schema.SPOT_DEPOSIT_ADDRESS: BASIC_FROM,
            schema.SPOT_LAT: 55.0,
            schema.SPOT_LONG: -5.0,
            schema.SPOT_RADIUS: 200,
            schema.SPOT_MAX_CLAIMS_PER_USER: 1,
            schema.SPOT_MAX_TOTAL_CLAIMS: 1,
            schema.SPOT_TOTAL_VALUE: 10_000_000,
            schema.SPOT_CREATION_FEE: 100_000,
            schema.SPOT_STARTS_AT: None,
            schema.SPOT_ENDS_AT: 86_400,
            schema.SPOT_USE_PASSWORD: 0,
        }
        required = db_access.spot_required_deposit_amount(spot)
        with (
            patch.object(db_access, "get_spot", AsyncMock(return_value=spot)),
            patch.object(db_access, "can_user_create_spot", AsyncMock(return_value=True)),
            patch.object(db_access, "get_prizedraw", AsyncMock(return_value=None)),
            patch.object(db_access, "spot_meets_minimum_payout", AsyncMock(return_value=True)),
            patch.object(db_access, "get_confirmed_spot_deposit_total", AsyncMock(return_value=required)),
            patch.object(
                db_access,
                "has_confirmed_spot_creation_fee_transaction",
                AsyncMock(side_effect=AssertionError("internal fee must not be a publish prerequisite")),
            ),
        ):
            self.assertTrue(await db_access.can_publish_spot(object(), spot_id=1))

    async def test_fee_worker_selects_published_and_completed_spots(self):
        class FakeDb:
            def __init__(self):
                self.sql = ""
                self.params = ()

            async def execute_fetchall(self, sql, params):
                self.sql = sql
                self.params = params
                return []

        db = FakeDb()
        self.assertEqual(await db_access.get_spot_ids_ready_for_creation_fee(db), [])
        self.assertIn("IN (?, ?, ?)", db.sql)
        self.assertEqual(
            db.params[:3],
            (
                const.SPOT_STATUS_DRAFT,
                const.SPOT_STATUS_PUBLISHED,
                const.SPOT_STATUS_COMPLETED,
            ),
        )

    async def test_creation_fee_submission_accepts_published_spot(self):
        spot = {
            schema.SPOT_ID: 1,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            schema.SPOT_CREATION_FEE: 0,
        }
        with patch.object(db_access, "get_spot", AsyncMock(return_value=spot)):
            result = await trans_updater.submit_spot_creation_fee_transaction(object(), spot_id=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "zero_amount")


class ClaimStatusPresentationTest(unittest.TestCase):
    def test_elapsed_pending_duration_is_verifying(self):
        result = public_html._claim_display_status(
            claim={},
            spot={schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CLAIM_DURATION: 600},
            is_prizedraw=False,
            status_label="pending",
            payout={},
            duration_remaining=0,
        )
        self.assertEqual(result["text"], "Verifying")
        self.assertEqual(result["label"], "verifying")

    def test_success_waiting_for_payout_is_processing(self):
        result = public_html._claim_display_status(
            claim={},
            spot={schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CLAIM_DURATION: 600},
            is_prizedraw=False,
            status_label="success",
            payout={"payout_confirmed_count": 0},
            duration_remaining=0,
        )
        self.assertEqual(result["text"], "Success (Processing)")

    def test_confirmed_payout_is_plain_success(self):
        result = public_html._claim_display_status(
            claim={},
            spot={schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CLAIM_DURATION: 600},
            is_prizedraw=False,
            status_label="success",
            payout={"payout_confirmed_count": 1},
            duration_remaining=0,
        )
        self.assertEqual(result["text"], "Success")


class FrontendRegressionSourceTest(unittest.TestCase):
    def test_publish_failure_closes_confirmation_before_notice(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "my_spots.js").read_text()
        catch_start = source.index("async function confirmPublishSpot")
        catch_block = source[catch_start:source.index("function closeCancelModal", catch_start)]
        self.assertLess(
            catch_block.index("els.publishBackdrop.hidden = true;"),
            catch_block.index("showNotice({"),
        )

    def test_duration_success_does_not_append_verifying_suffix(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "claim_detail.js").read_text()
        self.assertNotIn("? ' (Verifying)'", source)
        self.assertIn("statusKeyword.textContent = 'Verifying';", source)
        self.assertIn("status === 'success' && Number(claim.payout_confirmed_count || 0) <= 0", source)

    def test_combined_code_duration_warning_is_present(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "find_spots.js").read_text()
        self.assertIn("codeUsedWhenVerificationStarts", source)


if __name__ == "__main__":
    unittest.main()
