from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

import cache
import claim_auth_abuse_guard
import claim_security
import constants as const
import database as schema
import social_preview


class ClaimAuthAbuseGuardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()

    def _scope(self):
        return {
            "type": "http",
            "method": "POST",
            "path": "/api/security/verify",
            "headers": [(b"x-real-ip", b"203.0.113.42")],
            "client": ("100.64.0.2", 443),
            "server": ("testserver", 443),
            "scheme": "https",
            "query_string": b"",
        }

    def _receive(self, body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return receive

    def _send_to(self, messages):
        async def send(message):
            messages.append(message)

        return send

    async def test_repeated_verify_attempt_is_rate_limited_before_inner_app(self):
        body = json.dumps({"device_id_hash": "a" * 64}).encode()
        app_calls = 0

        async def app(scope, receive, send):
            nonlocal app_calls
            app_calls += 1
            # Ensure the guard faithfully replayed the consumed body.
            message = await receive()
            self.assertEqual(message["body"], body)
            payload = b'{"ok":true}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": payload, "more_body": False})

        async def delegate(*args, **kwargs):
            self.fail("verify path should be consumed by the replay limiter")

        first_messages = []
        second_messages = []

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(claim_auth_abuse_guard, "VERIFY_RATE_LIMIT_PER_IP", 1),
            mock.patch.object(claim_auth_abuse_guard, "VERIFY_RATE_LIMIT_PER_DEVICE", 1),
            mock.patch.object(claim_auth_abuse_guard, "_DELEGATE", delegate),
        ):
            consumed = await claim_auth_abuse_guard.guard_http_request_with_verify_rate_limit(
                app,
                self._scope(),
                self._receive(body),
                self._send_to(first_messages),
            )
            consumed_again = await claim_auth_abuse_guard.guard_http_request_with_verify_rate_limit(
                app,
                self._scope(),
                self._receive(body),
                self._send_to(second_messages),
            )

        self.assertTrue(consumed)
        self.assertTrue(consumed_again)
        self.assertEqual(app_calls, 1)
        self.assertEqual(first_messages[0]["status"], 200)
        self.assertEqual(second_messages[0]["status"], 429)

    def test_non_verify_path_is_not_claimed_by_this_layer(self):
        scope = self._scope()
        scope["path"] = "/api/spot/1/claim"
        self.assertFalse(claim_auth_abuse_guard._verify_path(scope))

    async def test_twenty_devices_on_shared_wifi_pass_through_middleware(self):
        calls = []

        async def app(scope, receive, send):
            calls.append((scope["path"], json.loads((await receive())["body"])))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

        async def delegate(inner, scope, receive, send):
            await inner(scope, receive, send)
            return True

        middleware = social_preview.SocialPreviewMiddleware(app)
        statuses = []
        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(claim_auth_abuse_guard, "_DELEGATE", delegate),
            mock.patch.object(claim_security, "guard_http_request", claim_auth_abuse_guard.guard_http_request_with_verify_rate_limit),
        ):
            for index in range(20):
                device = f"{index:064x}"
                for path in ("/api/security/challenge", "/api/security/verify"):
                    scope = self._scope()
                    scope["path"] = path
                    messages = []
                    await middleware(
                        scope,
                        self._receive(json.dumps({"device_id_hash": device}).encode()),
                        self._send_to(messages),
                    )
                    statuses.append(next(m["status"] for m in messages if m["type"] == "http.response.start"))

        self.assertEqual(statuses, [200] * 40)
        self.assertEqual(len(calls), 40)


if __name__ == "__main__":
    unittest.main()
