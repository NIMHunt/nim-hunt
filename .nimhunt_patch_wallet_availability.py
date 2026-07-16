from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:180]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_section(path: str, start: str, end: str, replacement: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{path}: start marker not found: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{path}: end marker not found: {end!r}")
    file_path.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


# ---------------------------------------------------------------------------
# Database helpers and claim-rule precedence
# ---------------------------------------------------------------------------
replace_once(
    "db_access.py",
    '''async def count_spot_deposit_transactions(db, *, spot_id: int) -> int:
''',
    '''async def get_confirmed_spot_funding_address(db, *, spot_id: int) -> str | None:
    """Return the first confirmed on-chain sender for a Spot's deposits.

    This address becomes the Spot's funding wallet. Later top-ups must come from
    the same wallet so cancellation can safely refund one known contributor.
    """
    cur = await db.execute(
        f"""
        SELECT {schema.TRANS_FROM_ADDRESS} AS from_address
        FROM {schema.TRANS_TABLE_NAME}
        WHERE {schema.TRANS_SPOT_ID} = ?
          AND {schema.TRANS_TYPE} = ?
          AND {schema.TRANS_STATUS} = ?
          AND TRIM({schema.TRANS_FROM_ADDRESS}) != ''
        ORDER BY {schema.TRANS_CREATED_AT} ASC, {schema.TRANS_ID} ASC
        LIMIT 1;
        """,
        (int(spot_id), const.TRANS_TYPE_FILL_SPOT, const.TRANS_STATUS_CONFIRMED),
    )
    row = await cur.fetchone()
    if row is None or not row["from_address"]:
        return None
    return str(row["from_address"]).strip()


async def count_spot_deposit_transactions(db, *, spot_id: int) -> int:
''',
)

replace_section(
    "db_access.py",
    "async def get_claim_rule_check(\n",
    "\n\n\n\ndef _normalise_location_accuracy_score",
    '''async def get_claim_rule_check(
    db,
    *,
    spot_id: int,
    user_id: int,
    lat: float | None,
    long: float | None,
    location_accuracy_metres: float | None = None,
) -> RowDict:
    """Return a compact claim outcome check without writing anything.

    Location is optional so Find Spots can still report permanent blockers such
    as ownership, exhausted capacity, or the user's claim limit. Those blockers
    deliberately take precedence over the temporary absence of a GPS reading.
    """
    user_ok = await can_user_claim(db, user_id=user_id)
    public = await get_public_spot(db, spot_id=spot_id)
    spot = await get_spot(db, spot_id=spot_id)
    own_spot = bool(spot and int(spot[schema.SPOT_CREATED_BY]) == int(user_id))
    location_known = lat is not None and long is not None
    distance_check = None
    if location_known:
        distance_check = await get_claim_distance_check(
            db,
            spot_id=spot_id,
            lat=float(lat),
            long=float(long),
            location_accuracy_metres=location_accuracy_metres,
        )
    capacity_ok = await is_spot_claim_capacity_available(db, spot_id=spot_id)
    user_limit_ok = not await has_user_reached_claim_limit(db, spot_id=spot_id, user_id=user_id)
    cancellation_pending = await has_spot_cancellation_started(db, spot_id=spot_id)

    spot_current = bool(public and int(public.get("availability_rank", 1)) == 0)
    within_radius = bool(distance_check and distance_check["within_radius"])

    reason = None
    message = None
    if not user_ok:
        reason = "user_not_allowed"
        message = "This device account cannot claim spots."
    elif own_spot:
        reason = "own_spot"
        message = "You cannot claim your own spot."
    elif cancellation_pending:
        reason = "cancellation_pending"
        message = "This spot is being cancelled and can no longer be claimed."
    elif not spot_current:
        reason = "not_active"
        message = "This spot is not active right now."
    elif not capacity_ok:
        reason = "capacity_full"
        message = "This spot has no remaining claim capacity."
    elif not user_limit_ok:
        reason = "user_limit_reached"
        message = "You have already reached your claim limit for this spot."
    elif not location_known:
        reason = "location_unknown"
        message = "Your location is unknown."
    elif not within_radius:
        reason = "outside_radius"
        message = "Move inside the spot radius to claim."

    allowed = bool(
        user_ok
        and not own_spot
        and not cancellation_pending
        and spot_current
        and capacity_ok
        and user_limit_ok
        and location_known
        and within_radius
    )

    return {
        "allowed": allowed,
        "reason": reason,
        "message": message,
        "user_ok": user_ok,
        "own_spot": own_spot,
        "spot_current": spot_current,
        "cancellation_pending": cancellation_pending,
        "location_known": location_known,
        "within_radius": within_radius,
        "capacity_ok": capacity_ok,
        "user_limit_ok": user_limit_ok,
        "distance": distance_check,
    }
''',
)

# ---------------------------------------------------------------------------
# Claim-status API: return permanent blockers even without GPS
# ---------------------------------------------------------------------------
replace_once(
    "public_html.py",
    '''        if payload.lat is None or payload.long is None:
            return JSONResponse({**meta, "ok": True, "user": _public_user(user), "statuses": {}})

''',
    "",
)
replace_once(
    "public_html.py",
    '''                lat=float(payload.lat),
                long=float(payload.long),
''',
    '''                lat=None if payload.lat is None else float(payload.lat),
                long=None if payload.long is None else float(payload.long),
''',
)
replace_once(
    "public_html.py",
    '''                "within_radius": bool(rule.get("within_radius")),
''',
    '''                "location_known": bool(rule.get("location_known")),
                "within_radius": bool(rule.get("within_radius")),
''',
)

# ---------------------------------------------------------------------------
# Funding wallet restriction: helpful submission guard + authoritative chain guard
# ---------------------------------------------------------------------------
replace_once(
    "trans_updater.py",
    '''    trans_id = await db_access.create_spot_deposit_transaction(
''',
    '''    funding_address = await db_access.get_confirmed_spot_funding_address(
        db,
        spot_id=int(spot_id),
    )
    if funding_address is not None:
        established_sender = _normalise_address_for_compare(funding_address)
        submitted_sender = _normalise_address_for_compare(clean_from_address)
        if established_sender is None or submitted_sender != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    trans_id = await db_access.create_spot_deposit_transaction(
''',
)

replace_section(
    "trans_updater.py",
    "async def mark_trans_as_confirmed(\n",
    "\n\nasync def mark_trans_as_failed",
    '''async def mark_trans_as_confirmed(
    db,
    trans: RowDict,
    *,
    block_number: int | None = None,
    verified_details: VerifiedChainDetails | None = None,
) -> RowDict:
    """Mark a TRANSACTION confirmed without permitting mixed-wallet deposits.

    A BEGIN IMMEDIATE lock serialises confirmation of competing deposits. The
    first confirmed on-chain sender becomes the Spot's funding wallet; a later
    deposit from another sender is retained for audit but marked FAILED so it
    cannot fund claims or be included in the creator's cancellation refund.
    """
    trans_id = _transaction_id(trans)
    completed_prizedraw_payout = await _claim_transaction_is_completed_prizedraw_payout(db, trans)
    claim_id = trans.get(schema.TRANS_CLAIM_ID)
    trans_type = int(trans.get(schema.TRANS_TYPE) or -1)
    spot_id = trans.get(schema.TRANS_SPOT_ID)
    funding_mismatch_reason: str | None = None
    cancelled_finalized = False

    try:
        await db.execute("BEGIN IMMEDIATE;")

        if verified_details is not None and verified_details.ok:
            if trans_type == const.TRANS_TYPE_FILL_SPOT and spot_id is not None:
                funding_address = await db_access.get_confirmed_spot_funding_address(
                    db,
                    spot_id=int(spot_id),
                )
                if funding_address is not None:
                    established_sender = _normalise_address_for_compare(funding_address)
                    confirmed_sender = _normalise_address_for_compare(verified_details.from_address)
                    if established_sender is None or confirmed_sender != established_sender:
                        funding_mismatch_reason = (
                            "confirmed deposit used a different wallet than the Spot's original funding wallet"
                        )

            # Keep the actual chain facts even when the funding-policy check
            # rejects this deposit. That leaves a complete audit trail for any
            # manual recovery of funds sent from the wrong wallet.
            await db_access.update_transaction_chain_details(
                db,
                trans_id=trans_id,
                from_address=verified_details.from_address,
                to_address=verified_details.to_address,
                amount=verified_details.amount,
            )

        if funding_mismatch_reason is not None:
            await db_access.set_transaction_status_to_failed(db, trans_id=trans_id)
        else:
            if block_number is None:
                await db_access.modify_transaction_status(
                    db,
                    trans_id=trans_id,
                    status=const.TRANS_STATUS_CONFIRMED,
                )
            else:
                await db_access.set_transaction_status_to_confirmed(
                    db,
                    trans_id=trans_id,
                    block_number=int(block_number),
                )

            # In the revised Prizedraw model, selected winners stay PENDING until
            # their payout transaction confirms. Losers are already SUCCESS.
            if completed_prizedraw_payout and claim_id is not None:
                await db_access.set_claim_status_to_success(db, claim_id=int(claim_id))
            cancelled_finalized = await _finalize_cancelled_spot_if_ready(
                db, spot_id=spot_id
            )

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await cache.notify_transaction_changed(
        db,
        trans_id=trans_id,
        spot_id=spot_id,
        user_id=trans.get(schema.TRANS_USER_ID),
    )
    if completed_prizedraw_payout and claim_id is not None and funding_mismatch_reason is None:
        await cache.notify_claim_changed(
            db,
            spot_id=spot_id,
            user_id=trans.get(schema.TRANS_USER_ID),
        )
    if cancelled_finalized:
        await cache.notify_spot_changed(db, spot_id=spot_id)

    if funding_mismatch_reason is not None:
        return {
            "trans_id": trans_id,
            "status": "failed",
            "reason": funding_mismatch_reason,
        }
    return {"trans_id": trans_id, "status": "confirmed", "block_number": block_number}
''',
)

# ---------------------------------------------------------------------------
# Find Spots: unknown location hides the claim button; hard blockers still show
# ---------------------------------------------------------------------------
replace_section(
    "static/find_spots.js",
    "function claimStatusForSpot(spot) {\n",
    "\nfunction shouldShowClaimAction(spot) {",
    '''function claimStatusForSpot(spot) {
    const stored = state.claimStatusBySpotId.get(Number(spot.id));
    if (stored) return stored;

    const inRange = spotWithinRadius(spot);
    const ownSpot = currentUserOwnsSpot(spot);
    const active = spot.status_label === 'active';
    const participantCount = Number(spot.success_claim_count || 0)
        + (spot.is_prizedraw ? Number(spot.pending_claim_count || 0) : 0);
    const maxParticipants = Number(spot.max_total_claims || 0);
    const capacityFull = maxParticipants > 0 && participantCount >= maxParticipants;
    let reason = 'outside_radius';
    let message = 'Move inside the spot radius to claim.';

    if (ownSpot) {
        reason = 'own_spot';
        message = 'You cannot claim your own spot.';
    } else if (!state.user) {
        reason = 'user_unknown';
        message = `Open ${APP_NAME} in Nimiq Pay to identify this device.`;
    } else if (!active) {
        reason = 'not_active';
        message = 'This spot is not active right now.';
    } else if (capacityFull) {
        reason = 'capacity_full';
        message = 'This spot has no remaining claim capacity.';
    } else if (!state.hasUserLocation) {
        reason = 'location_unknown';
        message = 'Your location is unknown.';
    } else if (inRange) {
        reason = 'unknown';
        message = 'This Spot cannot be claimed right now.';
    }

    return {
        allowed: false,
        action: 'unavailable',
        kind: 'unavailable',
        reason,
        user_ok: Boolean(state.user),
        own_spot: ownSpot,
        location_known: state.hasUserLocation,
        within_radius: inRange,
        capacity_ok: !capacityFull,
        user_limit_ok: true,
        requires_password: Boolean(spot.use_password),
        requires_duration: Number(spot.claim_duration || 0) > 0,
        is_prizedraw: Boolean(spot.is_prizedraw),
        reward_amount: Number(spot.total_value || 0) / Math.max(1, Number(spot.is_prizedraw ? spot.prize_count || 1 : spot.max_total_claims || 1)),
        participant_count: participantCount,
        max_participants: maxParticipants,
        prize_count: Number(spot.prize_count || 1),
        message,
    };
}
''',
)

replace_section(
    "static/find_spots.js",
    "function shouldShowClaimAction(spot) {\n",
    "\nfunction claimActionText(spot) {",
    '''function shouldShowClaimAction(spot) {
    if (spot.status_label !== 'active') return false;

    const status = claimStatusForSpot(spot);
    if (status.allowed || status.within_radius) return true;

    const reason = String(status.reason || '').toLowerCase();
    if (status.own_spot || status.capacity_ok === false || status.user_limit_ok === false) return true;

    return new Set([
        'own_spot',
        'user_not_allowed',
        'capacity_full',
        'user_limit_reached',
        'claim_code_unavailable',
        'already_claimed',
        'already_entered',
        'cancellation_pending',
    ]).has(reason);
}
''',
)

replace_section(
    "static/find_spots.js",
    "function claimUnavailableMessage(status, spot) {\n",
    "\nfunction attachUnavailableClaimTooltip",
    '''function claimUnavailableMessage(status, spot) {
    const reason = String(status?.reason || '').trim();
    const message = String(status?.message || '').trim();

    if (status?.own_spot || reason === 'own_spot' || currentUserOwnsSpot(spot)) return 'You cannot claim your own spot.';
    if (status?.user_ok === false && reason === 'user_not_allowed') return 'This device account cannot claim spots.';
    if (status?.capacity_ok === false || reason === 'capacity_full') return 'This spot has no remaining claim capacity.';
    if (status?.user_limit_ok === false || reason === 'user_limit_reached') return 'You have already reached your claim limit for this spot.';
    if (reason === 'claim_code_unavailable') return 'There are no unused claim codes left for this spot.';
    if (reason === 'already_claimed' || reason === 'already_entered') return message || 'You have already used your available claim for this spot.';
    if (reason === 'cancellation_pending') return 'This spot is being cancelled and can no longer be claimed.';
    if (!state.user || reason === 'user_unknown') return `Open ${APP_NAME} in Nimiq Pay to identify this device.`;
    if (!state.hasUserLocation || reason === 'location_unknown') return 'Your location is unknown.';
    if (reason === 'not_active') return 'This spot is not active right now.';
    if (reason === 'outside_radius' || status?.within_radius === false) return 'Move inside the spot radius to claim.';
    if (message && message !== 'This spot cannot be claimed right now.') return message;

    return REPORT_TEXT.claim?.unavailableTooltip || 'This Spot cannot be claimed right now.';
}
''',
)

replace_once(
    "static/find_spots.js",
    '''    if (!Array.isArray(spots) || spots.length <= 0 || !state.hasUserLocation) return;
''',
    '''    if (!Array.isArray(spots) || spots.length <= 0) return;
''',
)

# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------
Path("tests/test_funding_wallet_and_find_spots.py").write_text(
    '''import tempfile
import unittest
from unittest import mock

import constants as const
import database as schema
import db_access
import trans_updater


class FundingWalletDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_db_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_db_path
        self._tmp.close()

    async def _create_spot(self):
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="funding-owner")
            spot_id = await db_access.create_spot(db, created_by=owner_id, title="Funding Wallet Spot")
            spot = await db_access.get_spot(db, spot_id=spot_id)
            await db.commit()
        return owner_id, spot_id, spot

    async def test_first_confirmed_sender_owns_later_topups(self):
        owner_id, spot_id, spot = await self._create_spot()
        deposit_address = str(spot[schema.SPOT_DEPOSIT_ADDRESS])

        async with schema.get_db() as db:
            first_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=10_000_000,
                from_address="wallet-a",
                to_address=deposit_address,
                tx_hash="funding-first",
            )
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=first_id,
                block_number=1,
            )
            wrong_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=2_000_000,
                from_address="wallet-a",
                to_address=deposit_address,
                tx_hash="funding-wrong",
            )
            await db.commit()
            wrong = await db_access.get_transaction(db, trans_id=wrong_id)

            normalise = lambda value: str(value or "").strip().lower() or None
            with mock.patch.object(trans_updater, "_normalise_address_for_compare", side_effect=normalise), \
                 mock.patch.object(trans_updater.cache, "notify_transaction_changed", mock.AsyncMock()):
                result = await trans_updater.mark_trans_as_confirmed(
                    db,
                    wrong,
                    block_number=2,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address="wallet-b",
                        to_address=deposit_address,
                        amount=2_000_000,
                    ),
                )

            wrong_after = await db_access.get_transaction(db, trans_id=wrong_id)
            self.assertEqual(result["status"], "failed")
            self.assertIn("original funding wallet", result["reason"])
            self.assertEqual(int(wrong_after[schema.TRANS_STATUS]), const.TRANS_STATUS_FAILED)
            self.assertEqual(wrong_after[schema.TRANS_FROM_ADDRESS], "wallet-b")
            self.assertEqual(
                await db_access.get_confirmed_spot_funding_address(db, spot_id=spot_id),
                "wallet-a",
            )

            same_id = await db_access.create_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=3_000_000,
                from_address="wallet-a",
                to_address=deposit_address,
                tx_hash="funding-same",
            )
            await db.commit()
            same = await db_access.get_transaction(db, trans_id=same_id)
            with mock.patch.object(trans_updater, "_normalise_address_for_compare", side_effect=normalise), \
                 mock.patch.object(trans_updater.cache, "notify_transaction_changed", mock.AsyncMock()):
                same_result = await trans_updater.mark_trans_as_confirmed(
                    db,
                    same,
                    block_number=3,
                    verified_details=trans_updater.VerifiedChainDetails(
                        ok=True,
                        from_address="wallet-a",
                        to_address=deposit_address,
                        amount=3_000_000,
                    ),
                )

            same_after = await db_access.get_transaction(db, trans_id=same_id)
            self.assertEqual(same_result["status"], "confirmed")
            self.assertEqual(int(same_after[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)
            self.assertEqual(await db_access.get_confirmed_spot_deposit_total(db, spot_id=spot_id), 13_000_000)


class FundingWalletSubmissionGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_known_different_sender_is_rejected_before_recording(self):
        spot = {
            schema.SPOT_ID: 7,
            schema.SPOT_DEPOSIT_ADDRESS: "deposit-wallet",
        }
        identity = lambda value, **kwargs: str(value).strip().lower()
        with mock.patch.object(trans_updater.db_access, "get_spot", mock.AsyncMock(return_value=spot)), \
             mock.patch.object(
                 trans_updater.db_access,
                 "get_confirmed_spot_funding_address",
                 mock.AsyncMock(return_value="wallet-a"),
             ), \
             mock.patch.object(trans_updater.wallet, "normalise_nimiq_address", side_effect=identity), \
             mock.patch.object(
                 trans_updater,
                 "_normalise_address_for_compare",
                 side_effect=lambda value: str(value).strip().lower(),
             ), \
             mock.patch.object(
                 trans_updater.db_access,
                 "create_spot_deposit_transaction",
                 mock.AsyncMock(),
             ) as create_transaction:
            with self.assertRaisesRegex(ValueError, "original funding wallet"):
                await trans_updater.record_spot_deposit_transaction(
                    object(),
                    user_id=1,
                    spot_id=7,
                    amount=100,
                    from_address="wallet-b",
                    to_address="deposit-wallet",
                    tx_hash="wrong-wallet-topup",
                )

        create_transaction.assert_not_awaited()


class ClaimRuleWithoutLocationTest(unittest.IsolatedAsyncioTestCase):
    def _patch_rules(self, *, owner_id=99, capacity=True, reached_limit=False):
        return (
            mock.patch.object(db_access, "can_user_claim", mock.AsyncMock(return_value=True)),
            mock.patch.object(db_access, "get_public_spot", mock.AsyncMock(return_value={"availability_rank": 0})),
            mock.patch.object(
                db_access,
                "get_spot",
                mock.AsyncMock(return_value={schema.SPOT_CREATED_BY: owner_id}),
            ),
            mock.patch.object(
                db_access,
                "get_claim_distance_check",
                mock.AsyncMock(),
            ),
            mock.patch.object(
                db_access,
                "is_spot_claim_capacity_available",
                mock.AsyncMock(return_value=capacity),
            ),
            mock.patch.object(
                db_access,
                "has_user_reached_claim_limit",
                mock.AsyncMock(return_value=reached_limit),
            ),
            mock.patch.object(
                db_access,
                "has_spot_cancellation_started",
                mock.AsyncMock(return_value=False),
            ),
        )

    async def _check(self, *, owner_id=99, capacity=True, reached_limit=False):
        patches = self._patch_rules(
            owner_id=owner_id,
            capacity=capacity,
            reached_limit=reached_limit,
        )
        started = [patcher.start() for patcher in patches]
        self.addCleanup(lambda: [patcher.stop() for patcher in reversed(patches)])
        result = await db_access.get_claim_rule_check(
            object(),
            spot_id=7,
            user_id=1,
            lat=None,
            long=None,
        )
        started[3].assert_not_awaited()
        return result

    async def test_own_spot_supersedes_unknown_location(self):
        result = await self._check(owner_id=1, capacity=False, reached_limit=True)
        self.assertEqual(result["reason"], "own_spot")

    async def test_exhausted_capacity_supersedes_unknown_location(self):
        result = await self._check(capacity=False, reached_limit=True)
        self.assertEqual(result["reason"], "capacity_full")

    async def test_user_limit_supersedes_unknown_location(self):
        result = await self._check(capacity=True, reached_limit=True)
        self.assertEqual(result["reason"], "user_limit_reached")

    async def test_unknown_location_is_used_only_without_harder_blocker(self):
        result = await self._check(capacity=True, reached_limit=False)
        self.assertEqual(result["reason"], "location_unknown")
        self.assertFalse(result["allowed"])
        self.assertFalse(result["location_known"])


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
