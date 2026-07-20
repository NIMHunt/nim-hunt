from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# trans_updater.py
# ---------------------------------------------------------------------------
path = ROOT / "trans_updater.py"
text = path.read_text()

text = replace_once(
    text,
    '''    ``startAt`` is an optional transaction-hash cursor.  When no cursor is
    requested, omit it entirely rather than sending an empty string: an empty
    string is not a valid Nimiq transaction hash and some RPC servers reject
    the whole request as invalid parameters.
    """
    params: list[Any] = [address, int(max_transactions)]
    clean_start_at = str(start_at or "").strip()
    if clean_start_at:
        if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_start_at):
            raise ValueError("start_at must be a 64-character hexadecimal Nimiq transaction hash")
        params.append(clean_start_at)
''',
    '''    ``startAt`` is a transaction-hash cursor.  The currently deployed
    TestAlbatross RPC requires the third positional parameter even when no
    cursor is used, but rejects an empty string.  JSON ``null`` represents the
    unused cursor without pretending that an empty value is a transaction hash.
    """
    params: list[Any] = [address, int(max_transactions), None]
    clean_start_at = str(start_at or "").strip()
    if clean_start_at:
        if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_start_at):
            raise ValueError("start_at must be a 64-character hexadecimal Nimiq transaction hash")
        params[2] = clean_start_at
''',
    label="address history RPC parameters",
)

anchor = '''

async def verify_chain_details_for_record(
'''
insert = '''

def _normalise_chain_account_type(value: Any) -> str | None:
    """Return a stable name for account-type values exposed by Nimiq RPC."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return {0: "basic", 1: "vesting", 2: "htlc", 3: "staking"}.get(value)

    text = re.sub(r"[^a-z0-9]", "", str(value).strip().lower())
    aliases = {
        "0": "basic",
        "basic": "basic",
        "basicaccount": "basic",
        "1": "vesting",
        "vesting": "vesting",
        "vestingcontract": "vesting",
        "2": "htlc",
        "htlc": "htlc",
        "hashedtimelockedcontract": "htlc",
        "3": "staking",
        "staking": "staking",
        "stakingcontract": "staking",
    }
    return aliases.get(text)


def _first_chain_scalar_for_keys(value: Any, keys: set[str]) -> Any | None:
    keys_lc = {key.lower() for key in keys}
    for path, item in _walk_json(value):
        if not path or isinstance(item, (dict, list)):
            continue
        if path[-1].lower() in keys_lc:
            return item
    return None


def _normalise_chain_timestamp_milliseconds(value: Any) -> int | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    # Nimiq RPC normally returns milliseconds.  Accept seconds defensively.
    return timestamp * 1000 if timestamp < 10_000_000_000 else timestamp


async def get_chain_account_by_address(
    address: str,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> RowDict:
    """Return one account object from the configured Nimiq RPC."""
    clean_address = _validate_nimiq_address(address, field_name="account address")
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method="getAccountByAddress",
        params=[clean_address],
        timeout_seconds=int(timeout_seconds),
    )
    data, _metadata = _unwrap_rpc_result(result)
    if not isinstance(data, dict):
        raise RuntimeError("Nimiq RPC getAccountByAddress returned no account object")
    return dict(data)


async def resolve_nimiq_pay_payout_address(
    address: str,
    *,
    source_tx_hash: str | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> str:
    """Resolve a Nimiq Pay account to a safe basic-account payout address.

    Nimiq Pay may expose an HTLC contract through ``listAccounts()``.  Existing
    HTLCs reject ordinary incoming transfers, so sending a refund or reward back
    to the contract address creates an on-chain failed transaction.  For a
    refund, the original deposit transaction tells us which HTLC party
    authorised the payment: the recipient before/equal to timeout, otherwise
    the sender.  For a reward with no source transaction, the contract recipient
    is the beneficiary selected by Nimiq Pay.

    Unsupported contract types and incomplete RPC data fail closed before a
    durable outgoing intent is created or any transaction is broadcast.
    """
    clean_address = _validate_nimiq_address(address, field_name="payout address")

    source_status: ChainTransactionStatus | None = None
    source_type: str | None = None
    if source_tx_hash:
        clean_hash = str(source_tx_hash).strip()
        if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash):
            raise RuntimeError("payout source transaction hash is invalid")
        source_status = await get_chain_transaction_status(
            clean_hash,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
        if source_status.status != "confirmed":
            raise RuntimeError(
                "payout source transaction could not be re-verified as confirmed"
            )
        source_type = _normalise_chain_account_type(
            _first_chain_scalar_for_keys(
                source_status.raw,
                {"fromType", "senderType", "from_type", "sender_type"},
            )
        )
        if source_type == "basic":
            return clean_address
        if source_type not in {None, "htlc"}:
            raise RuntimeError(
                f"payout source account type {source_type!r} is not supported"
            )

    account = await get_chain_account_by_address(
        clean_address,
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )
    account_type = _normalise_chain_account_type(account.get("type"))
    if account_type == "basic":
        if source_type == "htlc":
            # The contract has been pruned since the payment and its HTLC party
            # metadata is no longer available.  Never pay the now-empty contract
            # address as though it were a newly-created basic account.
            raise RuntimeError("the source HTLC was pruned before its beneficiary could be resolved")
        return clean_address
    if account_type != "htlc":
        raise RuntimeError(
            f"payout address account type {account_type or account.get('type')!r} is not supported"
        )

    target_field = "recipient"
    if source_status is not None:
        transaction_time = _normalise_chain_timestamp_milliseconds(
            _first_chain_scalar_for_keys(source_status.raw, {"timestamp", "time"})
        )
        timeout = _normalise_chain_timestamp_milliseconds(account.get("timeout"))
        if transaction_time is None or timeout is None:
            raise RuntimeError("HTLC payout party could not be resolved from transaction time and timeout")
        # Regular HTLC transfers are authorised by the recipient through the
        # timeout; after it, only the original sender can resolve the contract.
        target_field = "recipient" if transaction_time <= timeout else "sender"

    target = _validate_nimiq_address(
        str(account.get(target_field) or ""),
        field_name=f"HTLC {target_field} payout address",
    )
    target_account = await get_chain_account_by_address(
        target,
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )
    target_type = _normalise_chain_account_type(target_account.get("type"))
    if target_type != "basic":
        raise RuntimeError(
            f"resolved HTLC {target_field} is not a basic account"
        )
    return target
'''
if anchor not in text:
    raise RuntimeError("account-resolution insertion anchor missing")
text = text.replace(anchor, insert + anchor, 1)

text = replace_once(
    text,
    '''        refund_address = next((
            str(row.get(schema.TRANS_FROM_ADDRESS) or "").strip()
            for row in confirmed_deposits
            if str(row.get(schema.TRANS_FROM_ADDRESS) or "").strip()
        ), None)
        if refund_amount > 0 and not refund_address:
            raise ValueError("cannot refund this spot because no original deposit sender address is recorded")
''',
    '''        refund_source = next((
            row
            for row in confirmed_deposits
            if str(row.get(schema.TRANS_FROM_ADDRESS) or "").strip()
        ), None)
        refund_address = (
            str(refund_source.get(schema.TRANS_FROM_ADDRESS) or "").strip()
            if refund_source is not None
            else None
        )
        refund_source_tx_hash = (
            str(refund_source.get(schema.TRANS_TX_HASH) or "").strip()
            if refund_source is not None
            else None
        )
        if refund_amount > 0 and not refund_address:
            raise ValueError("cannot refund this spot because no original deposit sender address is recorded")
''',
    label="refund source selection",
)

text = replace_once(
    text,
    '''    try:
        fee_result = (
''',
    '''    resolved_refund_address = refund_address
    if refund_amount > 0:
        try:
            resolved_refund_address = await resolve_nimiq_pay_payout_address(
                str(refund_address),
                source_tx_hash=refund_source_tx_hash,
            )
        except Exception as exc:
            logger.warning(
                "Cancellation refund address resolution deferred: spot_id=%s error=%s",
                int(spot_id),
                wallet.redact_secret_values(exc),
            )
            await notify_cancellation_change()
            return {
                **result_base,
                "cancelled": False,
                "cancellation_pending": True,
                "deferred": True,
                "reason": "refund_address_resolution_pending",
                "message": "Cancellation is queued while the original Nimiq Pay funding account is resolved safely.",
            }
        result_base["refund_address"] = resolved_refund_address

    try:
        fee_result = (
''',
    label="refund preflight insertion",
)

text = replace_once(
    text,
    '''                db, spot_id=int(spot_id), to_address=str(refund_address), amount=refund_amount
''',
    '''                db, spot_id=int(spot_id), to_address=str(resolved_refund_address), amount=refund_amount
''',
    label="resolved refund destination",
)

text = replace_once(
    text,
    '''    if not clean_to_address:
        raise ValueError("claim has no payout_address; ask the user to enter through Nimiq Pay again")

    transaction_kind = (
''',
    '''    if not clean_to_address:
        raise ValueError("claim has no payout_address; ask the user to enter through Nimiq Pay again")

    clean_to_address = await resolve_nimiq_pay_payout_address(clean_to_address)

    transaction_kind = (
''',
    label="claim payout address resolution",
)

path.write_text(text)


# ---------------------------------------------------------------------------
# cancellation_safety.py
# ---------------------------------------------------------------------------
path = ROOT / "cancellation_safety.py"
text = path.read_text()

text = replace_once(
    text,
    '''_GUARD_TABLE = "CANCELLATION_SEND_GUARD"
_INSTALLED = False
''',
    '''_GUARD_TABLE = "CANCELLATION_SEND_GUARD"
_RETRYABLE_FAILED_REFUND_GUARD_REASON = "existing failed or ambiguous cancellation transaction"
_INSTALLED = False
''',
    label="guard reason constant",
)

anchor = '''

def _blocked_result(*, spot_id: int, reason: str) -> RowDict:
'''
insert = '''

def _single_retryable_failed_refund(transactions: list[RowDict]) -> RowDict | None:
    """Return the sole failed refund eligible for one proven-safe retry."""
    failed_refunds = [
        row for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CANCEL_SPOT
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        == const.TRANS_STATUS_FAILED
    ]
    active_refunds = [
        row for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_CANCEL_SPOT
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        != const.TRANS_STATUS_FAILED
    ]
    failed_fees = [
        row for row in transactions
        if int(row.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_PLAT_FEE
        and int(row.get(schema.TRANS_STATUS) if row.get(schema.TRANS_STATUS) is not None else -1)
        == const.TRANS_STATUS_FAILED
    ]
    if len(failed_refunds) != 1 or active_refunds or failed_fees:
        return None
    return failed_refunds[0]


async def _failed_refund_is_definitively_failed(row: RowDict) -> bool:
    """Re-check that a stored failed refund really executed unsuccessfully."""
    tx_hash = str(row.get(schema.TRANS_TX_HASH) or "").strip()
    if not trans_updater._NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        return False
    status = await trans_updater.get_chain_transaction_status(tx_hash)
    return (
        status.status == "failed"
        and trans_updater._execution_result_is_failure(status.raw)
    )
'''
if anchor not in text:
    raise RuntimeError("cancellation safety insertion anchor missing")
text = text.replace(anchor, insert + anchor, 1)

old = '''        existing_guard = await _guard_row(db, spot_id=spot_id)
        if existing_guard is not None:
            return _blocked_result(
                spot_id=spot_id,
                reason=str(existing_guard.get("reason") or existing_guard.get("state")),
            )

        transactions = await db_access.get_transactions_by_spot(
            db,
            spot_id=spot_id,
            limit=db_access.MAX_LIMIT,
        )
        failed_legs = _failed_cancellation_legs(transactions)
        if failed_legs:
            await _block_guard(
                db,
                spot_id=spot_id,
                reason="existing failed or ambiguous cancellation transaction",
            )
            return _blocked_result(
                spot_id=spot_id,
                reason="existing failed or ambiguous cancellation transaction",
            )
'''
new = '''        transactions = await db_access.get_transactions_by_spot(
            db,
            spot_id=spot_id,
            limit=db_access.MAX_LIMIT,
        )
        retryable_refund = _single_retryable_failed_refund(transactions)
        refund_failure_proven = False
        if retryable_refund is not None:
            try:
                refund_failure_proven = await _failed_refund_is_definitively_failed(
                    retryable_refund
                )
            except Exception:
                refund_failure_proven = False

        existing_guard = await _guard_row(db, spot_id=spot_id)
        if existing_guard is not None:
            guard_reason = str(existing_guard.get("reason") or existing_guard.get("state"))
            recoverable_failed_refund_guard = (
                str(existing_guard.get("state") or "") == "blocked"
                and guard_reason == _RETRYABLE_FAILED_REFUND_GUARD_REASON
                and refund_failure_proven
            )
            if recoverable_failed_refund_guard:
                await _release_guard(db, spot_id=spot_id)
            else:
                return _blocked_result(spot_id=spot_id, reason=guard_reason)

        failed_legs = _failed_cancellation_legs(transactions)
        if failed_legs and not refund_failure_proven:
            await _block_guard(
                db,
                spot_id=spot_id,
                reason=_RETRYABLE_FAILED_REFUND_GUARD_REASON,
            )
            return _blocked_result(
                spot_id=spot_id,
                reason=_RETRYABLE_FAILED_REFUND_GUARD_REASON,
            )
'''
text = replace_once(text, old, new, label="failed refund retry guard")
path.write_text(text)


# ---------------------------------------------------------------------------
# RPC parameter tests
# ---------------------------------------------------------------------------
path = ROOT / "tests" / "test_nimiq_address_history_rpc_params.py"
text = path.read_text()
text = text.replace(
    "test_unused_start_cursor_is_omitted_from_rpc_params",
    "test_unused_start_cursor_is_sent_as_json_null",
)
text = text.replace('self.assertEqual(kwargs["params"], [ADDRESS, 123])', 'self.assertEqual(kwargs["params"], [ADDRESS, 123, None])')
text = text.replace(
    "test_cancellation_recovery_succeeds_when_rpc_accepts_only_two_default_params",
    "test_cancellation_recovery_succeeds_with_null_default_cursor",
)
text = text.replace('self.assertEqual(kwargs["params"], [ADDRESS, 500])', 'self.assertEqual(kwargs["params"], [ADDRESS, 500, None])')
path.write_text(text)


# ---------------------------------------------------------------------------
# Payout-address resolution tests
# ---------------------------------------------------------------------------
(ROOT / "tests" / "test_nimiq_payout_address_resolution.py").write_text('''from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import trans_updater

SOURCE = const.DEV_PLATFORM_FEE_ADDRESS
RECIPIENT = const.DEV_SECOND_FUNDING_ADDRESS
SENDER = const.DEV_FUNDING_ADDRESS
HASH = "ab" * 32


class NimiqPayoutAddressResolutionTests(IsolatedAsyncioTestCase):
    async def test_basic_account_is_returned_unchanged(self):
        with mock.patch.object(
            trans_updater,
            "get_chain_account_by_address",
            mock.AsyncMock(return_value={"type": "basic", "address": SOURCE}),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(SOURCE)
        self.assertEqual(result, SOURCE)

    async def test_htlc_reward_uses_contract_recipient(self):
        accounts = [
            {
                "type": "htlc",
                "address": SOURCE,
                "recipient": RECIPIENT,
                "sender": SENDER,
                "timeout": 2_000_000_000_000,
            },
            {"type": "basic", "address": RECIPIENT},
        ]
        with mock.patch.object(
            trans_updater,
            "get_chain_account_by_address",
            mock.AsyncMock(side_effect=accounts),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(SOURCE)
        self.assertEqual(result, RECIPIENT)

    async def test_htlc_refund_before_timeout_uses_recipient(self):
        source_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=HASH,
            raw={"fromType": 2, "timestamp": 1_000_000_000_000},
        )
        accounts = [
            {
                "type": "htlc",
                "recipient": RECIPIENT,
                "sender": SENDER,
                "timeout": 1_500_000_000_000,
            },
            {"type": "basic", "address": RECIPIENT},
        ]
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=source_status),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_account_by_address",
                mock.AsyncMock(side_effect=accounts),
            ),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(
                SOURCE,
                source_tx_hash=HASH,
            )
        self.assertEqual(result, RECIPIENT)

    async def test_htlc_refund_after_timeout_uses_sender(self):
        source_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=HASH,
            raw={"fromType": "htlc", "timestamp": 2_000_000_000_001},
        )
        accounts = [
            {
                "type": "htlc",
                "recipient": RECIPIENT,
                "sender": SENDER,
                "timeout": 2_000_000_000_000,
            },
            {"type": "basic", "address": SENDER},
        ]
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=source_status),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_account_by_address",
                mock.AsyncMock(side_effect=accounts),
            ),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(
                SOURCE,
                source_tx_hash=HASH,
            )
        self.assertEqual(result, SENDER)

    async def test_pruned_source_htlc_fails_closed(self):
        source_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=HASH,
            raw={"fromType": 2, "timestamp": 1_000_000_000_000},
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=source_status),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_account_by_address",
                mock.AsyncMock(return_value={"type": "basic", "address": SOURCE}),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "pruned"):
                await trans_updater.resolve_nimiq_pay_payout_address(
                    SOURCE,
                    source_tx_hash=HASH,
                )

    async def test_unsupported_contract_type_fails_before_send(self):
        with mock.patch.object(
            trans_updater,
            "get_chain_account_by_address",
            mock.AsyncMock(return_value={"type": "vesting", "address": SOURCE}),
        ):
            with self.assertRaisesRegex(RuntimeError, "not supported"):
                await trans_updater.resolve_nimiq_pay_payout_address(SOURCE)
''')


# ---------------------------------------------------------------------------
# Cancellation safety tests
# ---------------------------------------------------------------------------
path = ROOT / "tests" / "test_cancellation_safety.py"
text = path.read_text()
old_test = '''    async def test_failed_refund_attempt_blocks_all_automatic_resends(self) -> None:
        failed_refund = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
        }

        with (
            mock.patch.object(
                cancellation_safety,
                "_ensure_guard_table",
                mock.AsyncMock(),
            ),
            mock.patch.object(
                cancellation_safety,
                "_guard_row",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[failed_refund]),
            ),
            mock.patch.object(
                cancellation_safety,
                "_block_guard",
                mock.AsyncMock(),
            ) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
                cancellation_fee=10,
                fee_address="NQ34 fee",
            )

        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.assertTrue(result["manual_review_required"])
        self.original.assert_not_awaited()
        block_guard.assert_awaited_once()
'''
new_test = '''    async def test_one_proven_failed_refund_is_retried_once(self) -> None:
        failed_refund = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
            schema.TRANS_TX_HASH: "ab" * 32,
        }
        self.original.return_value = {
            "ok": True,
            "spot_id": 7,
            "cancelled": False,
            "cancellation_pending": True,
            "refund": {"ok": True},
        }
        failed_status = cancellation_safety.trans_updater.ChainTransactionStatus(
            status="failed",
            tx_hash="ab" * 32,
            raw={"executionResult": False},
        )

        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[failed_refund]),
            ),
            mock.patch.object(
                cancellation_safety.trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=failed_status),
            ),
            mock.patch.object(cancellation_safety, "_acquire_guard", mock.AsyncMock(return_value=True)),
            mock.patch.object(cancellation_safety, "_release_guard", mock.AsyncMock()) as release_guard,
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(),
                spot_id=7,
                cancellation_fee=10,
                fee_address="NQ34 fee",
            )

        self.assertEqual(result["refund"], {"ok": True})
        self.original.assert_awaited_once()
        release_guard.assert_awaited_once()
        block_guard.assert_not_awaited()

    async def test_ambiguous_failed_refund_remains_blocked(self) -> None:
        failed_refund = {
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
            schema.TRANS_TX_HASH: "not-a-chain-hash",
        }
        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=[failed_refund]),
            ),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(), spot_id=7
            )
        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.original.assert_not_awaited()
        block_guard.assert_awaited_once()

    async def test_second_failed_refund_is_never_retried(self) -> None:
        failed_refunds = [
            {
                schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
                schema.TRANS_STATUS: const.TRANS_STATUS_FAILED,
                schema.TRANS_TX_HASH: value * 32,
            }
            for value in ("ab", "cd")
        ]
        with (
            mock.patch.object(cancellation_safety, "_ensure_guard_table", mock.AsyncMock()),
            mock.patch.object(cancellation_safety, "_guard_row", mock.AsyncMock(return_value=None)),
            mock.patch.object(
                cancellation_safety.db_access,
                "get_transactions_by_spot",
                mock.AsyncMock(return_value=failed_refunds),
            ),
            mock.patch.object(cancellation_safety, "_block_guard", mock.AsyncMock()) as block_guard,
        ):
            result = await cancellation_safety.guarded_submit_spot_cancellation_transactions(
                object(), spot_id=7
            )
        self.assertEqual(result["reason"], "manual_reconciliation_required")
        self.original.assert_not_awaited()
        block_guard.assert_awaited_once()
'''
text = replace_once(text, old_test, new_test, label="cancellation safety tests")
path.write_text(text)

print("Applied HTLC payout/refund repair")
