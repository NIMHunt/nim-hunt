from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

import claim_security_response_delivery


class ClaimSecurityResponseDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_response_is_forwarded_before_background_work_starts(self):
        events: list[str] = []
        client_messages: list[dict] = []

        async def app(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps({"ok": True, "claim": {"id": 7}}).encode(),
                    "more_body": False,
                }
            )
            events.append("background-start")
            await asyncio.sleep(0)
            events.append("background-finish")

        async def original_guard(wrapped_app, scope, receive, send):
            captured: list[dict] = []

            async def capture(message):
                captured.append(message)

            await wrapped_app(scope, receive, capture)
            events.append("security-recorded")
            for message in captured:
                await send(message)
            events.append("response-forwarded")
            return True

        async def client_send(message):
            client_messages.append(message)
            if message.get("type") == "http.response.body":
                events.append("client-body")

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        with mock.patch.object(
            claim_security_response_delivery,
            "_ORIGINAL_GUARD",
            original_guard,
        ):
            consumed = await claim_security_response_delivery.guard_http_request_with_response_delivery(
                app,
                {"type": "http", "method": "POST", "path": "/api/spot/1/claim"},
                receive,
                client_send,
            )

        self.assertTrue(consumed)
        self.assertEqual(len(client_messages), 2)
        self.assertLess(events.index("security-recorded"), events.index("client-body"))
        self.assertLess(events.index("client-body"), events.index("background-start"))
        self.assertLess(events.index("background-start"), events.index("background-finish"))

    async def test_background_exception_cannot_erase_completed_response(self):
        client_messages: list[dict] = []

        async def app(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"ok":true}',
                    "more_body": False,
                }
            )
            raise RuntimeError("settlement exploded")

        async def original_guard(wrapped_app, scope, receive, send):
            captured: list[dict] = []

            async def capture(message):
                captured.append(message)

            await wrapped_app(scope, receive, capture)
            for message in captured:
                await send(message)
            return True

        async def client_send(message):
            client_messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        with mock.patch.object(
            claim_security_response_delivery,
            "_ORIGINAL_GUARD",
            original_guard,
        ):
            with self.assertLogs(claim_security_response_delivery.logger, level="ERROR"):
                consumed = await claim_security_response_delivery.guard_http_request_with_response_delivery(
                    app,
                    {"type": "http", "method": "POST", "path": "/api/claim/1/detail"},
                    receive,
                    client_send,
                )

        self.assertTrue(consumed)
        self.assertEqual([message["type"] for message in client_messages], [
            "http.response.start",
            "http.response.body",
        ])

    async def test_app_failure_before_response_still_propagates(self):
        async def app(_scope, _receive, _send):
            raise RuntimeError("handler failed before response")

        async def original_guard(wrapped_app, scope, receive, send):
            await wrapped_app(scope, receive, send)
            return True

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            self.fail("No response should have been sent")

        with mock.patch.object(
            claim_security_response_delivery,
            "_ORIGINAL_GUARD",
            original_guard,
        ):
            with self.assertRaisesRegex(RuntimeError, "handler failed before response"):
                await claim_security_response_delivery.guard_http_request_with_response_delivery(
                    app,
                    {"type": "http", "method": "POST", "path": "/api/spot/1/claim"},
                    receive,
                    send,
                )


if __name__ == "__main__":
    unittest.main()
