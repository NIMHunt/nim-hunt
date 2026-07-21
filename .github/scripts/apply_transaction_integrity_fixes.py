from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Restore the intended creator-facing funding rule: the complete combined
# creator deposit makes a draft publishable. The creation-fee leg remains an
# internal, separately reconciled transfer and may finish after publication.
# ---------------------------------------------------------------------------
replace_once(
    "db_access.py",
    '''        WHERE s.{schema.SPOT_STATUS} = ?
          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
''',
    '''        WHERE s.{schema.SPOT_STATUS} IN (?, ?, ?)
          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
''',
)
replace_once(
    "db_access.py",
    '''        (
            const.SPOT_STATUS_DRAFT,
            const.TRANS_TYPE_FILL_SPOT,
''',
    '''        (
            const.SPOT_STATUS_DRAFT,
            const.SPOT_STATUS_PUBLISHED,
            const.SPOT_STATUS_COMPLETED,
            const.TRANS_TYPE_FILL_SPOT,
''',
)
replace_once(
    "db_access.py",
    '''    confirmed_amount = await get_confirmed_spot_deposit_total(db, spot_id=spot_id)
    if confirmed_amount < spot_required_deposit_amount(spot):
        return False
    return await has_confirmed_spot_creation_fee_transaction(db, spot_id=spot_id)
''',
    '''    confirmed_amount = await get_confirmed_spot_deposit_total(db, spot_id=spot_id)
    if confirmed_amount < spot_required_deposit_amount(spot):
        return False

    # The creator deposits the Spot value and the snapshotted creation fee in one
    # combined payment. Once that complete deposit confirms, the draft is funded.
    # The internal fee transfer is reconciled independently and must not make the
    # creator wait to publish a fully funded Spot.
    return True
''',
)

replace_once(
    "trans_updater.py",
    '''    """Send one fully funded draft's snapshotted creation fee.
''',
    '''    """Send one fully funded Spot's snapshotted creation fee.
''',
)
replace_once(
    "trans_updater.py",
    '''    spot_status_value = spot.get(schema.SPOT_STATUS)
    if spot_status_value is None or int(spot_status_value) != const.SPOT_STATUS_DRAFT:
        raise ValueError("creation fees can only be submitted for draft spots")
''',
    '''    spot_status_value = spot.get(schema.SPOT_STATUS)
    allowed_statuses = {
        const.SPOT_STATUS_DRAFT,
        const.SPOT_STATUS_PUBLISHED,
        const.SPOT_STATUS_COMPLETED,
    }
    if spot_status_value is None or int(spot_status_value) not in allowed_statuses:
        raise ValueError("creation fees can only be submitted for funded, non-cancelled spots")
''',
)
replace_once(
    "trans_updater.py",
    '''    """Submit missing creation fees for fully funded drafts.
''',
    '''    """Submit missing creation fees for fully funded Spots.
''',
)

replace_once(
    "public_html.py",
    '''_ASSET_VERSION = "blockchain-flow-v1-20260720"
''',
    '''_ASSET_VERSION = "transaction-integrity-v1-20260721"
''',
)
replace_once(
    "public_html.py",
    '''    Deposits may arrive in parts. The server requests only the still-unsubmitted
    portion of ``Spot value + creation fee`` and does not consider the draft
    publishable until the creation-fee transaction itself confirms.
''',
    '''    Deposits may arrive in parts. The server requests only the still-unsubmitted
    portion of ``Spot value + creation fee``. Once that combined creator deposit
    confirms, the draft is publishable; the internal fee transfer is reported and
    reconciled separately.
''',
)
replace_once(
    "public_html.py",
    '''    if submitted_amount <= 0:
        status_value = "missing"
        status_label = "No Deposit"
    elif not funding_complete:
        status_value = "partial"
        status_label = "Partial Deposit"
    elif not fee_paid:
        status_value = "processing"
        status_label = "Creation Fee Processing"
    else:
        status_value = "ready"
        status_label = "Ready"
''',
    '''    if submitted_amount <= 0:
        status_value = "missing"
        status_label = "No Deposit"
    elif not funding_complete:
        status_value = "partial"
        status_label = "Partial Deposit"
    else:
        status_value = "ready"
        status_label = "Ready"
''',
)
replace_once(
    "public_html.py",
    '''    minimum_payout_ok = total_value >= minimum_payout * payout_divisor
    fully_funded = bool(deposit.get("funding_complete"))
    creation_fee_paid = bool(deposit.get("fee_paid"))
    ready_to_publish = fully_funded and creation_fee_paid

    publish_block_reason = None
    publish_block_message = None
    if fully_funded and not creation_fee_paid:
        publish_block_reason = "creation_fee_processing"
        publish_block_message = "The creation fee must confirm before publishing."
    elif ready_to_publish and draft_end_time_elapsed:
        publish_block_reason = "end_time_elapsed"
        publish_block_message = "The configured end time has already elapsed."
    elif ready_to_publish and not minimum_payout_ok:
''',
    '''    minimum_payout_ok = total_value >= minimum_payout * payout_divisor
    fully_funded = bool(deposit.get("funding_complete"))
    ready_to_publish = fully_funded

    publish_block_reason = None
    publish_block_message = None
    if ready_to_publish and draft_end_time_elapsed:
        publish_block_reason = "end_time_elapsed"
        publish_block_message = "The configured end time has already elapsed."
    elif ready_to_publish and not minimum_payout_ok:
''',
)

# ---------------------------------------------------------------------------
# Never confirm a transaction whose execution failed. Nimiq RPC responses are
# not always shaped identically, so inspect nested metadata as well as the top
# level. This protects direct hash lookup and both address-history fallbacks.
# ---------------------------------------------------------------------------
replace_once(
    "trans_updater.py",
    '''def _execution_result_is_failure(value: Any) -> bool:
    return isinstance(value, dict) and value.get("executionResult") is False
''',
    '''def _execution_result_is_failure(value: Any) -> bool:
    """Return True when any RPC layer explicitly reports failed execution."""
    for path, item in _walk_json(value):
        if not path:
            continue
        key = path[-1].replace("_", "").lower()
        if key == "executionresult" and item is False:
            return True
    return False
''',
)
replace_once(
    "trans_updater.py",
    '''    raw = status.raw
    chain_from = _extract_chain_from_address(raw)
''',
    '''    raw = status.raw
    if _execution_result_is_failure(raw):
        return VerifiedChainDetails(
            ok=False,
            reason="confirmed transaction explicitly reported a failed execution result",
        )

    chain_from = _extract_chain_from_address(raw)
''',
)
replace_once(
    "trans_updater.py",
    '''        matched = _find_transaction_by_hash(history, tx_hash)
        if matched is not None:
            return ChainTransactionStatus(
                status="confirmed",
                tx_hash=tx_hash,
                block_number=_extract_block_number(matched),
                raw=matched,
                reason="found through deposit-address history",
            )
''',
    '''        matched = _find_transaction_by_hash(history, tx_hash)
        if matched is not None:
            if _execution_result_is_failure(matched):
                return ChainTransactionStatus(
                    status="failed",
                    tx_hash=tx_hash,
                    block_number=_extract_block_number(matched),
                    raw=matched,
                    reason="deposit hash was found in address history with a failed execution result",
                )
            return ChainTransactionStatus(
                status="confirmed",
                tx_hash=tx_hash,
                block_number=_extract_block_number(matched),
                raw=matched,
                reason="found through deposit-address history",
            )
''',
)

# ---------------------------------------------------------------------------
# Show owners the actual refund destination/hash. Nimiq Pay may fund from an
# HTLC; safe cancellation then pays the contract's Basic beneficiary rather
# than attempting an ordinary transfer back into the HTLC contract itself.
# ---------------------------------------------------------------------------
replace_once(
    "public_html.py",
    '''    remaining_lost = remaining_amount > 0 and refund_amount <= 0
    return {
''',
    '''    remaining_lost = remaining_amount > 0 and refund_amount <= 0

    refund_transactions = [
        trans
        for trans in transactions
        if int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CANCEL_SPOT
    ]
    refund_transactions.sort(
        key=lambda trans: (
            int(trans.get(schema.TRANS_CREATED_AT) or 0),
            int(trans.get(schema.TRANS_ID) or 0),
        ),
        reverse=True,
    )
    latest_refund = refund_transactions[0] if refund_transactions else None
    refund_transaction = None
    if latest_refund is not None:
        refund_transaction = {
            "status": _transaction_status_label(latest_refund.get(schema.TRANS_STATUS)),
            "amount": int(latest_refund.get(schema.TRANS_AMOUNT) or 0),
            "to_address": latest_refund.get(schema.TRANS_TO_ADDRESS),
            "tx_hash": latest_refund.get(schema.TRANS_TX_HASH),
            "block_number": latest_refund.get(schema.TRANS_BLOCK_NUMBER),
        }

    return {
''',
)
replace_once(
    "public_html.py",
    '''        "remaining_lost": remaining_lost,
        "fee_address": getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
''',
    '''        "remaining_lost": remaining_lost,
        "refund_transaction": refund_transaction,
        "fee_address": getattr(const, "SPOT_CANCELLATION_FEE_ADDRESS", ""),
''',
)

# ---------------------------------------------------------------------------
# Duration claims: distinguish condition verification from payout processing,
# and keep polling until a successful standard payout is actually confirmed.
# ---------------------------------------------------------------------------
replace_once(
    "public_html.py",
    '''    status_label: str,
    payout: dict[str, Any],
) -> dict[str, str]:
''',
    '''    status_label: str,
    payout: dict[str, Any],
    duration_remaining: int = 0,
) -> dict[str, str]:
''',
)
replace_once(
    "public_html.py",
    '''    if not is_prizedraw:
        if status_label == "success":
            return {"label": "success", "text": "Success", "class": "success"}
        if status_label == "failed":
            return {"label": "failed", "text": "Failed", "class": "failed"}
        return {"label": "pending", "text": "Pending", "class": "pending"}
''',
    '''    if not is_prizedraw:
        if status_label == "failed":
            return {"label": "failed", "text": "Failed", "class": "failed"}
        if status_label == "success":
            if int(payout.get("payout_confirmed_count") or 0) > 0:
                return {"label": "success", "text": "Success", "class": "success"}
            return {
                "label": "success_processing",
                "text": "Success (Processing)",
                "class": "success",
            }
        if int(spot.get(schema.SPOT_CLAIM_DURATION) or 0) > 0 and int(duration_remaining) <= 0:
            return {"label": "verifying", "text": "Verifying", "class": "pending"}
        return {"label": "pending", "text": "Pending", "class": "pending"}
''',
)
replace_once(
    "public_html.py",
    '''        status_label=status_label,
        payout=payout,
    )
''',
    '''        status_label=status_label,
        payout=payout,
        duration_remaining=remaining,
    )
''',
)

replace_once(
    "static/claim_detail.js",
    '''        Boolean(claim?.viewer_is_recipient),
        Number(spot.success_claim_count || 0),
''',
    '''        Boolean(claim?.viewer_is_recipient),
        Number(claim?.payout_pending_count || 0),
        Number(claim?.payout_confirmed_count || 0),
        Number(claim?.payout_failed_count || 0),
        Number(spot.success_claim_count || 0),
''',
)
replace_once(
    "static/claim_detail.js",
    '''function createDurationTimerText(claim) {
    const span = document.createElement('span');
''',
    '''function createDurationTimerText(claim, statusKeyword) {
    const span = document.createElement('span');
''',
)
replace_once(
    "static/claim_detail.js",
    '''        span.textContent = reachedGoal
            ? ' (Verifying)'
            : ` (${formatSeconds(cappedElapsed)}/${formatSeconds(required)})`;

        if (reachedGoal && span._nhTimerId) {
''',
    '''        if (reachedGoal) {
            statusKeyword.textContent = 'Verifying';
            span.textContent = '';
        } else {
            span.textContent = ` (${formatSeconds(cappedElapsed)}/${formatSeconds(required)})`;
        }

        if (reachedGoal && span._nhTimerId) {
''',
)
replace_once(
    "static/claim_detail.js",
    '''function buildStatusWithTimer(claim) {
    const fragment = document.createDocumentFragment();
    fragment.append(buildStatusKeyword(claim));

    if (Number(claim.duration_required || 0) > 0) {
        fragment.append(createDurationTimerText(claim));
    }

    return fragment;
}
''',
    '''function buildStatusWithTimer(claim) {
    const fragment = document.createDocumentFragment();
    const statusKeyword = buildStatusKeyword(claim);
    fragment.append(statusKeyword);

    const status = String(claim.status_label || '').toLowerCase();
    const durationRemaining = Number(claim.duration_remaining || 0);
    if (Number(claim.duration_required || 0) > 0 && status === 'pending' && durationRemaining > 0) {
        fragment.append(createDurationTimerText(claim, statusKeyword));
    }

    return fragment;
}
''',
)
replace_once(
    "static/claim_detail.js",
    '''function claimNeedsLiveRefresh(claim) {
    if (!claim) return false;
    if (String(claim.status_label || '').toLowerCase() === 'pending') {
        if (Number(claim.duration_required || 0) > 0 && !durationGoalReached(claim)) return false;
        return true;
    }
    if (!claim.is_prizedraw) return false;
''',
    '''function claimNeedsLiveRefresh(claim) {
    if (!claim) return false;
    const status = String(claim.status_label || '').toLowerCase();
    if (status === 'pending') {
        if (Number(claim.duration_required || 0) > 0 && !durationGoalReached(claim)) return false;
        return true;
    }
    if (!claim.is_prizedraw) {
        return status === 'success' && Number(claim.payout_confirmed_count || 0) <= 0;
    }
''',
)

# ---------------------------------------------------------------------------
# Explain the one-time-code behaviour before a combined code+duration claim is
# started. The code is atomically consumed/reserved with the claim so it cannot
# be replayed while the duration condition is pending.
# ---------------------------------------------------------------------------
replace_once(
    "static/interface_text.js",
    '''            passwordRequired: 'A password is required.',
            passwordLabel: 'Password',
''',
    '''            passwordRequired: 'A password is required.',
            codeUsedWhenVerificationStarts: 'This one-time code is used when verification begins and is not restored if the duration check later fails.',
            passwordLabel: 'Password',
''',
)
replace_once(
    "static/find_spots.js",
    '''    const duration = durationText(spot.claim_duration);
    if (duration) rows.push(claimSummaryLine(claimText.durationRequired ? claimText.durationRequired(duration) : `You must remain within the area for ${duration}.`));
    if (status.requires_password || spot.use_password) rows.push(claimSummaryLine(claimText.passwordRequired || 'A password is required.'));

    els.claimSummary.replaceChildren(...rows);
''',
    '''    const duration = durationText(spot.claim_duration);
    const needsPassword = Boolean(status.requires_password || spot.use_password);
    if (duration) rows.push(claimSummaryLine(claimText.durationRequired ? claimText.durationRequired(duration) : `You must remain within the area for ${duration}.`));
    if (needsPassword) rows.push(claimSummaryLine(claimText.passwordRequired || 'A password is required.'));
    if (needsPassword && duration) {
        rows.push(claimSummaryLine(
            claimText.codeUsedWhenVerificationStarts
            || 'This one-time code is used when verification begins and is not restored if the duration check later fails.'
        ));
    }

    els.claimSummary.replaceChildren(...rows);
''',
)

# ---------------------------------------------------------------------------
# Close the Publish confirmation before showing a failure notice, otherwise the
# notice is correctly rendered but trapped visually behind the first backdrop.
# ---------------------------------------------------------------------------
replace_once(
    "static/my_spots.js",
    '''    } catch (err) {
        console.error(err);
        state.publishInProgress = false;
        els.publishConfirm.disabled = false;
        els.publishCancel.disabled = false;
        els.publishConfirm.textContent = TEXT.publish.confirm;
        showNotice({
''',
    '''    } catch (err) {
        console.error(err);
        state.publishInProgress = false;
        els.publishBackdrop.hidden = true;
        state.publishSpot = null;
        els.publishConfirm.disabled = false;
        els.publishCancel.disabled = false;
        els.publishConfirm.textContent = TEXT.publish.confirm;
        showNotice({
''',
)
replace_once(
    "static/interface_text.js",
    '''            reports: ({ pending, total }) => `Reports: ${pending} pending / ${total} total`,
''',
    '''            reports: ({ pending, total }) => `Reports: ${pending} pending / ${total} total`,
            refundTransaction: ({ amountText, destination, status, shortHash }) => `Refund: ${amountText} sent to ${destination} (${status}${shortHash ? `, tx ${shortHash}` : ''})`,
''',
)
replace_once(
    "static/my_spots.js",
    '''    appendBulletLine(lines, TEXT.spotDetail.claimRadius(Number(spot.radius || 0)));

    const maxClaimsPerUser = Number(spot.max_claims_per_user ?? 1);
''',
    '''    appendBulletLine(lines, TEXT.spotDetail.claimRadius(Number(spot.radius || 0)));

    const refundTransaction = spot.cancellation?.refund_transaction;
    if (refundTransaction?.to_address) {
        const txHash = String(refundTransaction.tx_hash || '').trim();
        const shortHash = txHash ? `${txHash.slice(0, 12)}…` : '';
        const refundLine = document.createElement('span');
        refundLine.textContent = TEXT.spotDetail.refundTransaction({
            amountText: nimFromLunaText(refundTransaction.amount || 0),
            destination: refundTransaction.to_address,
            status: refundTransaction.status || 'unknown',
            shortHash,
        });
        if (txHash) refundLine.title = txHash;
        appendBulletLine(lines, refundLine);
    }

    const maxClaimsPerUser = Number(spot.max_claims_per_user ?? 1);
''',
)

# Bust imported-module caches as well as the top-level page scripts.
replace_once(
    "static/my_spots.js",
    "from './interface_text.js?v=polish-live-v1-20260720';",
    "from './interface_text.js?v=transaction-integrity-v1-20260721';",
)
replace_once(
    "static/find_spots.js",
    "from './interface_text.js?v=polish-live-v1-20260720';",
    "from './interface_text.js?v=transaction-integrity-v1-20260721';",
)
replace_once(
    "static/claim_detail.js",
    "from './interface_text.js?v=polish-live-v1-20260720';",
    "from './interface_text.js?v=transaction-integrity-v1-20260721';",
)

# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
write(
    "tests/test_transaction_integrity_regressions.py",
    '''from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import constants as const
import database as schema
import db_access
import public_html
import trans_updater


BASIC_FROM = "NQ88 MK32 JK09 4U4P 2QXU D4BY JHCU C0AB SKB4"
BASIC_TO = "NQ54 FTGY F6VJ EJPU NSMN RA5Q 0K21 8EQT Q05P"
TX_HASH = "cf41b77531dd7fc5b3ae0ba26d1f95bd8eff6ffe6df612dcf01a0f50ba7a2692"


class TransactionExecutionResultTest(unittest.TestCase):
    def test_nested_failed_execution_is_never_verified(self):
        raw = {
            "data": {
                "transaction": {
                    "sender": BASIC_FROM,
                    "recipient": BASIC_TO,
                    "value": 39_900_000,
                    "executionResult": False,
                }
            }
        }
        self.assertTrue(trans_updater._execution_result_is_failure(raw))
        trans = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_FROM_ADDRESS: BASIC_FROM,
            schema.TRANS_TO_ADDRESS: BASIC_TO,
            schema.TRANS_AMOUNT: 39_900_000,
        }
        status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=TX_HASH,
            raw=raw,
        )
        verified = trans_updater._verify_chain_details_for_record(trans, status)
        self.assertFalse(verified.ok)
        self.assertIn("failed execution", str(verified.reason))

    def test_nested_success_is_not_mistaken_for_failure(self):
        self.assertFalse(
            trans_updater._execution_result_is_failure(
                {"data": {"transaction": {"executionResult": True}}}
            )
        )


class CreatorFundingPresentationTest(unittest.TestCase):
    def test_full_combined_deposit_is_ready_while_fee_is_reconciling(self):
        total_value = 40_000_000
        creation_fee = 100_000
        transactions = [{
            schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
            schema.TRANS_AMOUNT: total_value + creation_fee,
            schema.TRANS_CREATED_AT: 1,
        }]
        summary = public_html._deposit_summary(
            transactions,
            total_value=total_value,
            creation_fee=creation_fee,
            deposit_address=BASIC_FROM,
            creation_fee_address=BASIC_TO,
        )
        self.assertTrue(summary["funding_complete"])
        self.assertFalse(summary["fee_paid"])
        self.assertEqual(summary["fee_status"], "preparing")
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["status_label"], "Ready")

    def test_refund_summary_exposes_actual_basic_destination(self):
        transactions = [
            {
                schema.TRANS_ID: 1,
                schema.TRANS_TYPE: const.TRANS_TYPE_FILL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 40_100_000,
            },
            {
                schema.TRANS_ID: 2,
                schema.TRANS_TYPE: const.TRANS_TYPE_PLAT_FEE,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 100_000,
            },
            {
                schema.TRANS_ID: 3,
                schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_CONFIRMED,
                schema.TRANS_AMOUNT: 39_900_000,
                schema.TRANS_TO_ADDRESS: BASIC_TO,
                schema.TRANS_TX_HASH: TX_HASH,
                schema.TRANS_BLOCK_NUMBER: 6_609_204,
                schema.TRANS_CREATED_AT: 3,
            },
        ]
        summary = public_html._cancellation_summary(transactions)
        refund = summary["refund_transaction"]
        self.assertIsNotNone(refund)
        self.assertEqual(refund["status"], "confirmed")
        self.assertEqual(refund["to_address"], BASIC_TO)
        self.assertEqual(refund["tx_hash"], TX_HASH)


class PublishRuleTest(unittest.IsolatedAsyncioTestCase):
    async def test_internal_creation_fee_confirmation_does_not_block_publish(self):
        spot = {
            schema.SPOT_ID: 1,
            schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
            schema.SPOT_CREATED_BY: 7,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            schema.SPOT_TITLE: "Funded Spot",
            schema.SPOT_DEPOSIT_ADDRESS: BASIC_FROM,
            schema.SPOT_LAT: 55.0,
            schema.SPOT_LONG: -5.0,
            schema.SPOT_RADIUS: 200,
            schema.SPOT_MAX_CLAIMS_PER_USER: 1,
            schema.SPOT_MAX_TOTAL_CLAIMS: 1,
            schema.SPOT_TOTAL_VALUE: 10_000_000,
            schema.SPOT_CREATION_FEE: 100_000,
            schema.SPOT_STARTS_AT: None,
            schema.SPOT_ENDS_AT: 86_400,
            schema.SPOT_USE_PASSWORD: 0,
        }
        required = db_access.spot_required_deposit_amount(spot)
        with (
            patch.object(db_access, "get_spot", AsyncMock(return_value=spot)),
            patch.object(db_access, "can_user_create_spot", AsyncMock(return_value=True)),
            patch.object(db_access, "get_prizedraw", AsyncMock(return_value=None)),
            patch.object(db_access, "spot_meets_minimum_payout", AsyncMock(return_value=True)),
            patch.object(db_access, "get_confirmed_spot_deposit_total", AsyncMock(return_value=required)),
            patch.object(
                db_access,
                "has_confirmed_spot_creation_fee_transaction",
                AsyncMock(side_effect=AssertionError("internal fee must not be a publish prerequisite")),
            ),
        ):
            self.assertTrue(await db_access.can_publish_spot(object(), spot_id=1))

    async def test_fee_worker_selects_published_and_completed_spots(self):
        class FakeDb:
            def __init__(self):
                self.sql = ""
                self.params = ()

            async def execute_fetchall(self, sql, params):
                self.sql = sql
                self.params = params
                return []

        db = FakeDb()
        self.assertEqual(await db_access.get_spot_ids_ready_for_creation_fee(db), [])
        self.assertIn("IN (?, ?, ?)", db.sql)
        self.assertEqual(
            db.params[:3],
            (
                const.SPOT_STATUS_DRAFT,
                const.SPOT_STATUS_PUBLISHED,
                const.SPOT_STATUS_COMPLETED,
            ),
        )

    async def test_creation_fee_submission_accepts_published_spot(self):
        spot = {
            schema.SPOT_ID: 1,
            schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
            schema.SPOT_CANCELLATION_STARTED_AT: None,
            schema.SPOT_CREATION_FEE: 0,
        }
        with patch.object(db_access, "get_spot", AsyncMock(return_value=spot)):
            result = await trans_updater.submit_spot_creation_fee_transaction(object(), spot_id=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "zero_amount")


class ClaimStatusPresentationTest(unittest.TestCase):
    def test_elapsed_pending_duration_is_verifying(self):
        result = public_html._claim_display_status(
            claim={},
            spot={schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CLAIM_DURATION: 600},
            is_prizedraw=False,
            status_label="pending",
            payout={},
            duration_remaining=0,
        )
        self.assertEqual(result["text"], "Verifying")
        self.assertEqual(result["label"], "verifying")

    def test_success_waiting_for_payout_is_processing(self):
        result = public_html._claim_display_status(
            claim={},
            spot={schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CLAIM_DURATION: 600},
            is_prizedraw=False,
            status_label="success",
            payout={"payout_confirmed_count": 0},
            duration_remaining=0,
        )
        self.assertEqual(result["text"], "Success (Processing)")

    def test_confirmed_payout_is_plain_success(self):
        result = public_html._claim_display_status(
            claim={},
            spot={schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED, schema.SPOT_CLAIM_DURATION: 600},
            is_prizedraw=False,
            status_label="success",
            payout={"payout_confirmed_count": 1},
            duration_remaining=0,
        )
        self.assertEqual(result["text"], "Success")


class FrontendRegressionSourceTest(unittest.TestCase):
    def test_publish_failure_closes_confirmation_before_notice(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "my_spots.js").read_text()
        catch_start = source.index("async function confirmPublishSpot")
        catch_block = source[catch_start:source.index("function closeCancelModal", catch_start)]
        self.assertLess(
            catch_block.index("els.publishBackdrop.hidden = true;"),
            catch_block.index("showNotice({"),
        )

    def test_duration_success_does_not_append_verifying_suffix(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "claim_detail.js").read_text()
        self.assertNotIn("? ' (Verifying)'", source)
        self.assertIn("statusKeyword.textContent = 'Verifying';", source)
        self.assertIn("status === 'success' && Number(claim.payout_confirmed_count || 0) <= 0", source)

    def test_combined_code_duration_warning_is_present(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "find_spots.js").read_text()
        self.assertIn("codeUsedWhenVerificationStarts", source)


if __name__ == "__main__":
    unittest.main()
''',
)

# Remove the one-shot patch machinery from the resulting branch.
for relative in (
    ".github/scripts/apply_transaction_integrity_fixes.py",
    ".github/workflows/apply-transaction-integrity-fixes.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
