from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import draft_creation
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


class DraftCreationAdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await schema.init_db()
        async with schema.get_db() as db:
            self.user_id = await db_access.create_user(db, device_id_hash="d" * 64)
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def test_stale_pending_is_reclaimed_but_consumed_is_counted_and_indexes_burn(self):
        with (
            mock.patch.object(const, "DRAFT_CREATION_WINDOW_SECONDS", 100),
            mock.patch.object(const, "DRAFT_RESERVATION_STALE_SECONDS", 10),
            mock.patch.object(const, "DRAFT_CREATION_LIMIT_PER_USER", 2),
        ):
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    stale = await db_access.reserve_draft_creation(db, user_id=self.user_id, now=100)
                async with db_access.transaction(db, immediate=True):
                    consumed = await db_access.reserve_draft_creation(db, user_id=self.user_id, now=101)
                    await db_access.consume_draft_creation(db, reservation_id=consumed["id"], user_id=self.user_id)
                async with db_access.transaction(db, immediate=True):
                    replacement = await db_access.reserve_draft_creation(db, user_id=self.user_id, now=111)

            self.assertIsNotNone(replacement)
            self.assertGreater(replacement["deposit_key_index"], consumed["deposit_key_index"])
            self.assertGreater(replacement["deposit_key_index"], stale["deposit_key_index"])
            # The consumed row plus replacement exhaust the allowance; the
            # stale pending row did not remain counted.
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    blocked = await db_access.reserve_draft_creation(db, user_id=self.user_id, now=111)
            self.assertIsNone(blocked)

    async def test_cancelled_waiter_keeps_derivation_slot_until_thread_finishes(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def derive(index):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return index

        draft_creation._derivation_semaphore = asyncio.Semaphore(1)
        with mock.patch.object(draft_creation.wallet, "derive_spot_deposit_address", derive):
            first = asyncio.create_task(draft_creation.derive_reserved_deposit(1))
            await asyncio.sleep(0.005)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await draft_creation.derive_reserved_deposit(2)
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
