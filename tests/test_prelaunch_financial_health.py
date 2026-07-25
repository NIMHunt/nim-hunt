from __future__ import annotations

import json
import unittest
from unittest import mock

import main


class FinancialWorkerHealthTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _diagnostics(*, refresher_running: bool = True, settlement_running: bool = True):
        return {
            "local_intent_count": 0,
            "refresher": {
                "running": refresher_running,
                "healthy": True,
            },
            "settlement": {
                "running": settlement_running,
                "healthy": True,
            },
        }

    async def _response_for(self, diagnostics):
        with mock.patch.object(
            main,
            "funding_flow_diagnostics",
            mock.AsyncMock(return_value=diagnostics),
        ):
            return await main.transaction_healthz()

    async def test_transaction_health_fails_when_settlement_worker_is_stopped(self):
        response = await self._response_for(
            self._diagnostics(settlement_running=False),
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.body)["ok"])

    async def test_transaction_health_fails_when_transaction_refresher_is_stopped(self):
        response = await self._response_for(
            self._diagnostics(refresher_running=False),
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.body)["ok"])
