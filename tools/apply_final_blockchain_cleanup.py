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

print("Final blockchain cleanup applied.")
