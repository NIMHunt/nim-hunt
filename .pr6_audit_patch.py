from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:180]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "db_access.py",
    '''    to_address: str | None = None,
    amount: int | None = None,
) -> None:
''',
    '''    to_address: str | None = None,
    amount: int | None = None,
    block_number: int | None = None,
) -> None:
''',
)

replace_once(
    "db_access.py",
    '''    if amount is not None:
        amount_i = int(amount)
        if amount_i < 0:
            raise ValueError("amount must be non-negative")
        updates.append(f"{schema.TRANS_AMOUNT} = ?")
        params.append(amount_i)

    if not updates:
''',
    '''    if amount is not None:
        amount_i = int(amount)
        if amount_i < 0:
            raise ValueError("amount must be non-negative")
        updates.append(f"{schema.TRANS_AMOUNT} = ?")
        params.append(amount_i)

    if block_number is not None:
        block_number_i = int(block_number)
        if block_number_i < 0:
            raise ValueError("block_number must be non-negative")
        updates.append(f"{schema.TRANS_BLOCK_NUMBER} = ?")
        params.append(block_number_i)

    if not updates:
''',
)

replace_once(
    "trans_updater.py",
    '''                to_address=verified_details.to_address,
                amount=verified_details.amount,
            )
''',
    '''                to_address=verified_details.to_address,
                amount=verified_details.amount,
                block_number=block_number,
            )
''',
)

replace_once(
    "tests/test_funding_wallet_and_find_spots.py",
    '''            self.assertEqual(wrong_after[schema.TRANS_FROM_ADDRESS], "wallet-b")
            self.assertEqual(
''',
    '''            self.assertEqual(wrong_after[schema.TRANS_FROM_ADDRESS], "wallet-b")
            self.assertEqual(int(wrong_after[schema.TRANS_BLOCK_NUMBER]), 2)
            self.assertEqual(
''',
)
