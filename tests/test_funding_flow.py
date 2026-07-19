import asyncio

import constants as const
import database as schema
import funding_fee_worker
import funding_status


def _transaction(*, trans_type, status, amount, tx_hash, from_address='NQ FROM', to_address='NQ TO'):
    return {
        schema.TRANS_TYPE: trans_type,
        schema.TRANS_STATUS: status,
        schema.TRANS_AMOUNT: amount,
        schema.TRANS_TX_HASH: tx_hash,
        schema.TRANS_FROM_ADDRESS: from_address,
        schema.TRANS_TO_ADDRESS: to_address,
        schema.TRANS_CREATED_AT: 1,
    }


def test_full_pending_deposit_is_processing_not_partial():
    required = 101 * const.LUNA_PER_NIM
    result = funding_status.deposit_summary(
        [
            _transaction(
                trans_type=const.TRANS_TYPE_FILL_SPOT,
                status=const.TRANS_STATUS_PENDING,
                amount=required,
                tx_hash='deposit-hash',
            )
        ],
        total_value=100 * const.LUNA_PER_NIM,
        creation_fee=const.LUNA_PER_NIM,
        deposit_address='NQ DEPOSIT',
        creation_fee_address='NQ FEE',
    )

    assert result['status'] == 'processing'
    assert result['status_label'] == 'Deposit Processing'
    assert result['funding_submitted'] is True
    assert result['funding_complete'] is False


def test_confirmed_full_funding_is_ready_before_internal_fee_submission():
    total_value = 100 * const.LUNA_PER_NIM
    creation_fee = const.LUNA_PER_NIM
    required = total_value + creation_fee
    result = funding_status.deposit_summary(
        [
            _transaction(
                trans_type=const.TRANS_TYPE_FILL_SPOT,
                status=const.TRANS_STATUS_CONFIRMED,
                amount=required,
                tx_hash='deposit-hash',
                to_address='NQ DEPOSIT',
            )
        ],
        total_value=total_value,
        creation_fee=creation_fee,
        deposit_address='NQ DEPOSIT',
        creation_fee_address='NQ FEE',
    )

    assert result['status'] == 'ready'
    assert result['fee_paid'] is True
    assert result['fee_submitted'] is False
    assert result['fee_status'] == 'preparing'


def test_broadcast_fee_unlocks_ready_before_confirmation():
    total_value = 100 * const.LUNA_PER_NIM
    creation_fee = const.LUNA_PER_NIM
    required = total_value + creation_fee
    deposit_address = 'NQ DEPOSIT'
    fee_address = 'NQ FEE'
    result = funding_status.deposit_summary(
        [
            _transaction(
                trans_type=const.TRANS_TYPE_FILL_SPOT,
                status=const.TRANS_STATUS_CONFIRMED,
                amount=required,
                tx_hash='deposit-hash',
                to_address=deposit_address,
            ),
            _transaction(
                trans_type=const.TRANS_TYPE_CREATION_FEE,
                status=const.TRANS_STATUS_PENDING,
                amount=creation_fee,
                tx_hash='fee-chain-hash',
                from_address=deposit_address,
                to_address=fee_address,
            ),
        ],
        total_value=total_value,
        creation_fee=creation_fee,
        deposit_address=deposit_address,
        creation_fee_address=fee_address,
    )

    assert result['status'] == 'ready'
    assert result['fee_paid'] is True
    assert result['fee_submitted'] is True
    assert result['fee_confirmed'] is False
    assert result['fee_status'] == 'pending'


def test_local_fee_intent_is_invisible_and_does_not_block_ready_status():
    total_value = 100 * const.LUNA_PER_NIM
    creation_fee = const.LUNA_PER_NIM
    required = total_value + creation_fee
    deposit_address = 'NQ DEPOSIT'
    fee_address = 'NQ FEE'
    result = funding_status.deposit_summary(
        [
            _transaction(
                trans_type=const.TRANS_TYPE_FILL_SPOT,
                status=const.TRANS_STATUS_CONFIRMED,
                amount=required,
                tx_hash='deposit-hash',
                to_address=deposit_address,
            ),
            _transaction(
                trans_type=const.TRANS_TYPE_CREATION_FEE,
                status=const.TRANS_STATUS_PENDING,
                amount=creation_fee,
                tx_hash='NIMHUNT_INTENT:creation_fee:1:abc',
                from_address=deposit_address,
                to_address=fee_address,
            ),
        ],
        total_value=total_value,
        creation_fee=creation_fee,
        deposit_address=deposit_address,
        creation_fee_address=fee_address,
    )

    assert result['status'] == 'ready'
    assert result['fee_paid'] is True
    assert result['fee_submitted'] is False
    assert result['requires_attention'] is True
    assert result['fee_status'] == 'attention_required'


def test_creation_fee_helper_failure_is_not_silently_treated_as_duplicate(monkeypatch):
    spot = {
        schema.SPOT_ID: 7,
        schema.SPOT_STATUS: const.SPOT_STATUS_DRAFT,
        schema.SPOT_CREATED_BY: 1,
        schema.SPOT_TITLE: 'Test Spot',
        schema.SPOT_CREATION_FEE: const.LUNA_PER_NIM,
        schema.SPOT_CREATION_FEE_ADDRESS: 'NQ FEE',
        schema.SPOT_DEPOSIT_ADDRESS: 'NQ DEPOSIT',
        schema.SPOT_TOTAL_VALUE: 100 * const.LUNA_PER_NIM,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
    }

    async def get_spot(_db, *, spot_id):
        assert spot_id == 7
        return spot

    async def confirmed_total(_db, *, spot_id):
        assert spot_id == 7
        return 101 * const.LUNA_PER_NIM

    async def no_existing_fee(_db, *, spot_id):
        assert spot_id == 7
        return False

    async def helper_failure(*args, **kwargs):
        raise RuntimeError(
            'Chain send did not return a usable transaction hash; '
            'local intent 42 was left pending for safety: helper timed out'
        )

    monkeypatch.setattr(funding_fee_worker.db_access, 'get_spot', get_spot)
    monkeypatch.setattr(
        funding_fee_worker.db_access,
        'get_confirmed_spot_deposit_total',
        confirmed_total,
    )
    monkeypatch.setattr(
        funding_fee_worker.db_access,
        'has_nonfailed_spot_creation_fee_transaction',
        no_existing_fee,
    )
    monkeypatch.setattr(
        funding_fee_worker.trans_updater,
        '_submit_recorded_chain_send',
        helper_failure,
    )

    async def run():
        try:
            await funding_fee_worker.submit_spot_creation_fee_transaction(
                object(),
                spot_id=7,
            )
        except RuntimeError as exc:
            assert 'helper timed out' in str(exc)
        else:
            raise AssertionError('helper failure was silently swallowed')

    asyncio.run(run())
