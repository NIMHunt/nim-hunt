from __future__ import annotations

import hashlib
import json
import tempfile
from unittest import IsolatedAsyncioTestCase

from starlette.requests import Request

import cache
import database as schema
import fresh_claim_guard
import public_html


class FreshClaimGuardRouteTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.old_path = schema.DB_PATH
        schema.DB_PATH = self.tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self.old_path
        self.tmp.close()

    async def test_empty_visible_spot_list_records_authenticated_ordinary_gps(self):
        device = hashlib.sha256(b"empty-map-user").hexdigest()
        payload = public_html.ClaimStatusRequest(
            device_id_hash=device,
            wallet_available=True,
            location_available=True,
            lat=12.34567,
            long=23.45678,
            accuracy=5,
            spot_ids=[],
        )
        request = Request({
            "type": "http", "method": "POST", "path": "/api/spots/claim-status",
            "headers": [], "client": ("127.0.0.1", 1234),
        })

        response = await public_html.spots_claim_status_api(payload, request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["statuses"], {})
        async with schema.get_db() as db:
            user = await db.execute_fetchall(
                f"SELECT {schema.USER_ID} FROM {schema.USER_TABLE_NAME} "
                f"WHERE {schema.USER_DEVICE_ID_HASH}=?", (device,)
            )
            state = await fresh_claim_guard._get(
                db, fresh_claim_guard._key(fresh_claim_guard.GPS_PREFIX, user[0][0])
            )
        self.assertTrue(state["ordinary_presence_before_reward"])
        self.assertFalse(state["first_inside_public_spot"])
        self.assertEqual((state["last_lat"], state["last_long"]), (12.346, 23.457))


def test_find_spots_requests_claim_status_even_when_no_spots_are_visible():
    source = (fresh_claim_guard.const.STATIC_DIR / "find_spots.js").read_text()
    function = source[source.index("async function refreshClaimStatusesForSpots"):]
    function = function[:function.index("\n}\n")]
    assert "spots.length <= 0" not in function
    assert "Array.isArray(spots) ? spots : []" in function
