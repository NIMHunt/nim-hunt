from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:100]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "trans_updater.py",
    'f"SELECT * FROM {schema.TRANS_TABLE_NAME} WHERE {schema.TRANS_TX_HASH} = ? LIMIT 1;",',
    'f"SELECT * FROM {schema.TRANS_TABLE_NAME} WHERE LOWER({schema.TRANS_TX_HASH}) = ? LIMIT 1;",',
)

replace_once(
    "trans_updater.py",
    '''    tx_hash = str(result.tx_hash or "").strip().lower()
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        raise RuntimeError("Nimiq helper returned an invalid transaction hash")

    expected_from = _validate_nimiq_address(expected_from_address, field_name="expected from_address")
''',
    '''    tx_hash = str(result.tx_hash or "").strip().lower()

    expected_from = _validate_nimiq_address(expected_from_address, field_name="expected from_address")
''',
)
replace_once(
    "trans_updater.py",
    '''    if actual_amount != expected_amount:
        raise RuntimeError("Nimiq helper returned an amount that does not match the intended payment")

    return SubmittedChainTransaction(
''',
    '''    if actual_amount != expected_amount:
        raise RuntimeError("Nimiq helper returned an amount that does not match the intended payment")
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        raise RuntimeError("Nimiq helper returned an invalid transaction hash")

    return SubmittedChainTransaction(
''',
)

replace_once(
    "trans_updater.py",
    '''    clean_hash = str(tx_hash or "").strip().lower()
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash):
        raise ValueError("tx_hash must be a 64-character hexadecimal Nimiq transaction hash")

    existing = await _transaction_by_hash(db, tx_hash=clean_hash)
    if existing is not None:
        if not _same_recorded_deposit(existing, user_id=user_id, spot_id=spot_id):
            raise ValueError("this transaction hash is already attached to a different record")
        return {
            "ok": True,
            "already_recorded": True,
            "trans_id": int(existing[schema.TRANS_ID]),
            "spot_id": int(spot_id),
            "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
        }

    amount = int(amount)
''',
    '''    clean_hash = str(tx_hash or "").strip().lower()
    hash_is_valid = bool(_NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash))
    if hash_is_valid:
        existing = await _transaction_by_hash(db, tx_hash=clean_hash)
        if existing is not None:
            if not _same_recorded_deposit(existing, user_id=user_id, spot_id=spot_id):
                raise ValueError("this transaction hash is already attached to a different record")
            return {
                "ok": True,
                "already_recorded": True,
                "trans_id": int(existing[schema.TRANS_ID]),
                "spot_id": int(spot_id),
                "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
            }

    amount = int(amount)
''',
)

replace_once(
    "trans_updater.py",
    '''    totals = await db_access.get_spot_deposit_totals(db, spot_id=int(spot_id))
    if int(totals.get("pending_amount") or 0) > 0:
        raise ValueError("this draft already has a pending deposit")
    required = int(db_access.spot_required_deposit_amount(spot))
    amount_due = max(0, required - int(totals.get("confirmed_amount") or 0))
    if amount_due <= 0:
        raise ValueError("this draft is already fully funded")
    amount = min(amount, amount_due)

    funding_address = await db_access.get_confirmed_spot_funding_address(
''',
    '''    funding_address = await db_access.get_confirmed_spot_funding_address(
''',
)

replace_once(
    "trans_updater.py",
    '''        if established_sender is None or submitted_sender != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    try:
''',
    '''        if established_sender is None or submitted_sender != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    totals = await db_access.get_spot_deposit_totals(db, spot_id=int(spot_id))
    if int(totals.get("pending_amount") or 0) > 0:
        raise ValueError("this draft already has a pending deposit")
    required = int(db_access.spot_required_deposit_amount(spot))
    amount_due = max(0, required - int(totals.get("confirmed_amount") or 0))
    if amount_due <= 0:
        raise ValueError("this draft is already fully funded")
    amount = min(amount, amount_due)

    if not hash_is_valid:
        raise ValueError("tx_hash must be a 64-character hexadecimal Nimiq transaction hash")

    try:
''',
)

replace_once(
    "public_html.py",
    '''            deposit_record = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=user_id,
                spot_id=spot_id,
                amount=submitted_amount,
                from_address=payload.from_address,
                tx_hash=payload.tx_hash,
                to_address=spot.get(schema.SPOT_DEPOSIT_ADDRESS),
            )
''',
    '''            try:
                deposit_record = await trans_updater.record_spot_deposit_transaction(
                    db,
                    user_id=user_id,
                    spot_id=spot_id,
                    amount=submitted_amount,
                    from_address=payload.from_address,
                    tx_hash=payload.tx_hash,
                    to_address=spot.get(schema.SPOT_DEPOSIT_ADDRESS),
                )
            except ValueError as exc:
                return JSONResponse(
                    {
                        **meta,
                        "ok": False,
                        "code": "deposit_rejected",
                        "message": str(exc),
                    },
                    status_code=status.HTTP_409_CONFLICT,
                )
''',
)

replace_once(
    "tests/test_blockchain_flow_integration.py",
    '''        async with schema.get_db() as db:
            repeated = await trans_updater.record_spot_deposit_transaction(
''',
    '''        async with schema.get_db() as db:
            # Older rows may preserve the provider's uppercase formatting. Hash
            # identity is hexadecimal and therefore case-insensitive.
            await db.execute(
                f"UPDATE {schema.TRANS_TABLE_NAME} SET {schema.TRANS_TX_HASH} = UPPER({schema.TRANS_TX_HASH}) WHERE {schema.TRANS_ID} = ?;",
                (first["trans_id"],),
            )
            await db.commit()
            repeated = await trans_updater.record_spot_deposit_transaction(
''',
)

replace_once(
    "tests/test_spot_creation_fees.py",
    'tx_hash="creation-fee-chain-hash",',
    'tx_hash="44" * 32,',
)
replace_once(
    "tests/test_spot_creation_fees.py",
    'tx_hash="partial-api-deposit",',
    'tx_hash="55" * 32,',
)

print("Final blockchain cleanup applied.")
