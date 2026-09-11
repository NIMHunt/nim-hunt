from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI

import cache
import claim_auth_abuse_guard
import claim_security
import constants as const
import database as schema
import db_access
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
        async def delegate(inner, scope, receive, send):
            await inner(scope, receive, send)
            return True

        application = FastAPI()
        application.include_router(claim_security.router)
        middleware = social_preview.SocialPreviewMiddleware(application)

        async def post(path, payload):
            body = json.dumps(payload).encode()
            scope = self._scope()
            scope["path"] = path
            scope["raw_path"] = path.encode()
            scope["headers"].append((b"content-type", b"application/json"))
            messages = []
            await middleware(scope, self._receive(body), self._send_to(messages))
            status_code = next(
                item["status"] for item in messages if item["type"] == "http.response.start"
            )
            response_body = b"".join(
                item.get("body", b"")
                for item in messages
                if item["type"] == "http.response.body"
            )
            return status_code, json.loads(response_body)

        with (
            mock.patch.object(const, "PUBLIC_DEPLOYMENT", True),
            mock.patch.object(claim_auth_abuse_guard, "_DELEGATE", delegate),
            mock.patch.object(claim_security, "guard_http_request", claim_auth_abuse_guard.guard_http_request_with_verify_rate_limit),
            mock.patch.object(
                claim_security,
                "_verify_signature",
                new=mock.AsyncMock(
                    return_value="NQ45 1KUT 73F7 ADV4 UCT8 TX64 2DE4 CHBP SJBF"
                ),
            ),
        ):
            for index in range(20):
                device = f"{index:064x}"
                challenge_status, challenge = await post(
                    "/api/security/challenge", {"device_id_hash": device}
                )
                self.assertEqual(challenge_status, 200, challenge)
                verification_status, verification = await post(
                        "/api/security/verify",
                        {
                            "device_id_hash": device,
                            "challenge_id": challenge["challenge_id"],
                            "public_key": "d" * 64,
                            "signature": "e" * 128,
                        },
                    )
                self.assertEqual(verification_status, 200, verification)

        async with schema.get_db() as db:
            self.assertEqual(await db_access.count_users(db), 20)


if __name__ == "__main__":
    unittest.main()
