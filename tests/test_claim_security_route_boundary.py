from __future__ import annotations

import json
import unittest
from unittest import mock

import claim_security
import social_preview


class ClaimSecurityRouteBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def _scope(self, path: str, *, method: str = "POST") -> dict:
        return {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 12345),
        }

    async def _run(self, path: str, *, guard_result: bool = False):
        app_called = False
        messages: list[dict] = []
        received = False

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"{}", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        middleware = social_preview.SocialPreviewMiddleware(app)
        guard = mock.AsyncMock(return_value=guard_result)
        with mock.patch.object(claim_security, "guard_http_request", guard):
            await middleware(self._scope(path), receive, send)

        return app_called, messages, guard

    async def test_noncanonical_claim_route_ids_fail_closed_before_security_guard(self):
        paths = (
            "/api/spot/01/claim",
            "/api/spot/+1/claim",
            "/api/spot/0/claim",
            "/api/spot/-1/claim",
            "/api/spot/not-an-int/claim",
            "/api/claim/01/detail",
            "/api/claim/+1/detail",
            "/api/claim/01/location",
            "/api/claim/+1/location",
        )

        for path in paths:
            with self.subTest(path=path):
                app_called, messages, guard = await self._run(path)
                self.assertFalse(app_called)
                guard.assert_not_awaited()

                start = next(
                    message for message in messages if message["type"] == "http.response.start"
                )
                body = b"".join(
                    message.get("body", b"")
                    for message in messages
                    if message["type"] == "http.response.body"
                )
                self.assertEqual(start["status"], 422)
                self.assertEqual(json.loads(body)["code"], "invalid_resource_id")

    async def test_canonical_claim_route_ids_still_enter_security_guard(self):
        for path in (
            "/api/spot/1/claim",
            "/api/spot/123/claim",
            "/api/claim/1/detail",
            "/api/claim/123/location",
        ):
            with self.subTest(path=path):
                app_called, messages, guard = await self._run(path, guard_result=True)
                self.assertFalse(app_called)
                self.assertEqual(messages, [])
                guard.assert_awaited_once()
                guarded_scope = guard.await_args.args[1]
                self.assertEqual(guarded_scope["path"], path)

    async def test_unrelated_post_is_not_mistaken_for_protected_claim_route(self):
        app_called, messages, guard = await self._run("/api/spot/01/report")
        self.assertTrue(app_called)
        guard.assert_awaited_once()
        start = next(message for message in messages if message["type"] == "http.response.start")
        self.assertEqual(start["status"], 204)


if __name__ == "__main__":
    unittest.main()
