from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:160]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "trans_updater.py",
    "The durable outbox row is created from NimHunt\\s own expected values.",
    "The durable outbox row is created from NimHunt's own expected values.",
)
replace_once(
    "trans_updater.py",
    "# payment\\s uniqueness guard merely because an RPC shape",
    "# payment's uniqueness guard merely because an RPC shape",
)

replace_once(
    "settlement_updater.py",
    '''            except RuntimeError:
                # A concurrent worker may have created the unique payout row
                # after our read. Recheck before treating that as an error.
                if await db_access.has_nonfailed_claim_payout_transaction(send_db, claim_id=claim_id):
                    return {
                        "ok": True,
                        "claim_id": claim_id,
                        "paid": False,
                        "already_exists": True,
                        "reason": "concurrent_payout_already_recorded",
                    }
                raise
''',
    '''            except RuntimeError as exc:
                # Only a uniqueness-guard failure proves that another worker
                # won the race. A helper/broadcast failure may leave our own
                # local intent pending and must remain visible as an error.
                duplicate_guard_hit = "already has a non-failed payout transaction" in str(exc)
                if (
                    duplicate_guard_hit
                    and await db_access.has_nonfailed_claim_payout_transaction(send_db, claim_id=claim_id)
                ):
                    return {
                        "ok": True,
                        "claim_id": claim_id,
                        "paid": False,
                        "already_exists": True,
                        "reason": "concurrent_payout_already_recorded",
                    }
                raise
''',
)

replace_once(
    "db_access.py",
    '''    _require_one(cur.rowcount, f"Failed to update pending transaction status id={trans_id}")
''',
    '''    if cur.rowcount == 1:
        return
    current = await get_transaction(db, trans_id=int(trans_id))
    if current is not None and int(current[schema.TRANS_STATUS]) == int(status):
        return
    _require_one(cur.rowcount, f"Failed to update pending transaction status id={trans_id}")
''',
)
replace_once(
    "db_access.py",
    '''    _require_one(cur.rowcount, f"Failed to confirm pending transaction id={trans_id}")
''',
    '''    if cur.rowcount == 1:
        return
    current = await get_transaction(db, trans_id=int(trans_id))
    if current is not None and int(current[schema.TRANS_STATUS]) == const.TRANS_STATUS_CONFIRMED:
        return
    _require_one(cur.rowcount, f"Failed to confirm pending transaction id={trans_id}")
''',
)
replace_once(
    "db_access.py",
    '''    _require_one(cur.rowcount, f"Failed to fail pending transaction id={trans_id}")
''',
    '''    if cur.rowcount == 1:
        return
    current = await get_transaction(db, trans_id=int(trans_id))
    if current is not None and int(current[schema.TRANS_STATUS]) == const.TRANS_STATUS_FAILED:
        return
    _require_one(cur.rowcount, f"Failed to fail pending transaction id={trans_id}")
''',
)

replace_once(
    "tests/test_financial_finality.py",
    '''        self.assertEqual(int(trans[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)


class StandardPayoutRecoveryTest(FinancialDatabaseFixture):
''',
    '''        self.assertEqual(int(trans[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)

    async def test_repeated_confirmation_is_idempotent(self):
        _owner_id, claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        async with schema.get_db() as db:
            trans_id = await db_access.create_claim_transaction(
                db,
                user_id=claimant_id,
                claim_id=claim_id,
                amount=50,
                from_address="from",
                to_address="to",
                tx_hash="hash-repeat",
            )
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=trans_id,
                block_number=7,
            )
            await db.commit()

            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=trans_id,
                block_number=8,
            )
            await db.commit()
            trans = await db_access.get_transaction(db, trans_id=trans_id)

        self.assertEqual(int(trans[schema.TRANS_STATUS]), const.TRANS_STATUS_CONFIRMED)
        self.assertEqual(int(trans[schema.TRANS_BLOCK_NUMBER]), 7)


class StandardPayoutRecoveryTest(FinancialDatabaseFixture):
''',
)

replace_once(
    "tests/test_financial_finality.py",
    '''        self.assertEqual(submit.await_args.kwargs["amount"], 5_000_000)


class CancellationLiabilityTest(unittest.IsolatedAsyncioTestCase):
''',
    '''        self.assertEqual(submit.await_args.kwargs["amount"], 5_000_000)

    async def test_chain_send_failure_is_not_mislabeled_as_concurrent(self):
        _owner_id, _claimant_id, _spot_id, claim_id = await self.create_claim_fixture()
        with mock.patch.object(
            settlement_updater.trans_updater,
            "submit_claim_reward_transaction",
            mock.AsyncMock(
                side_effect=RuntimeError(
                    "Chain send did not return a usable transaction hash; local intent 7 was left pending for safety"
                )
            ),
        ):
            result = await settlement_updater.payout_standard_claim_if_ready(
                claim_id=claim_id,
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["paid"])
        self.assertIn("local intent 7", result["reason"])


class CancellationLiabilityTest(unittest.IsolatedAsyncioTestCase):
''',
)
