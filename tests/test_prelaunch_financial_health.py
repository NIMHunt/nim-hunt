from __future__ import annotations

import json
import unittest
from unittest import mock

import main


class FinancialWorkerHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_transaction_health_fails_when_settlement_worker_is_stopped(self):
        diagnostics = {
            "local_intent_count": 0,
            "refresher": {
                "running": True,
                "healthy": True,
            },
            "settlement": {
                "running": False,
                "healthy": True,
            },
        }
        with mock.patch.object(
            main,
            "funding_flow_diagnostics",
            mock.AsyncMock(return_value=diagnostics),
        ):
            response = await main.transaction_healthz()

        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.body)["ok"])
