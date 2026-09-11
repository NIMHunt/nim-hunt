from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from unittest import mock

import main
import public_html
import request_body_limit
import social_preview


def _scope(*, content_length: int | None = None, path: str = "/api/test") -> dict:
    headers = [] if content_length is None else [(b"content-length", str(content_length).encode())]
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("test", 443),
    }


async def _invoke(app, scope: dict, chunks: list[bytes]) -> tuple[int, dict]:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(body)


class RequestBodyLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_limit_passes_and_one_byte_over_is_rejected(self):
        seen = []

        async def inner(_scope, receive, send):
            seen.append((await receive())["body"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

        app = request_body_limit.RequestBodyLimitMiddleware(inner, max_body_bytes=8)
        status, _ = await _invoke(app, _scope(content_length=8), [b"12345678"])
        self.assertEqual(status, 200)
        self.assertEqual(seen, [b"12345678"])

        status, body = await _invoke(app, _scope(content_length=9), [b"ignored"])
        self.assertEqual(status, 413)
        self.assertEqual(body["code"], "request_body_too_large")
        self.assertEqual(len(seen), 1)

    async def test_chunking_and_incorrect_or_missing_content_length_cannot_bypass_limit(self):
        inner = mock.AsyncMock()
        app = request_body_limit.RequestBodyLimitMiddleware(inner, max_body_bytes=8)
        for length in (None, 1):
            status, _ = await _invoke(app, _scope(content_length=length), [b"1234", b"56789"])
            self.assertEqual(status, 413)
        inner.assert_not_awaited()

    async def test_oversized_claim_stops_before_claim_security_or_application_work(self):
        inner = mock.AsyncMock()
        claim_guard = mock.AsyncMock(return_value=False)
        boundary = request_body_limit.RequestBodyLimitMiddleware(
            social_preview.SocialPreviewMiddleware(inner), max_body_bytes=8
        )
        with mock.patch.object(social_preview.claim_security, "guard_http_request", claim_guard):
            status, _ = await _invoke(
                boundary,
                _scope(path="/api/spot/1/claim"),
                [b'{"device', b'_id_hash":"wallet-and-database-work"}'],
            )
        self.assertEqual(status, 413)
        claim_guard.assert_not_awaited()
        inner.assert_not_awaited()

    def test_configured_application_default_is_64_kib(self):
        self.assertEqual(main.const.MAX_HTTP_REQUEST_BODY_BYTES, 64 * 1024)


class ReverseGeocodeResourceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        public_html._reverse_geocode_cache.clear()
        public_html._reverse_geocode_inflight.clear()
        public_html._reverse_geocode_failures = 0
        public_html._reverse_geocode_circuit_open_until = 0.0
        public_html._reverse_geocode_semaphore = asyncio.Semaphore(
            public_html.REVERSE_GEOCODE_MAX_CONCURRENCY
        )

    async def test_one_hundred_requests_never_exceed_outbound_concurrency(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def provider(_lat, _long):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return "City", "Country"

        with mock.patch.object(public_html, "_reverse_geocode_sync", provider):
            await asyncio.gather(*(
                public_html._derive_place_for_create_map(-49 + index, -100 + index)
                for index in range(100)
            ))
        self.assertLessEqual(maximum, public_html.REVERSE_GEOCODE_MAX_CONCURRENCY)

    async def test_nearby_requests_coalesce_and_then_use_cache(self):
        provider = mock.Mock(side_effect=lambda *_args: ("Edinburgh", "United Kingdom"))
        with mock.patch.object(public_html, "_reverse_geocode_sync", provider):
            first, second = await asyncio.gather(
                public_html._derive_place_for_create_map(55.95331, -3.18831),
                public_html._derive_place_for_create_map(55.95339, -3.18839),
            )
            third = await public_html._derive_place_for_create_map(55.95335, -3.18835)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(provider.call_count, 1)

    async def test_timeout_returns_promptly_without_releasing_worker_slot_early(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def slow(_lat, _long):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return "Recovered", "Country"

        with (
            mock.patch.object(public_html, "_reverse_geocode_sync", slow),
            mock.patch.object(public_html, "REVERSE_GEOCODE_CALLER_TIMEOUT_SECONDS", 0.005),
        ):
            started = time.monotonic()
            results = await asyncio.gather(*(
                public_html._derive_place_for_create_map(10 + index, 10 + index)
                for index in range(10)
            ))
            self.assertLess(time.monotonic() - started, 0.04)
            self.assertTrue(all(result == (None, None) for result in results))
            await asyncio.sleep(0.15)
        self.assertLessEqual(maximum, public_html.REVERSE_GEOCODE_MAX_CONCURRENCY)

    async def test_circuit_opens_uses_fallback_and_later_recovers(self):
        provider = mock.Mock(side_effect=TimeoutError("upstream timed out"))
        with mock.patch.object(public_html, "_reverse_geocode_sync", provider):
            for index in range(public_html.REVERSE_GEOCODE_CIRCUIT_FAILURE_THRESHOLD):
                await public_html._derive_place_for_create_map(20 + index, 20 + index)
            calls_at_open = provider.call_count
            fallback = await public_html._derive_place_for_create_map(51.5074, -0.1278)
        self.assertEqual(provider.call_count, calls_at_open)
        self.assertEqual(fallback, ("London", "United Kingdom"))

        public_html._reverse_geocode_circuit_open_until = 0.0
        with mock.patch.object(
            public_html, "_reverse_geocode_sync", return_value=("Recovered", "Country")
        ) as recovered:
            result = await public_html._derive_place_for_create_map(35, 35)
        self.assertEqual(result, ("Recovered", "Country"))
        recovered.assert_called_once()


class PublicTransactionHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_response_contains_only_health_boolean(self):
        diagnostics = {
            "local_intent_count": 0,
            "fee_address": "NQ SECRET CONFIGURATION",
            "recent_errors": ["sensitive worker detail"],
            "refresher": {"running": True, "healthy": True, "queue_count": 4},
            "settlement": {"running": True, "healthy": True, "signing": "configured"},
        }
        with mock.patch.object(
            main, "funding_flow_diagnostics", mock.AsyncMock(return_value=diagnostics)
        ):
            response = await main.transaction_healthz()
        self.assertEqual(json.loads(response.body), {"ok": True})


if __name__ == "__main__":
    unittest.main()
