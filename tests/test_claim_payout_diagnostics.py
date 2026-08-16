from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest import mock

import claim_payout_diagnostics
import constants as const
import database as schema
import funding_flow


class ClaimPayoutDiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_security_hold_reasons_without_exposing_claim_details(self):
        fake_db = object()

        @asynccontextmanager
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

        rendered = repr(result).lower()
        self.assertNotIn("wallet_address", rendered)
        self.assertNotIn("device_id", rendered)
        self.assertNotIn("payout_address", rendered)
        self.assertNotIn("ip_hash", rendered)

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
