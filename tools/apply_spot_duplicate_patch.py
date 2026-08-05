from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_VERSION = "spot-duplicate-v1-20260805"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    if content.count(old) != 1:
        raise RuntimeError(
            f"Expected exactly one match in {path!r}, found {content.count(old)}"
        )
    write(path, content.replace(old, new, 1))


SPOT_DUPLICATE_PY = '''\
"""Create clean draft copies of creator-owned Spots."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

import constants as const
import database as schema
import db_access
from database import get_db
from public_html import (
    CreateDraftSpotRequest,
    _creator_api_user_or_response,
    _notify_user_cache,
    _public_user,
    _serialise_owner_spot,
)

router = APIRouter()


class DuplicateSpotError(Exception):
    """Expected creator-facing failure while preparing a duplicate draft."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        draft_count: int | None = None,
        draft_limit: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = int(http_status)
        self.draft_count = draft_count
        self.draft_limit = draft_limit


def duplicate_spot_configuration(
    source: dict[str, Any],
    *,
    title: str,
    now: int,
) -> dict[str, Any]:
    """Return the strict configuration whitelist for a new draft.

    Operational identifiers, statuses, deposits, transactions, claims, reports,
    claim codes and draw outcomes are deliberately absent. Creation helpers
    generate fresh identity, wallet and fee fields for the copy.
    """
    is_prizedraw = source.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None
    starts_at = source.get(schema.SPOT_STARTS_AT)
    copied_starts_at = (
        int(starts_at)
        if starts_at is not None and int(starts_at) > int(now)
        else None
    )

    kwargs: dict[str, Any] = {
        "title": str(title).strip(),
        "desc": source.get(schema.SPOT_DESC),
        "lat": source.get(schema.SPOT_LAT),
        "long": source.get(schema.SPOT_LONG),
        "radius": source.get(schema.SPOT_RADIUS),
        "claim_duration": source.get(schema.SPOT_CLAIM_DURATION),
        "max_claims_per_user": source.get(schema.SPOT_MAX_CLAIMS_PER_USER),
        "max_total_claims": source.get(schema.SPOT_MAX_TOTAL_CLAIMS),
        "total_value": source.get(schema.SPOT_TOTAL_VALUE),
        "starts_at": copied_starts_at,
        "ends_at": source.get(schema.SPOT_ENDS_AT),
        "use_password": (
            False
            if is_prizedraw
            else bool(int(source.get(schema.SPOT_USE_PASSWORD) or 0))
        ),
        "city": source.get(schema.SPOT_CITY),
        "country": source.get(schema.SPOT_COUNTRY),
        "auto_reverse_geocode": False,
    }
    if is_prizedraw:
        kwargs["prize_count"] = int(source[schema.PRIZEDRAW_PRIZE_COUNT])

    return {"is_prizedraw": is_prizedraw, "create_kwargs": kwargs}


async def duplicate_owned_spot_as_draft(
    db,
    *,
    source_spot_id: int,
    user_id: int,
    title: str,
    now: int,
    draft_limit: int,
) -> int:
    """Create one clean duplicate after ownership and draft-limit checks."""
    source = await db_access.get_spot_owner_summary(
        db,
        spot_id=int(source_spot_id),
    )
    if source is None:
        raise DuplicateSpotError(
            "spot_missing",
            "This spot could not be found.",
            status.HTTP_404_NOT_FOUND,
        )
    if int(source[schema.SPOT_CREATED_BY]) != int(user_id):
        raise DuplicateSpotError(
            "not_owner",
            "This spot was not created by this device account.",
            status.HTTP_403_FORBIDDEN,
        )

    draft_count = await db_access.count_draft_spots_by_user(
        db,
        user_id=int(user_id),
    )
    if draft_count >= int(draft_limit):
        raise DuplicateSpotError(
            "draft_limit_reached",
            (
                f"You already have {draft_count} draft spots. "
                "Publish or delete one before creating another."
            ),
            status.HTTP_409_CONFLICT,
            draft_count=draft_count,
            draft_limit=int(draft_limit),
        )

    config = duplicate_spot_configuration(source, title=title, now=int(now))
    create_kwargs = config["create_kwargs"]
    if config["is_prizedraw"]:
        return await db_access.create_prizedraw(
            db,
            created_by=int(user_id),
            **create_kwargs,
        )
    return await db_access.create_spot(
        db,
        created_by=int(user_id),
        **create_kwargs,
    )


@router.post("/api/my-spots/{spot_id}/duplicate")
async def duplicate_spot_api(
    spot_id: int,
    payload: CreateDraftSpotRequest,
) -> JSONResponse:
    """Duplicate one creator-owned Spot into a clean, unfunded draft."""
    if int(payload.captcha_answer) != int(payload.captcha_a) + int(payload.captcha_b):
        return JSONResponse(
            {
                "ok": False,
                "code": "captcha_failed",
                "message": "The captcha answer was not correct.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async with get_db() as db:
        try:
            async with db_access.transaction(db, immediate=True):
                user, meta, http_status = await _creator_api_user_or_response(
                    db,
                    payload,
                )
                if user is None:
                    return JSONResponse(meta, status_code=http_status)

                user_id = int(user[schema.USER_ID])
                now = await db_access.get_unixepoch(db)
                draft_limit = int(
                    getattr(const, "MAX_DRAFT_SPOTS_PER_USER", 3)
                )
                new_spot_id = await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=int(spot_id),
                    user_id=user_id,
                    title=payload.title,
                    now=now,
                    draft_limit=draft_limit,
                )
        except DuplicateSpotError as exc:
            error = {
                **meta,
                "ok": False,
                "code": exc.code,
                "message": exc.message,
                "user": _public_user(user),
            }
            if exc.draft_count is not None:
                error["draft_count"] = int(exc.draft_count)
            if exc.draft_limit is not None:
                error["draft_limit"] = int(exc.draft_limit)
            return JSONResponse(error, status_code=exc.http_status)
        except ValueError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "duplicate_failed",
                    "message": str(exc),
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await _notify_user_cache(db, user_id=user_id)
        spot = await db_access.get_spot_owner_summary(
            db,
            spot_id=new_spot_id,
        )
        response_now = await db_access.get_unixepoch(db)

    return JSONResponse(
        {
            **meta,
            "ok": True,
            "user": _public_user(user),
            "spot": (
                _serialise_owner_spot(spot, now=response_now, transactions=[])
                if spot
                else None
            ),
            "edit_url": f"{const.CREATE_SPOT_URL}/{new_spot_id}",
        },
        status_code=status.HTTP_201_CREATED,
    )
'''


SPOT_DUPLICATE_JS = '''\
const CREATE_DRAFT_PATH = '/api/create-spot/draft';
const DUPLICATE_BUTTON_CLASS = 'spot-duplicate-button';

export function duplicateSpotEndpoint(sourceSpotId) {
    const spotId = Number(sourceSpotId);
    if (!Number.isSafeInteger(spotId) || spotId <= 0) {
        throw new TypeError('A positive source Spot id is required.');
    }
    return `/api/my-spots/${spotId}/duplicate`;
}

function requestUrl(input, baseUrl) {
    const value = typeof input === 'string' ? input : input?.url;
    if (!value) return null;
    try {
        return new URL(value, baseUrl);
    } catch (_err) {
        return null;
    }
}

function requestMethod(input, init) {
    return String(init?.method || input?.method || 'GET').toUpperCase();
}

export function requestTargetsOrdinaryDraftCreation(input, init, baseUrl) {
    const url = requestUrl(input, baseUrl);
    return Boolean(
        url
        && url.pathname === CREATE_DRAFT_PATH
        && requestMethod(input, init) === 'POST'
    );
}

export function createDuplicateAwareFetch(
    originalFetch,
    {
        sourceSpotId = () => null,
        baseUrl = 'https://nimhunt.invalid',
        onSuccess = () => {},
    } = {},
) {
    if (typeof originalFetch !== 'function') {
        throw new TypeError('A fetch function is required.');
    }

    return async function duplicateAwareFetch(input, init) {
        const sourceId = Number(sourceSpotId());
        if (
            Number.isSafeInteger(sourceId)
            && sourceId > 0
            && requestTargetsOrdinaryDraftCreation(input, init, baseUrl)
        ) {
            const response = await originalFetch(
                duplicateSpotEndpoint(sourceId),
                init,
            );
            if (response?.ok) onSuccess();
            return response;
        }
        return originalFetch(input, init);
    };
}

export function spotFromListItem(item) {
    const raw = item?.dataset?.renderSignature;
    if (!raw) return null;
    try {
        const spot = JSON.parse(raw);
        const id = Number(spot?.id);
        return Number.isSafeInteger(id) && id > 0 ? spot : null;
    } catch (_err) {
        return null;
    }
}

export function installSpotDuplication({ windowObj = window, documentObj = document } = {}) {
    const createOpen = documentObj.getElementById('create-spot-open');
    const createBackdrop = documentObj.getElementById('create-spot-backdrop');
    const titleInput = documentObj.getElementById('create-spot-title-input');
    const standardInput = documentObj.getElementById('create-spot-type-standard');
    const prizedrawInput = documentObj.getElementById('create-spot-type-prizedraw');
    const cancelButton = documentObj.getElementById('create-spot-cancel');
    const sections = documentObj.getElementById('my-spots-sections');
    if (!createOpen || !createBackdrop || !titleInput || !sections) return () => {};

    let selectedSpot = null;
    let openingDuplicate = false;

    function setTypeInputsDisabled(disabled) {
        if (standardInput) standardInput.disabled = Boolean(disabled);
        if (prizedrawInput) prizedrawInput.disabled = Boolean(disabled);
    }

    function clearSelection() {
        selectedSpot = null;
        setTypeInputsDisabled(false);
    }

    const originalFetch = windowObj.fetch.bind(windowObj);
    windowObj.fetch = createDuplicateAwareFetch(originalFetch, {
        sourceSpotId: () => selectedSpot?.id,
        baseUrl: windowObj.location.origin,
        onSuccess: clearSelection,
    });

    function openDuplicateModal(spot) {
        selectedSpot = spot;
        openingDuplicate = true;
        createOpen.click();
        openingDuplicate = false;

        if (createBackdrop.hidden) {
            clearSelection();
            return;
        }

        titleInput.value = String(spot.title || '');
        if (standardInput) standardInput.checked = !Boolean(spot.is_prizedraw);
        if (prizedrawInput) prizedrawInput.checked = Boolean(spot.is_prizedraw);
        setTypeInputsDisabled(true);
        titleInput.dispatchEvent(new Event('input', { bubbles: true }));
        titleInput.focus();
    }

    function duplicateButton(spot) {
        const button = documentObj.createElement('button');
        button.type = 'button';
        button.className = (
            `nq-button light-blue spot-owner-action-button ${DUPLICATE_BUTTON_CLASS}`
        );
        button.textContent = 'Duplicate';
        button.setAttribute(
            'aria-label',
            `Duplicate ${String(spot.title || 'Spot')}`,
        );
        button.addEventListener('click', () => openDuplicateModal(spot));
        return button;
    }

    function enhanceItem(item) {
        if (!item || item.querySelector(`.${DUPLICATE_BUTTON_CLASS}`)) return;
        const spot = spotFromListItem(item);
        const detail = item.querySelector('.spot-list-detail');
        if (!spot || !detail) return;

        let actions = detail.querySelector('.spot-owner-actions');
        if (!actions) {
            actions = documentObj.createElement('div');
            actions.className = 'spot-owner-actions';
            detail.append(actions);
        }
        actions.append(duplicateButton(spot));
    }

    function enhanceAll() {
        for (const item of sections.querySelectorAll('.spot-list-item')) {
            enhanceItem(item);
        }
    }

    createOpen.addEventListener('click', () => {
        if (!openingDuplicate) clearSelection();
    }, true);
    cancelButton?.addEventListener('click', clearSelection);

    const observer = new MutationObserver(enhanceAll);
    observer.observe(sections, { childList: true, subtree: true });
    enhanceAll();

    return () => {
        observer.disconnect();
        windowObj.fetch = originalFetch;
        clearSelection();
    };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    installSpotDuplication();
}
'''


SPOT_DUPLICATE_NODE_TEST = '''\
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createDuplicateAwareFetch,
  duplicateSpotEndpoint,
  requestTargetsOrdinaryDraftCreation,
  spotFromListItem,
} from '../static/spot_duplicate.js';

test('duplicate endpoint requires and embeds a positive Spot id', () => {
  assert.equal(duplicateSpotEndpoint(42), '/api/my-spots/42/duplicate');
  assert.throws(() => duplicateSpotEndpoint(0), /positive source Spot id/);
});

test('only the ordinary POST draft request is eligible for rewriting', () => {
  const base = 'https://nimhunt.app/my-spots';
  assert.equal(
    requestTargetsOrdinaryDraftCreation(
      '/api/create-spot/draft',
      { method: 'POST' },
      base,
    ),
    true,
  );
  assert.equal(
    requestTargetsOrdinaryDraftCreation(
      '/api/create-spot/draft',
      { method: 'GET' },
      base,
    ),
    false,
  );
  assert.equal(
    requestTargetsOrdinaryDraftCreation(
      '/api/my-spots',
      { method: 'POST' },
      base,
    ),
    false,
  );
});

test('active duplication rewrites one creation request and preserves its body', async () => {
  const calls = [];
  let successCount = 0;
  const fetch = createDuplicateAwareFetch(
    async (input, init) => {
      calls.push([input, init]);
      return { ok: true };
    },
    {
      sourceSpotId: () => 17,
      baseUrl: 'https://nimhunt.app',
      onSuccess: () => { successCount += 1; },
    },
  );
  const init = { method: 'POST', body: '{"title":"Copy"}' };
  await fetch('/api/create-spot/draft', init);

  assert.deepEqual(calls, [['/api/my-spots/17/duplicate', init]]);
  assert.equal(successCount, 1);
});

test('failed duplication stays selected for a manual retry', async () => {
  let successCount = 0;
  const fetch = createDuplicateAwareFetch(
    async () => ({ ok: false }),
    {
      sourceSpotId: () => 4,
      baseUrl: 'https://nimhunt.app',
      onSuccess: () => { successCount += 1; },
    },
  );
  await fetch('/api/create-spot/draft', { method: 'POST' });
  assert.equal(successCount, 0);
});

test('list-item Spot parsing rejects malformed or missing identifiers', () => {
  assert.deepEqual(
    spotFromListItem({ dataset: { renderSignature: '{"id":8,"title":"Eight"}' } }),
    { id: 8, title: 'Eight' },
  );
  assert.equal(spotFromListItem({ dataset: { renderSignature: '{bad' } }), null);
  assert.equal(spotFromListItem({ dataset: { renderSignature: '{"id":0}' } }), null);
});
'''


SPOT_DUPLICATE_PY_TEST = '''\
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
from spot_duplicate import (
    DuplicateSpotError,
    duplicate_owned_spot_as_draft,
    duplicate_spot_configuration,
)


class SpotDuplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()
        async with schema.get_db() as db:
            self.owner_id = await db_access.create_user(
                db,
                device_id_hash="a" * 64,
            )
            self.other_id = await db_access.create_user(
                db,
                device_id_hash="b" * 64,
            )
            await db.commit()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def test_standard_duplicate_is_clean_and_resets_an_elapsed_start(self):
        old_fee = 12_345
        new_fee = 67_890
        now = 2_000_000_000
        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", old_fee):
            async with schema.get_db() as db:
                source_id = await db_access.create_spot(
                    db,
                    created_by=self.owner_id,
                    title="Original Standard",
                    desc="Copied description",
                    lat=55.95,
                    long=-3.19,
                    radius=300,
                    claim_duration=600,
                    max_claims_per_user=2,
                    max_total_claims=3,
                    total_value=3 * const.MIN_STANDARD_CLAIM_PAYOUT,
                    starts_at=now - 100,
                    ends_at=7 * 24 * 60 * 60,
                    use_password=True,
                    city="Edinburgh",
                    country="United Kingdom",
                    auto_reverse_geocode=False,
                )
                source = await db_access.get_spot(db, spot_id=source_id)
                await db_access.create_spot_deposit_transaction(
                    db,
                    user_id=self.owner_id,
                    spot_id=source_id,
                    amount=100,
                    from_address="NQ SOURCE",
                    to_address=str(source[schema.SPOT_DEPOSIT_ADDRESS]),
                    tx_hash="source-deposit-history",
                )
                await db_access.create_claim_code(
                    db,
                    spot_id=source_id,
                    claim_code="SOURCECODE1",
                )
                await db.commit()

        with mock.patch.object(const, "STANDARD_SPOT_CREATION_FEE", new_fee):
            async with schema.get_db() as db:
                async with db_access.transaction(db, immediate=True):
                    copy_id = await duplicate_owned_spot_as_draft(
                        db,
                        source_spot_id=source_id,
                        user_id=self.owner_id,
                        title="Copied Standard",
                        now=now,
                        draft_limit=10,
                    )
                source = await db_access.get_spot(db, spot_id=source_id)
                copy = await db_access.get_spot(db, spot_id=copy_id)
                copy_transactions = await db_access.get_transactions_by_spot(
                    db,
                    spot_id=copy_id,
                )
                copy_codes = await db_access.get_claim_codes(db, spot_id=copy_id)
                copy_claims = await db_access.get_claims(
                    db,
                    spot_id=copy_id,
                    include_failed=True,
                )

        self.assertNotEqual(copy_id, source_id)
        self.assertEqual(copy[schema.SPOT_STATUS], const.SPOT_STATUS_DRAFT)
        self.assertEqual(copy[schema.SPOT_TITLE], "Copied Standard")
        self.assertEqual(copy[schema.SPOT_DESC], source[schema.SPOT_DESC])
        self.assertEqual(copy[schema.SPOT_LAT], source[schema.SPOT_LAT])
        self.assertEqual(copy[schema.SPOT_LONG], source[schema.SPOT_LONG])
        self.assertEqual(copy[schema.SPOT_RADIUS], source[schema.SPOT_RADIUS])
        self.assertEqual(
            copy[schema.SPOT_MAX_TOTAL_CLAIMS],
            source[schema.SPOT_MAX_TOTAL_CLAIMS],
        )
        self.assertEqual(copy[schema.SPOT_ENDS_AT], source[schema.SPOT_ENDS_AT])
        self.assertIsNone(copy[schema.SPOT_STARTS_AT])
        self.assertNotEqual(copy[schema.SPOT_LINK], source[schema.SPOT_LINK])
        self.assertNotEqual(
            copy[schema.SPOT_DEPOSIT_ADDRESS],
            source[schema.SPOT_DEPOSIT_ADDRESS],
        )
        self.assertNotEqual(
            copy[schema.SPOT_DEPOSIT_KEY_INDEX],
            source[schema.SPOT_DEPOSIT_KEY_INDEX],
        )
        self.assertEqual(copy[schema.SPOT_CREATION_FEE], new_fee)
        self.assertEqual(copy_transactions, [])
        self.assertEqual(copy_codes, [])
        self.assertEqual(copy_claims, [])

    async def test_prizedraw_duplicate_preserves_future_schedule_and_prize_rules(self):
        now = 2_000_000_000
        future_start = now + 86_400
        async with schema.get_db() as db:
            source_id = await db_access.create_prizedraw(
                db,
                created_by=self.owner_id,
                title="Original Draw",
                desc="Draw details",
                lat=51.5,
                long=-0.1,
                radius=250,
                claim_duration=0,
                max_claims_per_user=1,
                max_total_claims=8,
                total_value=2 * const.MIN_PRIZEDRAW_PRIZE_PAYOUT,
                prize_count=2,
                starts_at=future_start,
                ends_at=3 * 24 * 60 * 60,
                city="London",
                country="United Kingdom",
                auto_reverse_geocode=False,
            )
            async with db_access.transaction(db, immediate=True):
                copy_id = await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=source_id,
                    user_id=self.owner_id,
                    title="Copied Draw",
                    now=now,
                    draft_limit=10,
                )
            copy = await db_access.get_spot_owner_summary(db, spot_id=copy_id)

        self.assertEqual(copy[schema.SPOT_STARTS_AT], future_start)
        self.assertEqual(copy[schema.SPOT_ENDS_AT], 3 * 24 * 60 * 60)
        self.assertEqual(copy[schema.SPOT_USE_PASSWORD], 0)
        self.assertEqual(copy[schema.PRIZEDRAW_PRIZE_COUNT], 2)

    async def test_duplicate_enforces_source_ownership_and_draft_limit(self):
        async with schema.get_db() as db:
            source_id = await db_access.create_spot(
                db,
                created_by=self.owner_id,
                title="Ownership Source",
            )
            await db.commit()

            with self.assertRaises(DuplicateSpotError) as not_owner:
                await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=source_id,
                    user_id=self.other_id,
                    title="Forbidden Copy",
                    now=2_000_000_000,
                    draft_limit=10,
                )
            self.assertEqual(not_owner.exception.code, "not_owner")

            with self.assertRaises(DuplicateSpotError) as limited:
                await duplicate_owned_spot_as_draft(
                    db,
                    source_spot_id=source_id,
                    user_id=self.owner_id,
                    title="Excess Copy",
                    now=2_000_000_000,
                    draft_limit=1,
                )
            self.assertEqual(limited.exception.code, "draft_limit_reached")

    def test_configuration_is_an_explicit_non_operational_whitelist(self):
        source = {
            schema.SPOT_DESC: "Description",
            schema.SPOT_LAT: 1.0,
            schema.SPOT_LONG: 2.0,
            schema.SPOT_RADIUS: 100,
            schema.SPOT_CLAIM_DURATION: 0,
            schema.SPOT_MAX_CLAIMS_PER_USER: 1,
            schema.SPOT_MAX_TOTAL_CLAIMS: 2,
            schema.SPOT_TOTAL_VALUE: const.MIN_SPOT_TOTAL_VALUE,
            schema.SPOT_STARTS_AT: None,
            schema.SPOT_ENDS_AT: const.MIN_SPOT_ENDS_AFTER_SECONDS,
            schema.SPOT_USE_PASSWORD: 0,
            schema.SPOT_CITY: "Here",
            schema.SPOT_COUNTRY: "There",
            schema.SPOT_ID: 99,
            schema.SPOT_LINK: "old-link",
            schema.SPOT_DEPOSIT_ADDRESS: "old-wallet",
            schema.SPOT_STATUS: const.SPOT_STATUS_COMPLETED,
        }
        config = duplicate_spot_configuration(
            source,
            title="Whitelisted Copy",
            now=2_000_000_000,
        )["create_kwargs"]
        self.assertNotIn(schema.SPOT_ID, config)
        self.assertNotIn(schema.SPOT_LINK, config)
        self.assertNotIn(schema.SPOT_DEPOSIT_ADDRESS, config)
        self.assertNotIn(schema.SPOT_STATUS, config)
        self.assertEqual(config["title"], "Whitelisted Copy")
'''


SPOT_DUPLICATE_DELIVERY_TEST = '''\
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_VERSION = "spot-duplicate-v1-20260805"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_duplicate_button_reuses_create_modal_and_dedicated_api():
    module = source("static/spot_duplicate.js")
    bootstrap = source("static/my_spots_bootstrap.js")
    template = source("templates/my_spots.html")

    assert "button.textContent = 'Duplicate'" in module
    assert "createOpen.click();" in module
    assert "titleInput.value" in module
    assert "prizedrawInput.checked" in module
    assert "requestTargetsOrdinaryDraftCreation" in module
    assert "`/api/my-spots/${spotId}/duplicate`" in module
    assert f"./spot_duplicate.js?v={CACHE_VERSION}" in bootstrap
    assert f"/static/my_spots_bootstrap.js?v={CACHE_VERSION}-" in template


def test_duplicate_backend_is_isolated_from_transaction_and_claim_writes():
    module = source("spot_duplicate.py")

    assert "duplicate_owned_spot_as_draft" in module
    assert "count_draft_spots_by_user" in module
    assert "SPOT_CREATED_BY" in module
    assert "create_spot(" in module
    assert "create_prizedraw(" in module
    assert "create_spot_deposit_transaction" not in module
    assert "create_claim(" not in module
    assert "create_claim_code(" not in module
    assert "trans_updater" not in module
    assert "settlement_updater" not in module
'''


write("spot_duplicate.py", SPOT_DUPLICATE_PY)
write("static/spot_duplicate.js", SPOT_DUPLICATE_JS)
write("helpers/spot_duplicate.test.mjs", SPOT_DUPLICATE_NODE_TEST)
write("tests/test_spot_duplication.py", SPOT_DUPLICATE_PY_TEST)
write("tests/test_spot_duplicate_delivery.py", SPOT_DUPLICATE_DELIVERY_TEST)

replace_once(
    "main.py",
    "from public_html import router as public_router\n",
    (
        "from public_html import router as public_router\n"
        "from spot_duplicate import router as spot_duplicate_router\n"
    ),
)
replace_once(
    "main.py",
    "app.include_router(public_router)\napp.include_router(social_preview.router)\n",
    (
        "app.include_router(public_router)\n"
        "app.include_router(spot_duplicate_router)\n"
        "app.include_router(social_preview.router)\n"
    ),
)
replace_once(
    "static/my_spots_bootstrap.js",
    "import './my_spots.js?v=rapid-deposit-v1-20260805';",
    (
        "import './my_spots.js?v=rapid-deposit-v1-20260805';\n"
        f"import './spot_duplicate.js?v={CACHE_VERSION}';"
    ),
)
replace_once(
    "templates/my_spots.html",
    "/static/my_spots_bootstrap.js?v=rapid-deposit-v1-20260805-",
    f"/static/my_spots_bootstrap.js?v={CACHE_VERSION}-",
)
replace_once(
    "tests/test_map_asset_cache_busting.py",
    'MY_SPOTS_BOOTSTRAP_CACHE_VERSION = "rapid-deposit-v1-20260805"',
    f'MY_SPOTS_BOOTSTRAP_CACHE_VERSION = "{CACHE_VERSION}"',
)
replace_once(
    "tests/test_map_asset_cache_busting.py",
    (
        '        f"./my_spots.js?v={{MY_SPOTS_BOOTSTRAP_CACHE_VERSION}}"\n'
        "        in bootstrap\n"
    ),
    (
        '        "./my_spots.js?v=rapid-deposit-v1-20260805"\n'
        "        in bootstrap\n"
    ),
)
replace_once(
    "tests/test_my_spots_world_wrap.py",
    'PAGE_CACHE_VERSION = "rapid-deposit-v1-20260805"',
    f'PAGE_CACHE_VERSION = "{CACHE_VERSION}"',
)
replace_once(
    "tests/test_my_spots_world_wrap.py",
    'assert f"./my_spots.js?v={PAGE_CACHE_VERSION}" in bootstrap',
    'assert "./my_spots.js?v=rapid-deposit-v1-20260805" in bootstrap',
)
replace_once(
    "tests/test_my_spots_world_wrap.py",
    'assert f"./my_spots_map_policy.js?v={PAGE_CACHE_VERSION}" in page',
    'assert "./my_spots_map_policy.js?v=rapid-deposit-v1-20260805" in page',
)
replace_once(
    "helpers/package.json",
    "pending_deposit_store.test.mjs keyed_reconcile.test.mjs",
    "pending_deposit_store.test.mjs spot_duplicate.test.mjs keyed_reconcile.test.mjs",
)
replace_once(
    "README.md",
    "- **Creator tools** — inspect drafts, deposits, publishing state, claim codes and history.",
    (
        "- **Creator tools** — inspect drafts, deposits, publishing state, claim codes and history; "
        "duplicate any owned Spot into a clean, editable draft."
    ),
)

# This workflow and script are staging machinery only; keep them out of the PR diff.
(ROOT / ".github/workflows/apply-spot-duplicate-patch.yml").unlink()
Path(__file__).unlink()
