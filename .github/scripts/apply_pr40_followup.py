from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_spot_creation_fees.py",
    '''    async def test_publish_waits_for_fee_confirmation(self):
        fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee)
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        async with schema.get_db() as db:
            self.assertFalse(await db_access.can_publish_spot(db, spot_id=spot_id))

        fee_trans_id = await self.create_creation_fee_transaction(spot_id)
        async with schema.get_db() as db:
            self.assertFalse(await db_access.can_publish_spot(db, spot_id=spot_id))
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=fee_trans_id,
                block_number=999,
            )
            await db.commit()
            self.assertTrue(await db_access.can_publish_spot(db, spot_id=spot_id))
''',
    '''    async def test_publish_does_not_wait_for_internal_fee_confirmation(self):
        fee = const.LUNA_PER_NIM
        spot_id = await self.create_standard_spot(fee=fee)
        spot = await self.get_spot(spot_id)
        await self.create_deposit(spot_id, db_access.spot_required_deposit_amount(spot))

        # The creator has already deposited both the Spot value and the
        # snapshotted creation fee. Publishing must not wait for NimHunt's
        # separate internal transfer to the shared fee address.
        async with schema.get_db() as db:
            self.assertTrue(await db_access.can_publish_spot(db, spot_id=spot_id))

        fee_trans_id = await self.create_creation_fee_transaction(spot_id)
        async with schema.get_db() as db:
            self.assertTrue(await db_access.can_publish_spot(db, spot_id=spot_id))
            await db_access.set_transaction_status_to_confirmed(
                db,
                trans_id=fee_trans_id,
                block_number=999,
            )
            await db.commit()
            self.assertTrue(await db_access.can_publish_spot(db, spot_id=spot_id))
''',
)

replace_once(
    "tests/test_spot_creation_fees.py",
    '''    def test_deposit_summary_reports_processing_until_fee_confirms(self):
''',
    '''    def test_deposit_summary_is_ready_while_fee_reconciles(self):
''',
)
replace_once(
    "tests/test_spot_creation_fees.py",
    '''        self.assertEqual(summary["status"], "processing")
        self.assertEqual(summary["status_label"], "Creation Fee Processing")
        self.assertTrue(summary["funding_complete"])
''',
    '''        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["status_label"], "Ready")
        self.assertTrue(summary["funding_complete"])
''',
)
replace_once(
    "tests/test_spot_creation_fees.py",
    '''    def test_confirmed_fee_to_wrong_address_does_not_unlock_publishing_ui(self):
''',
    '''    def test_wrong_internal_fee_destination_is_reported_without_blocking_publish(self):
''',
)
replace_once(
    "tests/test_spot_creation_fees.py",
    '''        self.assertEqual(summary["fee_status"], "verification_mismatch")
        self.assertEqual(summary["status"], "processing")
''',
    '''        self.assertEqual(summary["fee_status"], "verification_mismatch")
        # The combined creator deposit is still complete. The mismatched internal
        # fee transfer remains visible for operational repair without falsely
        # telling the creator that their deposit is incomplete.
        self.assertEqual(summary["status"], "ready")
''',
)

replace_once(
    "static/claim_detail.js",
    '''    if (status === 'pending') {
        if (Number(claim.duration_required || 0) > 0 && !durationGoalReached(claim)) return false;
        return true;
    }
''',
    '''    if (status === 'pending') {
        const durationRequired = Number(claim.duration_required || 0);
        const serverRemaining = Number(claim.duration_remaining || 0);
        // Trust the server when it has already reached the verification phase.
        // A phone clock that is a few seconds slow must not stop status polling.
        if (durationRequired > 0 && serverRemaining > 0 && !durationGoalReached(claim)) return false;
        return true;
    }
''',
)

replace_once(
    "public_html.py",
    '''        "payout_pending_count": int(payout["payout_pending_count"]),
        "payout_confirmed_count": int(payout["payout_confirmed_count"]),
        "payout_amount": int(payout["payout_amount"]),
''',
    '''        "payout_pending_count": int(payout["payout_pending_count"]),
        "payout_confirmed_count": int(payout["payout_confirmed_count"]),
        "payout_failed_count": int(payout["payout_failed_count"]),
        "payout_amount": int(payout["payout_amount"]),
''',
)

# The diagnostic workflow was only needed to capture the initial pytest output.
for relative in (
    ".github/workflows/diagnose-pr40-python-tests.yml",
    ".github/scripts/apply_pr40_followup.py",
    ".github/workflows/apply-pr40-followup.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
