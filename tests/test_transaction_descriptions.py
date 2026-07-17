import json
import unittest
from contextlib import asynccontextmanager
from unittest import mock

import constants as const
import database as schema
import public_html
import trans_updater
from transaction_descriptions import build_transaction_description


class TransactionDescriptionFormattingTest(unittest.TestCase):
    def test_short_descriptions_keep_requested_structure(self):
        self.assertEqual(build_transaction_description("Funding", "Town Drop"), "Funding: Town Drop")
        self.assertEqual(build_transaction_description("Claim", "Town Drop"), "Claim: Town Drop")
        self.assertEqual(build_transaction_description("Prizedraw", "Town Drop"), "Prizedraw: Town Drop")
        self.assertEqual(build_transaction_description("Refund Fee", "Town Drop"), "Refund Fee: Town Drop")
        self.assertEqual(build_transaction_description("Cancelled Spot", "Town Drop"), "Cancelled Spot: Town Drop")

    def test_long_unicode_description_is_valid_and_within_byte_limit(self):
        description = build_transaction_description("Prizedraw", "龍の広場龍の広場龍の広場")
        self.assertLessEqual(
            len(description.encode("utf-8")),
            const.NIMIQ_TRANSACTION_DESCRIPTION_MAX_BYTES,
        )
        self.assertTrue(description.startswith("Prizedraw: "))
        self.assertTrue(description.endswith("..."))

    def test_whitespace_is_normalised(self):
        self.assertEqual(
            build_transaction_description("Cancelled   Spot", "  Hill   Top  "),
            "Cancelled Spot: Hill Top",
        )


class FundingDescriptionApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_deposit_intent_returns_funding_description(self):
        spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_TITLE: "Town Drop",
            schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
            schema.SPOT_TOTAL_VALUE: 100,
            schema.SPOT_DEPOSIT_ADDRESS: "deposit-address",
        }
        user = {schema.USER_ID: 2}

        @asynccontextmanager
        async def fake_get_db():
            yield object()

        @asynccontextmanager
        async def fake_transaction(_db):
            yield

        with (
            mock.patch.object(public_html, "get_db", fake_get_db),
            mock.patch.object(public_html.db_access, "transaction", fake_transaction),
            mock.patch.object(
                public_html,
                "_identify_private_page_user",
                mock.AsyncMock(return_value=(user, {"test_user": False}, 200)),
            ),
            mock.patch.object(
                public_html.db_access,
                "is_spot_owned_by_user",
                mock.AsyncMock(return_value=True),
            ),
            mock.patch.object(public_html.db_access, "get_spot", mock.AsyncMock(return_value=spot)),
            mock.patch.object(
                public_html.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[]),
            ),
            mock.patch.object(
                public_html,
                "_deposit_summary",
                return_value={"pending_amount": 0, "amount_due": 100},
            ),
        ):
            response = await public_html.my_spots_deposit_intent_api(
                7,
                public_html.HomeSessionRequest(wallet_available=False),
            )

        payload = json.loads(response.body)
        self.assertEqual(payload["transaction_description"], "Funding: Town Drop")


class TransactionDescriptionSubmissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_standard_claim_uses_claim_description(self):
        claim = {
            schema.CLAIM_SPOT_ID: 7,
            schema.CLAIM_RECIPIENT: 2,
            schema.CLAIM_PAYOUT_ADDRESS: "recipient",
        }
        spot = {schema.SPOT_ID: 7, schema.SPOT_TITLE: "Town Drop"}
        submit = mock.AsyncMock(return_value={"ok": True, "trans_id": 1})
        with (
            mock.patch.object(trans_updater.db_access, "get_claim", mock.AsyncMock(return_value=claim)),
            mock.patch.object(
                trans_updater.db_access,
                "has_nonfailed_claim_payout_transaction",
                mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)),
            mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=False)),
            mock.patch.object(trans_updater, "_submit_recorded_chain_send", submit),
        ):
            await trans_updater.submit_claim_reward_transaction(object(), claim_id=11, amount=100)

        self.assertEqual(submit.await_args.kwargs["memo"], "Claim: Town Drop")

    async def test_prizedraw_claim_uses_prizedraw_description(self):
        claim = {
            schema.CLAIM_SPOT_ID: 7,
            schema.CLAIM_RECIPIENT: 2,
            schema.CLAIM_PAYOUT_ADDRESS: "recipient",
        }
        spot = {schema.SPOT_ID: 7, schema.SPOT_TITLE: "Town Drop"}
        submit = mock.AsyncMock(return_value={"ok": True, "trans_id": 1})
        with (
            mock.patch.object(trans_updater.db_access, "get_claim", mock.AsyncMock(return_value=claim)),
            mock.patch.object(
                trans_updater.db_access,
                "has_nonfailed_claim_payout_transaction",
                mock.AsyncMock(return_value=False),
            ),
            mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)),
            mock.patch.object(trans_updater.db_access, "is_prizedraw", mock.AsyncMock(return_value=True)),
            mock.patch.object(trans_updater, "_submit_recorded_chain_send", submit),
        ):
            await trans_updater.submit_claim_reward_transaction(object(), claim_id=11, amount=100)

        self.assertEqual(submit.await_args.kwargs["memo"], "Prizedraw: Town Drop")

    async def test_cancellation_legs_use_distinct_descriptions(self):
        spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_TITLE: "Town Drop",
            schema.SPOT_CREATED_BY: 1,
        }
        submit = mock.AsyncMock(return_value={"ok": True, "trans_id": 1})
        with (
            mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)),
            mock.patch.object(trans_updater, "_submit_recorded_chain_send", submit),
        ):
            await trans_updater.submit_platform_fee_transaction(
                object(),
                spot_id=7,
                amount=1,
                fee_address="fee",
            )
            fee_memo = submit.await_args.kwargs["memo"]

            await trans_updater.submit_spot_refund_transaction(
                object(),
                spot_id=7,
                to_address="owner",
                amount=2,
            )
            refund_memo = submit.await_args.kwargs["memo"]

        self.assertEqual(fee_memo, "Refund Fee: Town Drop")
        self.assertEqual(refund_memo, "Cancelled Spot: Town Drop")


if __name__ == "__main__":
    unittest.main()
