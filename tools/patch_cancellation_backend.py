from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# CRITICAL: cancellation refunds/fees are automatic at-most-once operations.
replace_once(
    "db_access.py",
    dedent('''\
    async def create_spot_refund_transaction(
        db,
        *,
        user_id: int,
        spot_id: int,
        amount: int,
        from_address: str,
        to_address: str,
        tx_hash: str,
    ) -> int:
        return await _create_transaction(
            db,
            user_id=user_id,
            spot_id=spot_id,
            claim_id=None,
            trans_type=const.TRANS_TYPE_CANCEL_SPOT,
            amount=amount,
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
        )
    '''),
    dedent('''\
    async def has_any_spot_cancellation_leg(
        db,
        *,
        spot_id: int,
        trans_type: int,
    ) -> bool:
        """Return True after any refund/fee attempt exists, regardless of status.

        Outgoing cancellation money must be at-most-once automatically. A row
        marked FAILED can still represent an ambiguous broadcast, so it remains
        a permanent automatic-retry barrier until an operator reconciles it.
        """
        trans_type = int(trans_type)
        if trans_type not in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}:
            raise ValueError("trans_type is not a cancellation leg")
        cur = await db.execute(
            f"""
            SELECT 1
            FROM {schema.TRANS_TABLE_NAME}
            WHERE {schema.TRANS_SPOT_ID} = ?
              AND {schema.TRANS_TYPE} = ?
            LIMIT 1;
            """,
            (int(spot_id), trans_type),
        )
        return await cur.fetchone() is not None


    async def create_spot_refund_transaction(
        db,
        *,
        user_id: int,
        spot_id: int,
        amount: int,
        from_address: str,
        to_address: str,
        tx_hash: str,
    ) -> int:
        if await has_any_spot_cancellation_leg(
            db,
            spot_id=int(spot_id),
            trans_type=const.TRANS_TYPE_CANCEL_SPOT,
        ):
            raise RuntimeError(
                f"Spot id={spot_id} already has a cancellation refund transaction attempt"
            )
        return await _create_transaction(
            db,
            user_id=user_id,
            spot_id=spot_id,
            claim_id=None,
            trans_type=const.TRANS_TYPE_CANCEL_SPOT,
            amount=amount,
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
        )
    '''),
)

replace_once(
    "db_access.py",
    dedent('''\
    async def create_platform_fee_transaction(
        db,
        *,
        user_id: int,
        amount: int,
        from_address: str,
        to_address: str,
        tx_hash: str,
        spot_id: int | None = None,
        claim_id: int | None = None,
    ) -> int:
        return await _create_transaction(
            db,
            user_id=user_id,
            spot_id=spot_id,
            claim_id=claim_id,
            trans_type=const.TRANS_TYPE_PLAT_FEE,
            amount=amount,
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
        )
    '''),
    dedent('''\
    async def create_platform_fee_transaction(
        db,
        *,
        user_id: int,
        amount: int,
        from_address: str,
        to_address: str,
        tx_hash: str,
        spot_id: int | None = None,
        claim_id: int | None = None,
    ) -> int:
        if spot_id is not None and claim_id is None and await has_any_spot_cancellation_leg(
            db,
            spot_id=int(spot_id),
            trans_type=const.TRANS_TYPE_PLAT_FEE,
        ):
            raise RuntimeError(
                f"Spot id={spot_id} already has a cancellation fee transaction attempt"
            )
        return await _create_transaction(
            db,
            user_id=user_id,
            spot_id=spot_id,
            claim_id=claim_id,
            trans_type=const.TRANS_TYPE_PLAT_FEE,
            amount=amount,
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
        )
    '''),
)

replace_once(
    "trans_updater.py",
    dedent('''\
            create_transaction_kwargs={
                "user_id": int(spot[schema.SPOT_CREATED_BY]),
                "spot_id": int(spot_id),
            },
        )
        return {**result, "spot_id": int(spot_id)}


    async def submit_spot_creation_fee_transaction(
    '''),
    dedent('''\
            create_transaction_kwargs={
                "user_id": int(spot[schema.SPOT_CREATED_BY]),
                "spot_id": int(spot_id),
            },
            serialize_intent=True,
        )
        return {**result, "spot_id": int(spot_id)}


    async def submit_spot_creation_fee_transaction(
    '''),
)

replace_once(
    "trans_updater.py",
    dedent('''\
            create_transaction_kwargs={
                "user_id": int(spot[schema.SPOT_CREATED_BY]),
                "spot_id": int(spot_id),
            },
        )
        return {**result, "spot_id": int(spot_id)}


    async def _published_standard_spot_is_complete(
    '''),
    dedent('''\
            create_transaction_kwargs={
                "user_id": int(spot[schema.SPOT_CREATED_BY]),
                "spot_id": int(spot_id),
            },
            serialize_intent=True,
        )
        return {**result, "spot_id": int(spot_id)}


    async def _published_standard_spot_is_complete(
    '''),
)

replace_once(
    "trans_updater.py",
    dedent('''\
        The cancellation marker is durable and immediately removes the Spot from
        public claiming. Existing deposit/fee/reward transactions are allowed to
        settle first; a background settlement pass retries this function until the
        refund and cancellation fee can be submitted without double-spending.
    '''),
    dedent('''\
        The cancellation marker is durable and immediately removes the Spot from
        public claiming. Existing deposit/fee/reward transactions are allowed to
        settle first. Refund and cancellation-fee legs are automatic at-most-once:
        any failed/ambiguous attempt pauses further sends for manual reconciliation.
    '''),
)

replace_once(
    "trans_updater.py",
    dedent('''\
            transactions = await db_access.get_transactions_by_spot(
                db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT
            )
            deposit_transactions = [
    '''),
    dedent('''\
            transactions = await db_access.get_transactions_by_spot(
                db, spot_id=int(spot_id), limit=db_access.MAX_LIMIT
            )
            failed_cancellation_legs = [
                trans
                for trans in transactions
                if int(trans.get(schema.TRANS_TYPE) or -1)
                in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
                and int(
                    trans.get(schema.TRANS_STATUS)
                    if trans.get(schema.TRANS_STATUS) is not None
                    else -1
                )
                == const.TRANS_STATUS_FAILED
            ]
            if failed_cancellation_legs:
                return await deferred_result(
                    reason="manual_reconciliation_required",
                    message=(
                        "Cancellation is paused because a prior refund or fee attempt "
                        "has an ambiguous failed status. No further automatic sends will occur."
                    ),
                )

            deposit_transactions = [
    '''),
)

print("Applied cancellation backend patch.")
