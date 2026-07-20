from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


trans_path = Path("trans_updater.py")
helper_anchor = '''async def check_pending_transaction(
    trans: RowDict,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
    user_deposit_stale_after_seconds: int = DEFAULT_USER_DEPOSIT_STALE_AFTER_SECONDS,
) -> ChainTransactionStatus:
'''
helper_and_anchor = '''async def _recover_server_transaction_from_address_history(
    trans: RowDict,
    chain_status: ChainTransactionStatus,
    *,
    rpc_url: str,
    timeout_seconds: int,
) -> ChainTransactionStatus | None:
    """Recover an existing outgoing hash when direct RPC lookup misses it.

    Nimiq public RPCs can return no result for ``getTransactionByHash`` even
    though the same confirmed transaction is present in the sender address's
    history.  Server-initiated rows must never be resent merely because of that
    indexing gap.  This fallback accepts only the exact hash already stored in
    SQLite; the normal strict sender/recipient/amount verifier still runs before
    the row is marked confirmed.
    """
    if not _is_server_initiated_transaction(trans):
        return None

    tx_hash = str(trans.get(schema.TRANS_TX_HASH) or chain_status.tx_hash or "").strip()
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        return None

    address = _verification_address_for_record(trans)
    if address is None:
        return None

    try:
        history = await get_chain_transactions_by_address(
            address,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
            max_transactions=int(getattr(const, "NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)),
        )
    except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, json.JSONDecodeError):
        # Address history is an additional proof source, not permission to turn
        # an ambiguous outgoing payment into a failure or a retry.
        return None

    matched = _find_transaction_by_hash(history, tx_hash)
    if matched is None:
        return None

    block_number = _extract_block_number(matched)
    if _execution_result_is_failure(matched):
        return ChainTransactionStatus(
            status="failed",
            tx_hash=tx_hash,
            block_number=block_number,
            raw=matched,
            reason="exact hash was found in expected-address history with a failed execution result",
        )

    return ChainTransactionStatus(
        status="confirmed",
        tx_hash=tx_hash,
        block_number=block_number,
        raw=matched,
        reason="exact hash recovered through expected-address transaction history",
    )


async def check_pending_transaction(
    trans: RowDict,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
    user_deposit_stale_after_seconds: int = DEFAULT_USER_DEPOSIT_STALE_AFTER_SECONDS,
) -> ChainTransactionStatus:
'''
replace_once(trans_path, helper_anchor, helper_and_anchor)

status_anchor = '''    chain_status = await get_chain_transaction_status(
        tx_hash,
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
    )

    if chain_status.status != "pending":
        return chain_status

    age_seconds = _transaction_age_seconds(trans)
'''
status_replacement = '''    chain_status = await get_chain_transaction_status(
        tx_hash,
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
    )

    if chain_status.status in {"pending", "unknown"}:
        recovered = await _recover_server_transaction_from_address_history(
            trans,
            chain_status,
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
        if recovered is not None:
            return recovered

    if chain_status.status != "pending":
        return chain_status

    age_seconds = _transaction_age_seconds(trans)
'''
replace_once(trans_path, status_anchor, status_replacement)

integration_path = Path("tests/test_blockchain_flow_integration.py")
replace_once(
    integration_path,
    "import constants as const\n",
    "import cache\nimport constants as const\n",
)
replace_once(
    integration_path,
    'HASH_3 = "33" * 32\n',
    'HASH_3 = "33" * 32\nHASH_4 = "44" * 32\n',
)
replace_once(
    integration_path,
    '''        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_path
        self._tmp.close()
''',
    '''        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()
''',
)

integration_append = '''

    async def test_confirmed_cancellation_hashes_missing_from_direct_lookup_recover_and_finalize(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        await self.record_confirmed_deposit(owner_id=owner_id, spot_id=spot_id, spot=spot)

        creation_fee = db_access.spot_creation_fee_amount(spot)
        self.assertGreater(creation_fee, 0)
        creation_fee_send = trans_updater.SubmittedChainTransaction(
            tx_hash=HASH_2,
            from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
            to_address=spot[schema.SPOT_CREATION_FEE_ADDRESS],
            amount=creation_fee,
        )

        async with schema.get_db() as db:
            with (
                mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
                mock.patch.object(
                    trans_updater,
                    "submit_chain_send_from_spot_deposit",
                    mock.AsyncMock(return_value=creation_fee_send),
                ),
            ):
                fee_result = await trans_updater.submit_spot_creation_fee_transaction(
                    db,
                    spot_id=spot_id,
                )

            fee_row = await db_access.get_transaction(db, trans_id=fee_result["trans_id"])
            await trans_updater.mark_trans_as_confirmed(
                db,
                fee_row,
                block_number=200,
                verified_details=trans_updater.VerifiedChainDetails(
                    ok=True,
                    from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                    to_address=spot[schema.SPOT_CREATION_FEE_ADDRESS],
                    amount=creation_fee,
                ),
            )

            await db.execute(
                f"UPDATE {schema.SPOT_TABLE_NAME} "
                f"SET {schema.SPOT_STATUS} = ? "
                f"WHERE {schema.SPOT_ID} = ?;",
                (const.SPOT_STATUS_PUBLISHED, spot_id),
            )
            await db.commit()

            hashes = iter((HASH_3, HASH_4))

            async def fake_cancellation_send(*, spot, to_address, amount, memo=None):
                return trans_updater.SubmittedChainTransaction(
                    tx_hash=next(hashes),
                    from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                    to_address=to_address,
                    amount=amount,
                )

            with (
                mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
                mock.patch.object(
                    trans_updater,
                    "submit_chain_send_from_spot_deposit",
                    side_effect=fake_cancellation_send,
                ),
            ):
                cancellation = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=const.SPOT_CANCELLATION_FEE,
                    fee_address=FUNDING_ADDRESS,
                )

            self.assertTrue(cancellation["cancellation_pending"])
            transactions = await db_access.get_transactions_by_spot(
                db,
                spot_id=spot_id,
                limit=50,
            )
            cancellation_rows = [
                row
                for row in transactions
                if int(row[schema.TRANS_TYPE])
                in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
            ]
            self.assertEqual(len(cancellation_rows), 2)
            self.assertTrue(
                all(int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_PENDING for row in cancellation_rows)
            )
            await cache.refresh_pending_transaction_cache(db)

        history = {
            "data": [
                {
                    "hash": row[schema.TRANS_TX_HASH],
                    "from": row[schema.TRANS_FROM_ADDRESS],
                    "to": row[schema.TRANS_TO_ADDRESS],
                    "value": int(row[schema.TRANS_AMOUNT]),
                    "blockNumber": 300 + index,
                    "executionResult": True,
                }
                for index, row in enumerate(cancellation_rows)
            ],
            "metadata": None,
        }

        async def direct_hash_lookup_misses(tx_hash, **kwargs):
            return trans_updater.ChainTransactionStatus(
                status="pending",
                tx_hash=tx_hash,
                reason="hash not found yet",
            )

        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                side_effect=direct_hash_lookup_misses,
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=history),
            ) as address_history,
            mock.patch.object(
                trans_updater,
                "submit_chain_send_from_spot_deposit",
                mock.AsyncMock(side_effect=AssertionError("reconciliation must not broadcast")),
            ) as resend,
            mock.patch.object(
                trans_updater,
                "refresh_chain_head_height",
                mock.AsyncMock(return_value=999),
            ),
        ):
            reconciled = await trans_updater.check_pending_transactions()

        self.assertEqual(reconciled["finalised_count"], 2)
        self.assertGreaterEqual(address_history.await_count, 2)
        resend.assert_not_awaited()

        async with schema.get_db() as db:
            final_spot = await db_access.get_spot(db, spot_id=spot_id)
            final_rows = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)

        self.assertEqual(int(final_spot[schema.SPOT_STATUS]), const.SPOT_STATUS_CANCELLED)
        final_cancellation_rows = [
            row
            for row in final_rows
            if int(row[schema.TRANS_TYPE])
            in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
        ]
        self.assertTrue(
            all(
                int(row[schema.TRANS_STATUS]) == const.TRANS_STATUS_CONFIRMED
                for row in final_cancellation_rows
            )
        )
'''
with integration_path.open("a", encoding="utf-8") as handle:
    handle.write(integration_append)

unit_path = Path("tests/test_server_transaction_history_recovery.py")
unit_path.write_text(
    '''from __future__ import annotations

import time
from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import database as schema
import trans_updater

HASH = "aa" * 32
ADDRESS = const.DEV_PLATFORM_FEE_ADDRESS


class ServerTransactionHistoryRecoveryTest(IsolatedAsyncioTestCase):
    def transaction(self, *, amount: int = 123_000) -> dict:
        return {
            schema.TRANS_ID: 41,
            schema.TRANS_TYPE: const.TRANS_TYPE_CANCEL_SPOT,
            schema.TRANS_STATUS: const.TRANS_STATUS_PENDING,
            schema.TRANS_TX_HASH: HASH,
            schema.TRANS_FROM_ADDRESS: ADDRESS,
            schema.TRANS_TO_ADDRESS: ADDRESS,
            schema.TRANS_AMOUNT: amount,
            schema.TRANS_CREATED_AT: int(time.time()),
        }

    def chain_transaction(self, *, amount: int = 123_000) -> dict:
        return {
            "hash": HASH,
            "from": ADDRESS,
            "to": ADDRESS,
            "value": amount,
            "blockNumber": 456,
            "executionResult": True,
        }

    async def test_pending_direct_lookup_recovers_exact_outgoing_hash_from_history(self):
        pending = trans_updater.ChainTransactionStatus(
            status="pending",
            tx_hash=HASH,
            reason="hash not found yet",
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value={"data": [self.chain_transaction()], "metadata": None}),
            ) as history,
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.tx_hash, HASH)
        self.assertIn("expected-address", result.reason)
        history.assert_awaited_once()
        verified = trans_updater._verify_chain_details_for_record(self.transaction(), result)
        self.assertTrue(verified.ok)

    async def test_unknown_direct_lookup_can_recover_exact_outgoing_hash_from_history(self):
        unknown = trans_updater.ChainTransactionStatus(
            status="unknown",
            tx_hash=HASH,
            reason="temporary direct lookup error",
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=unknown),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=[self.chain_transaction()]),
            ),
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "confirmed")

    async def test_absent_exact_hash_stays_pending_and_never_becomes_failed(self):
        pending = trans_updater.ChainTransactionStatus(
            status="pending",
            tx_hash=HASH,
            reason="hash not found yet",
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=[]),
            ),
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "pending")

    async def test_exact_hash_with_wrong_amount_does_not_pass_strict_verification(self):
        pending = trans_updater.ChainTransactionStatus(status="pending", tx_hash=HASH)
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(
                    return_value=[self.chain_transaction(amount=999_000)]
                ),
            ),
        ):
            result = await trans_updater.check_pending_transaction(self.transaction())

        self.assertEqual(result.status, "confirmed")
        verified = trans_updater._verify_chain_details_for_record(self.transaction(), result)
        self.assertFalse(verified.ok)
        self.assertIn("amount", verified.reason)
''',
    encoding="utf-8",
)
