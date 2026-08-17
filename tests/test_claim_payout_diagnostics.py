from __future__ import annotations

import contextlib
import unittest
from unittest import mock

import claim_payout_diagnostics
import database as schema
import funding_flow


CLAIM_TARGET = "NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"
VERIFIED_WALLET = "NQ48 LH6Q 7PFD LJYF 7PGB NJXL F8CX GHTJ YEKG"


class ClaimPayoutDiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_security_hold_reasons_without_exposing_claim_details(self):
        fake_db = object()

        @contextlib.asynccontextmanager
        async def fake_get_db():
            yield fake_db

        async def fake_get_claim(_db, *, claim_id):
            return {
                schema.CLAIM_ID: int(claim_id),
                schema.CLAIM_CLAIMED_AT: 1_000 if int(claim_id) == 11 else 1_500,
            }

        async def fake_decision(_db, *, claim_id):
            if int(claim_id) == 11:
                return {"allow": False, "reason": "security_record_missing", "manual_review": True}
            return {"allow": False, "reason": "security_hold", "retry_at": 2_200}

        settlement_status = {
            "last_result": {
                "standard_claim_payouts": {
                    "checked_count": 2,
                    "submitted_count": 0,
                    "failed_count": 0,
                    "results": [
                        {
                            "ok": True,
                            "claim_id": 11,
                            "paid": False,
                            "deferred": True,
                            "reason": "security_record_missing",
                        },
                        {
                            "ok": True,
                            "claim_id": 12,
                            "paid": False,
                            "deferred": True,
                            "reason": "security_hold",
                        },
                    ],
                }
            }
        }

        with (
            mock.patch.object(claim_payout_diagnostics, "get_db", fake_get_db),
            mock.patch.object(
                claim_payout_diagnostics.db_access,
                "get_unixepoch",
                mock.AsyncMock(return_value=2_000),
            ),
            mock.patch.object(
                claim_payout_diagnostics.db_access,
                "get_unpaid_successful_standard_claim_ids",
                mock.AsyncMock(return_value=[11, 12]),
            ),
            mock.patch.object(
                claim_payout_diagnostics.db_access,
                "get_claim",
                fake_get_claim,
            ),
            mock.patch.object(
                claim_payout_diagnostics.claim_security,
                "_payout_security_decision",
                fake_decision,
            ),
            mock.patch.object(
                claim_payout_diagnostics,
                "_latest_confirmed_standard_payout_comparison",
                mock.AsyncMock(return_value={"present": False}),
            ),
            mock.patch.object(
                claim_payout_diagnostics.settlement_updater,
                "settlement_refresher_status",
                return_value=settlement_status,
            ),
            mock.patch.object(
                claim_payout_diagnostics.claim_security,
                "PAYOUT_HOLD_SECONDS",
                1_200,
            ),
        ):
            result = await claim_payout_diagnostics.claim_payout_diagnostics()

        self.assertEqual(result["effective_security_hold_seconds"], 1_200)
        self.assertEqual(result["unpaid_successful_standard_count"], 2)
        self.assertEqual(result["oldest_unpaid_successful_standard_age_seconds"], 1_000)
        self.assertEqual(
            result["security_decision_counts"],
            {"security_hold": 1, "security_record_missing": 1},
        )
        self.assertEqual(
            result["last_standard_pass"]["result_reason_counts"],
            {"security_hold": 1, "security_record_missing": 1},
        )
        self.assertEqual(result["latest_confirmed_standard_payout"], {"present": False})

        rendered = repr(result).lower()
        self.assertNotIn("wallet_address", rendered)
        self.assertNotIn("device_id", rendered)
        self.assertNotIn("payout_address", rendered)
        self.assertNotIn("ip_hash", rendered)
        self.assertNotIn("tx_hash", rendered)

    async def test_latest_confirmed_payout_compares_addresses_without_exposing_values(self):
        fake_db = object()
        latest = {
            "claim_id": 42,
            "transaction_recipient": VERIFIED_WALLET,
            "settled_at": 1_900,
        }
        claim = {
            schema.CLAIM_ID: 42,
            schema.CLAIM_PAYOUT_ADDRESS: CLAIM_TARGET,
        }
        security_record = {
            "payout_address": CLAIM_TARGET,
            "verified_wallet": VERIFIED_WALLET,
        }

        with (
            mock.patch.object(
                claim_payout_diagnostics,
                "_latest_confirmed_standard_payout",
                mock.AsyncMock(return_value=latest),
            ),
            mock.patch.object(
                claim_payout_diagnostics.db_access,
                "get_claim",
                mock.AsyncMock(return_value=claim),
            ),
            mock.patch.object(
                claim_payout_diagnostics.claim_security,
                "_metadata_get",
                mock.AsyncMock(return_value=security_record),
            ),
        ):
            result = await claim_payout_diagnostics._latest_confirmed_standard_payout_comparison(
                fake_db,
                now=2_000,
            )

        self.assertTrue(result["present"])
        self.assertTrue(result["claim_record_present"])
        self.assertTrue(result["security_record_present"])
        self.assertEqual(result["age_seconds"], 100)
        self.assertFalse(result["transaction_recipient_matches_claim_target"])
        self.assertTrue(result["claim_target_matches_security_target"])
        self.assertFalse(result["claim_target_matches_verified_wallet"])
        self.assertTrue(result["transaction_recipient_matches_verified_wallet"])

        rendered = repr(result)
        self.assertNotIn(CLAIM_TARGET, rendered)
        self.assertNotIn(VERIFIED_WALLET, rendered)

    async def test_funding_flow_includes_claim_payout_diagnostics(self):
        base = {"network": "MainAlbatross", "pending_count": 0}
        claim_diag = {
            "effective_security_hold_seconds": 1_200,
            "unpaid_successful_standard_count": 1,
        }
        with (
            mock.patch.object(
                funding_flow.funding_monitor,
                "funding_flow_diagnostics",
                mock.AsyncMock(return_value=dict(base)),
            ),
            mock.patch.object(
                funding_flow,
                "claim_payout_diagnostics",
                mock.AsyncMock(return_value=dict(claim_diag)),
            ),
        ):
            result = await funding_flow.funding_flow_diagnostics()

        self.assertEqual(result["network"], "MainAlbatross")
        self.assertEqual(result["claim_payout_diagnostics"], claim_diag)


if __name__ == "__main__":
    unittest.main()
